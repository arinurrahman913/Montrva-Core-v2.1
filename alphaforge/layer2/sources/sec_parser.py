"""Parse SEC EDGAR XBRL company facts untuk extract quarterly fundamental data.

SEC EDGAR mewajibkan header User-Agent yang mengidentifikasi pemanggil
(https://www.sec.gov/os/webmaster-faq#developers) — tanpa header ini semua
request di-403. Ini penyebab utama endpoint "blocked" sebelumnya, bukan
rate limit sungguhan.

Dua endpoint dipakai:
- https://www.sec.gov/files/company_tickers.json — lookup ticker -> CIK
- https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json — semua fakta XBRL
  (us-gaap tags) hasil laporan 10-K/10-Q, granular per periode.

Lihat: 03_LAYER2_SPECS/02_EVIDENCE.md §1.5.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone

import requests

from ... import cache
from ._retry import retry

SEC_USER_AGENT = "AlphaForge Research research@alphaforge.local"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_TICKER_MAP_TTL = 7 * 24 * 3600  # 7 hari — mapping ticker->CIK nyaris tidak berubah
_FACTS_TTL = 24 * 3600  # 24 jam, selaras kebijakan fundamental cache lain

# SEC.gov limit resmi 10 req/detik — sebelumnya sec_edgar.py & sec_parser.py
# sama sekali tidak ada throttle (beda dari Yahoo/Finnhub yang sudah).
# Interval minimal ~150ms antar panggilan (~6.7/detik) kasih buffer aman,
# dipakai bersama oleh sec_parser.py & sec_edgar.py (lihat apply_sec_rate_limit).
SEC_MIN_INTERVAL_SECONDS = float(os.environ.get("SEC_MIN_INTERVAL_SECONDS", "0.15"))
SEC_RETRIES = 2
SEC_RETRY_BACKOFF_SECONDS = 3.0

_last_call_time = None
# Sama seperti finnhub.py: Evidence sekarang fetch multi-ticker concurrent
# (EVIDENCE_WORKERS di evidence.py), jadi check-then-sleep di bawah butuh
# lock supaya atomic — tanpa ini, beberapa thread bisa lolos cek bersamaan
# dan nembus limit 10 req/detik SEC.gov secara gabungan.
_lock = threading.Lock()


def reset_sec_rate_limit():
    """Reset rate-limit tracking (dipanggil di awal evidence run)."""
    global _last_call_time
    with _lock:
        _last_call_time = None


def apply_sec_rate_limit():
    """Jeda minimal antar SETIAP panggilan ke data.sec.gov / www.sec.gov —
    dipakai sec_parser.py & sec_edgar.py, dua-duanya sama-sama hit domain
    yang sama jadi harus berbagi satu tracker, bukan masing-masing punya
    timer sendiri (kalau tidak, throughput gabungan bisa 2x lipat dari yang
    dikira)."""
    global _last_call_time
    with _lock:
        now = time.time()
        if _last_call_time is not None:
            elapsed = now - _last_call_time
            if elapsed < SEC_MIN_INTERVAL_SECONDS:
                time.sleep(SEC_MIN_INTERVAL_SECONDS - elapsed)
        _last_call_time = time.time()

# us-gaap tags, urutan = prioritas fallback (perusahaan beda-beda pakai tag berbeda).
REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
GROSS_PROFIT_TAGS = ["GrossProfit"]
OPERATING_INCOME_TAGS = ["OperatingIncomeLoss"]
NET_INCOME_TAGS = ["NetIncomeLoss", "ProfitLoss"]
CASH_OPS_TAGS = ["NetCashProvidedByUsedInOperatingActivities"]
CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment"]
# Instant (point-in-time balance sheet date), bukan duration seperti tag di atas
# — dipakai untuk baseline dilution 12-bulan (lihat fetch_shares_outstanding_change_12m).
SHARES_OUTSTANDING_TAGS = ["CommonStockSharesOutstanding", "CommonStockSharesIssued"]

_HEADERS = {"User-Agent": SEC_USER_AGENT}


# Memo in-process untuk peta ticker->CIK. Tanpa ini, tiap panggilan membaca +
# meng-parse ulang file cache berisi ribuan entri dari disk; get_cik_from_ticker
# dipanggil 4x per ticker (sec_filings, form4, quarterly, shares_outstanding),
# jadi ~16.000 kali per run full-market. json.loads menahan GIL, sehingga di
# mode 5-thread ini ikut menyerialkan kerja CPU antar thread.
_cik_map_memo: dict[str, str] | None = None
_cik_map_lock = threading.Lock()


def _get_ticker_cik_map() -> dict[str, str]:
    """Fetch (atau baca dari cache/memo) mapping TICKER -> CIK 10-digit zero-padded."""
    global _cik_map_memo
    if _cik_map_memo is not None:
        return _cik_map_memo

    cached = cache.get("sec_edgar", "ticker_cik_map", _TICKER_MAP_TTL)
    if cached is not None:
        with _cik_map_lock:
            _cik_map_memo = cached
        return cached

    try:
        apply_sec_rate_limit()

        def _do_fetch():
            r = requests.get(TICKERS_URL, headers=_HEADERS, timeout=10)
            r.raise_for_status()
            return r.json()

        raw = retry(_do_fetch, retries=SEC_RETRIES, backoff_seconds=SEC_RETRY_BACKOFF_SECONDS,
                    label="sec_ticker_map")
    except Exception as exc:
        print(f"[sec_ticker_map] gagal (final): {exc}", file=sys.stderr)
        return {}

    mapping = {
        entry["ticker"].upper(): str(entry["cik_str"]).zfill(10)
        for entry in raw.values()
        if entry.get("ticker")
    }
    cache.set("sec_edgar", "ticker_cik_map", mapping)
    with _cik_map_lock:
        _cik_map_memo = mapping
    return mapping


def get_cik_from_ticker(ticker: str) -> str | None:
    """Lookup CIK dari ticker via SEC company_tickers.json (cached 7 hari)."""
    mapping = _get_ticker_cik_map()
    return mapping.get(ticker.upper())


def _fetch_company_facts(cik: str) -> dict | None:
    cache_key = f"facts_{cik}"
    cached = cache.get("sec_edgar", cache_key, _FACTS_TTL)
    if cached is not None:
        return cached

    apply_sec_rate_limit()

    def _do_fetch():
        r = requests.get(FACTS_URL.format(cik=cik), headers=_HEADERS, timeout=15)
        if r.status_code == 404:
            return None  # Perusahaan tanpa XBRL facts (mis. foreign private issuer) — bukan error, jangan diretry
        r.raise_for_status()
        return r.json()

    try:
        data = retry(_do_fetch, retries=SEC_RETRIES, backoff_seconds=SEC_RETRY_BACKOFF_SECONDS,
                     label=f"sec_facts:{cik}")
    except Exception as exc:
        print(f"[sec_facts:{cik}] gagal (final): {exc}", file=sys.stderr)
        return None

    if data is None:
        return None  # 404 — hasil valid, bukan kegagalan
    cache.set("sec_edgar", cache_key, data)
    return data


def _extract_quarterly_series(facts: dict, tags: list[str]) -> dict[str, float]:
    """Ekstrak {fiscal_date_end: value} untuk satu metrik dari daftar tag fallback.

    Filter hanya datapoint berdurasi ~1 kuartal (75-100 hari) dari form
    10-Q/10-K, supaya tidak tercampur dengan angka YTD/kumulatif atau
    tahunan penuh yang juga ada di XBRL companyfacts.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        units = node.get("units", {}).get("USD", [])
        series: dict[str, float] = {}
        filed_at: dict[str, str] = {}
        for point in units:
            if point.get("form") not in ("10-Q", "10-Q/A", "10-K", "10-K/A"):
                continue
            start, end = point.get("start"), point.get("end")
            if not start or not end:
                continue
            try:
                d0 = datetime.fromisoformat(start)
                d1 = datetime.fromisoformat(end)
            except ValueError:
                continue
            duration_days = (d1 - d0).days
            if not (75 <= duration_days <= 100):
                continue  # buang YTD/kumulatif/tahunan
            filed = point.get("filed", "")
            if end in filed_at and filed <= filed_at[end]:
                continue  # sudah ada revisi lebih baru untuk periode ini
            series[end] = point.get("val")
            filed_at[end] = filed
        if series:
            return series
    return {}


def _extract_instant_series(facts: dict, tags: list[str]) -> dict[str, float]:
    """Ekstrak {as_of_date: value} untuk fakta INSTANT (snapshot titik-waktu,
    mis. shares outstanding per tanggal neraca) — beda dari
    _extract_quarterly_series yang untuk fakta DURATION (revenue per periode).
    Instant facts di XBRL cuma punya 'end' (tanggal snapshot), tidak ada
    'start', jadi tidak ada filter durasi 75-100 hari di sini."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for tag in tags:
        node = gaap.get(tag)
        if not node:
            continue
        units = node.get("units", {}).get("shares", [])
        series: dict[str, float] = {}
        filed_at: dict[str, str] = {}
        for point in units:
            if point.get("form") not in ("10-Q", "10-Q/A", "10-K", "10-K/A"):
                continue
            end = point.get("end")
            if not end:
                continue
            filed = point.get("filed", "")
            if end in filed_at and filed <= filed_at[end]:
                continue  # sudah ada revisi lebih baru untuk tanggal ini
            series[end] = point.get("val")
            filed_at[end] = filed
        if series:
            return series
    return {}


def fetch_shares_outstanding_change_12m(ticker: str) -> float | None:
    """% perubahan shares outstanding dalam ~12 bulan terakhir — baseline untuk
    deteksi dilution (Risk _check_dilution). Positive = lebih banyak saham
    beredar sekarang dibanding ~setahun lalu (dilutive).

    Pakai company facts XBRL yang SAMA dengan fetch_quarterly_financials
    (di-cache 24 jam) — kalau quarterly_financials sudah dipanggil duluan
    untuk ticker yang sama dalam window cache itu, ini praktis gratis
    (cache hit), bukan network call kedua.
    """
    cik = get_cik_from_ticker(ticker)
    if not cik:
        return None

    facts = _fetch_company_facts(cik)
    if not facts:
        return None

    series = _extract_instant_series(facts, SHARES_OUTSTANDING_TAGS)
    if len(series) < 2:
        return None

    dates_sorted = sorted(series.keys(), reverse=True)
    latest_date = dates_sorted[0]
    latest_val = series[latest_date]
    if not latest_val:
        return None

    latest_dt = datetime.fromisoformat(latest_date)

    # Cari snapshot paling dekat ke 365 hari sebelum snapshot terbaru (toleransi
    # 300-430 hari — laporan kuartalan tidak selalu tepat 1 tahun kalender apart).
    best_date = None
    best_diff = None
    for d in dates_sorted[1:]:
        dt = datetime.fromisoformat(d)
        days_ago = (latest_dt - dt).days
        if not (300 <= days_ago <= 430):
            continue
        diff = abs(days_ago - 365)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_date = d

    if best_date is None:
        return None

    baseline_val = series[best_date]
    if not baseline_val:
        return None

    return ((latest_val - baseline_val) / baseline_val) * 100


def fetch_quarterly_financials(ticker: str, max_periods: int = 8) -> dict | None:
    """Fetch quarterly financial data dari SEC EDGAR XBRL company facts.

    Returns dict dengan 'periods' (list terurut dari terbaru), atau None kalau
    ticker tidak ditemukan / tidak punya data XBRL (mis. baru IPO, foreign issuer).
    """
    cik = get_cik_from_ticker(ticker)
    if not cik:
        return None

    facts = _fetch_company_facts(cik)
    if not facts:
        return None

    revenue = _extract_quarterly_series(facts, REVENUE_TAGS)
    if not revenue:
        return None  # Tanpa revenue, quarterly trend tidak berguna

    gross_profit = _extract_quarterly_series(facts, GROSS_PROFIT_TAGS)
    operating_income = _extract_quarterly_series(facts, OPERATING_INCOME_TAGS)
    net_income = _extract_quarterly_series(facts, NET_INCOME_TAGS)
    cash_ops = _extract_quarterly_series(facts, CASH_OPS_TAGS)
    capex = _extract_quarterly_series(facts, CAPEX_TAGS)

    end_dates = sorted(revenue.keys(), reverse=True)[:max_periods]

    periods = []
    for end_date in end_dates:
        periods.append({
            "period": end_date,
            "revenue": revenue.get(end_date),
            "gross_profit": gross_profit.get(end_date),
            "operating_income": operating_income.get(end_date),
            "net_income": net_income.get(end_date),
            "cash_from_operations": cash_ops.get(end_date),
            "capital_expenditures": capex.get(end_date),
            "fiscal_date": end_date,
        })

    return {
        "periods": periods,
        "source": "sec_edgar",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if len(periods) >= 4 else "limited",
    }


def extract_quarterly_metrics(quarterly_data: dict | None) -> dict:
    """Extract key metrics (YoY growth, margins) dari quarterly financial data.

    Returns dict flat: revenue_yoy_q1..q4, gross_margin_q1..q4,
    operating_margin_q1..q4, net_margin_q1..q4, capex_pct_revenue_q1..q4.
    q4 = kuartal paling baru, q1 = 3 kuartal sebelumnya.
    """
    if not quarterly_data or not quarterly_data.get("periods"):
        return {}

    periods = quarterly_data["periods"]
    if len(periods) < 4:
        return {}

    metrics = {}

    for i in range(4):
        period = periods[i]
        q_key = f"q{4 - i}"

        if len(periods) > i + 4:
            prior_year = periods[i + 4]
            if period.get("revenue") and prior_year.get("revenue"):
                yoy = ((period["revenue"] - prior_year["revenue"]) / prior_year["revenue"]) * 100
                metrics[f"revenue_yoy_{q_key}"] = yoy

        if period.get("gross_profit") and period.get("revenue"):
            metrics[f"gross_margin_{q_key}"] = (period["gross_profit"] / period["revenue"]) * 100
        if period.get("operating_income") and period.get("revenue"):
            metrics[f"operating_margin_{q_key}"] = (period["operating_income"] / period["revenue"]) * 100
        if period.get("net_income") and period.get("revenue"):
            metrics[f"net_margin_{q_key}"] = (period["net_income"] / period["revenue"]) * 100
        if period.get("capital_expenditures") and period.get("revenue"):
            metrics[f"capex_pct_revenue_{q_key}"] = (period["capital_expenditures"] / period["revenue"]) * 100

    return metrics
