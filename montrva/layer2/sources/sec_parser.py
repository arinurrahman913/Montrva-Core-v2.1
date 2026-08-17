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

SEC_USER_AGENT = "Montrva Research research@montrva.local"
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
# Tier KEDUA, dicoba HANYA kalau REVENUE_TAGS di atas kosong sama sekali.
# Sengaja bukan tambahan di daftar yang sama: aturan pemilihan "kuartal
# terbaru menang" tidak melihat arti tag, jadi menggabungkannya akan membuat
# emiten non-keuangan yang kebetulan melaporkan pendapatan bunga sekuartal
# lebih baru memilih pendapatan bunga sebagai "revenue"-nya.
#
# Bank tidak memakai `Revenues` sama sekali. Diukur pada 177 ticker sampel
# yang tag revenue standarnya kosong: 4 punya RevenuesNetOfInterestExpense,
# 21 punya pasangan InterestIncomeExpenseNet + NoninterestIncome dengan >= 4
# kuartal beririsan. Totalnya menutup ~16% dari kelompok itu.
REVENUE_TAGS_FINANCIAL = ["RevenuesNetOfInterestExpense"]
NET_INTEREST_INCOME_TAG = "InterestIncomeExpenseNet"
NONINTEREST_INCOME_TAG = "NoninterestIncome"

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

# Audit 2026-07-30 item B1: memo di atas cuma menutup jalur SUKSES -- kalau
# fetch pertama gagal (mis. sec.gov membalas 503), _cik_map_memo tetap None
# selamanya, jadi SETIAP panggilan get_cik_from_ticker berikutnya (4x/ticker
# x 4065 ticker = ~16.260 panggilan) mengulang seluruh siklus rate-limit +
# retry + backoff dari nol -- minimal ~13,5 jam tambahan, run tampak
# menggantung bukan gagal. Negative-cache dengan cooldown singkat: gagal
# sekali -> jangan coba lagi selama _CIK_MAP_RETRY_COOLDOWN_SECONDS (supaya
# gangguan BENERAN sesaat masih bisa pulih di tengah run panjang), tapi tidak
# menghantam jaringan di setiap satu dari ribuan panggilan berikutnya.
_CIK_MAP_RETRY_COOLDOWN_SECONDS = 60.0
_cik_map_failed_at: float | None = None


def _get_ticker_cik_map() -> dict[str, str]:
    """Fetch (atau baca dari cache/memo) mapping TICKER -> CIK 10-digit zero-padded."""
    global _cik_map_memo, _cik_map_failed_at
    with _cik_map_lock:
        if _cik_map_memo is not None:
            return _cik_map_memo
        if _cik_map_failed_at is not None:
            if time.time() - _cik_map_failed_at < _CIK_MAP_RETRY_COOLDOWN_SECONDS:
                return {}

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
        with _cik_map_lock:
            _cik_map_failed_at = time.time()
        return {}

    mapping = {
        entry["ticker"].upper(): str(entry["cik_str"]).zfill(10)
        for entry in raw.values()
        if entry.get("ticker")
    }
    cache.set("sec_edgar", "ticker_cik_map", mapping)
    with _cik_map_lock:
        _cik_map_memo = mapping
        _cik_map_failed_at = None
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


def _series_for_tag(gaap: dict, tag: str) -> dict[str, float]:
    """{fiscal_date_end: value} untuk SATU tag us-gaap.

    Filter hanya datapoint berdurasi ~1 kuartal (75-100 hari) dari form
    10-Q/10-K, supaya tidak tercampur dengan angka YTD/kumulatif atau
    tahunan penuh yang juga ada di XBRL companyfacts.
    """
    node = gaap.get(tag)
    if not node:
        return {}
    series: dict[str, float] = {}
    filed_at: dict[str, str] = {}
    for point in node.get("units", {}).get("USD", []):
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
    return series


def _extract_quarterly_series(facts: dict, tags: list[str]) -> tuple[dict[str, float], str | None]:
    """Pilih SATU tag dari daftar fallback, lalu kembalikan (series, nama_tag).

    Dulu fungsi ini mengembalikan tag PERTAMA yang berisi apa pun, tanpa
    melihat tanggalnya. Itu salah karena perusahaan berganti tag saat standar
    pelaporan berubah dan tag lamanya tetap tertinggal di companyfacts:

        NVDA  RevenueFromContractWithCustomer...  12 titik, 2017-04 s/d 2020-01
              Revenues                            64 titik, 2008-07 s/d 2026-04

    Yang dipakai adalah baris pertama — jadi tren revenue NVDA membeku di
    Januari 2020 sementara data 2026 tersedia dua tag di bawahnya. Terukur di
    artefak produksi `session-20260815T131949`: 304 dari 4.273 ticker punya
    kuartal terbaru <=2024 (ada yang 2011), dan 37 dari 60 sampel acak
    kelompok itu (62%) sebenarnya punya data 2026 di tag lain — termasuk HCA,
    GLW, AMP, TER, UPST.

    Kriteria pilih: kuartal TERBARU paling akhir menang; kalau seri berakhir
    di tanggal yang sama, yang titiknya lebih banyak; kalau masih seri, urutan
    prioritas daftar.

    Kedalaman sengaja TIDAK didahulukan, walau deret panjang kelihatan lebih
    berguna (YoY butuh 8 kuartal berurutan). Sudah dicoba dan hasilnya lebih
    buruk: MS mundur ke 2015, MBOT & BNY ke 2016, GEOS & RITM masing-masing
    satu kuartal — deret panjang yang sudah berhenti bertahun-tahun lalu
    mengalahkan deret pendek yang mutakhir. Trennya jadi ada, tapi tren tahun
    2016 yang dipajang sebagai "kini", dan itu persis bug yang sedang
    diperbaiki di sini. YoY yang hilang jujur; YoY dari kuartal 2016 tidak.

    Tidak ada toleransi tanggal: dua tag milik emiten yang SAMA memakai
    kalender fiskal yang sama, jadi tanggal tutup yang berbeda memang berarti
    kuartalnya berbeda, bukan sekadar geser penanggalan.

    Sengaja TIDAK menggabungkan beberapa tag jadi satu deret: `Revenues` dan
    `RevenueFromContractWithCustomer...` tidak selalu mendefinisikan hal yang
    sama, dan YoY/margin membandingkan periode dengan periode — deret campuran
    akan membandingkan dua definisi berbeda dan kelihatan seperti
    pertumbuhan/penyusutan yang tidak pernah terjadi.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    candidates: list[tuple[int, str, dict[str, float]]] = []
    for priority, tag in enumerate(tags):
        series = _series_for_tag(gaap, tag)
        if series:
            candidates.append((priority, tag, series))
    if not candidates:
        return {}, None

    priority, tag, series = max(candidates, key=lambda c: (max(c[2]), len(c[2]), -c[0]))
    return series, tag


def _financial_revenue_series(facts: dict) -> tuple[dict[str, float], str | None]:
    """Padanan "revenue" untuk bank/lembaga keuangan, dua jalur berurutan.

    1. `RevenuesNetOfInterestExpense` — memang total pendapatan bank, dipakai
       apa adanya kalau ada.
    2. Kalau tidak, JUMLAHKAN pendapatan bunga bersih + pendapatan
       non-bunga per periode. Ini penjumlahan dua komponen dari SATU periode
       yang sama menjadi satu besaran yang terdefinisi (itu yang disebut
       "total revenue" untuk bank), bukan penyambungan dua definisi berbeda
       antar-waktu seperti yang sengaja dihindari di _extract_quarterly_series.
       Hanya periode yang punya KEDUA komponen yang dipakai — periode dengan
       satu komponen saja akan tampak seperti pendapatan yang anjlok.

    Bases-nya dikembalikan supaya bisa dibaca hilir: net margin bank yang
    dihitung atas pendapatan bunga bersih tidak sebanding dengan net margin
    perusahaan biasa, dan pembacanya harus bisa tahu itu.
    """
    series, tag = _extract_quarterly_series(facts, REVENUE_TAGS_FINANCIAL)
    if series:
        return series, "financial_net_revenue"

    gaap = facts.get("facts", {}).get("us-gaap", {})
    nii = _series_for_tag(gaap, NET_INTEREST_INCOME_TAG)
    noninterest = _series_for_tag(gaap, NONINTEREST_INCOME_TAG)
    shared = set(nii) & set(noninterest)
    if len(shared) < 4:
        return {}, None
    return {d: nii[d] + noninterest[d] for d in shared}, "financial_composite"


def _is_subset_measure(standard: dict[str, float], financial: dict[str, float]) -> bool:
    """True kalau deret `standard` tampak cuma mengukur SEBAGIAN dari deret
    `financial` — dibandingkan pada periode terbaru yang dimiliki keduanya.

    Dibandingkan per-periode, bukan lewat total atau rata-rata, supaya
    perbedaan panjang deret tidak ikut terhitung sebagai perbedaan besaran.
    Kalau tidak ada periode yang beririsan, jawabannya False: tanpa titik
    banding yang sah, biarkan tag standar yang menang.
    """
    shared = set(standard) & set(financial)
    if not shared:
        return False
    latest = max(shared)
    return financial[latest] > standard[latest]


def _extract_instant_series(facts: dict, tags: list[str]) -> dict[str, float]:
    """Ekstrak {as_of_date: value} untuk fakta INSTANT (snapshot titik-waktu,
    mis. shares outstanding per tanggal neraca) — beda dari
    _extract_quarterly_series yang untuk fakta DURATION (revenue per periode).
    Instant facts di XBRL cuma punya 'end' (tanggal snapshot), tidak ada
    'start', jadi tidak ada filter durasi 75-100 hari di sini.

    Pemilihan tag memakai aturan yang sama dengan _extract_quarterly_series
    (snapshot terbaru menang, bukan urutan daftar) — masalahnya identik:
    emiten bisa berhenti melaporkan CommonStockSharesOutstanding dan lanjut
    dengan CommonStockSharesIssued, dan yang lama tetap tertinggal di
    companyfacts. Deret tetap diambil dari SATU tag: baseline 12 bulan
    dibandingkan di dalam deret yang sama, jadi mencampur "outstanding" dengan
    "issued" akan menghasilkan selisih dilusi yang tidak pernah terjadi."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    best: tuple[str, int, int] | None = None
    best_series: dict[str, float] = {}
    for priority, tag in enumerate(tags):
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
        if not series:
            continue
        rank = (max(series), len(series), -priority)
        if best is None or rank > best:
            best, best_series = rank, series
    return best_series


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


def fetch_quarterly_financials(ticker: str, max_periods: int = 24) -> dict | None:
    """Fetch quarterly financial data dari SEC EDGAR XBRL company facts.

    Returns dict dengan 'periods' (list terurut dari terbaru), atau None kalau
    ticker tidak ditemukan / tidak punya data XBRL (mis. baru IPO, foreign issuer).

    `max_periods` dulu 8. Dinaikkan ke 24 (6 tahun) supaya CAGR 3 & 5 tahun
    bisa dihitung dari TTM — 3Y butuh 16 kuartal, 5Y butuh 24. Ini **tidak
    menambah satu pun panggilan jaringan**: payload companyfacts yang sudah
    diambil memang memuat seluruh riwayat (NVDA punya 64 titik revenue), yang
    berubah cuma berapa banyak yang kita simpan.
    """
    cik = get_cik_from_ticker(ticker)
    if not cik:
        return None

    facts = _fetch_company_facts(cik)
    if not facts:
        return None

    revenue, revenue_tag = _extract_quarterly_series(facts, REVENUE_TAGS)
    revenue_basis = "standard" if revenue else None

    # Tier keuangan dipakai kalau tag standar kosong, TAPI juga kalau tag
    # standar ternyata cuma mengukur SEBAGIAN pendapatan bank. Fulton
    # Financial (FULT) melaporkan pendapatan jasa/fee-nya di bawah
    # `RevenueFromContractWithCustomerIncludingAssessedTax` — 79,3 jt untuk
    # kuartal yang pendapatan sesungguhnya 363,6 jt (bunga bersih 284,2 jt +
    # non-bunga 79,3 jt). Tag standarnya "berhasil", jadi jalur ini tidak
    # pernah dicoba, dan net margin-nya keluar 129% (laba bersih 102,4 jt
    # dibagi pendapatan fee saja). Bukan bug baru — begitu sejak dulu, cuma
    # tidak kelihatan selama blok kuartalannya ikut terbuang.
    #
    # Pasangan InterestIncomeExpenseNet + NoninterestIncome praktis khas
    # lembaga keuangan, dan syarat "lebih besar dari angka tag standar pada
    # periode yang sama" memastikan penggantian hanya terjadi saat tag
    # standar memang cuma sepotong.
    financial, financial_basis = _financial_revenue_series(facts)
    if financial and (not revenue or _is_subset_measure(revenue, financial)):
        revenue, revenue_basis = financial, financial_basis
        revenue_tag = (
            REVENUE_TAGS_FINANCIAL[0] if revenue_basis == "financial_net_revenue"
            else f"{NET_INTEREST_INCOME_TAG}+{NONINTEREST_INCOME_TAG}"
        )

    gross_profit, _ = _extract_quarterly_series(facts, GROSS_PROFIT_TAGS)
    operating_income, _ = _extract_quarterly_series(facts, OPERATING_INCOME_TAGS)
    net_income, _ = _extract_quarterly_series(facts, NET_INCOME_TAGS)
    cash_ops, _ = _extract_quarterly_series(facts, CASH_OPS_TAGS)
    capex, _ = _extract_quarterly_series(facts, CAPEX_TAGS)

    # Tulang punggung periode diambil dari revenue kalau ada, kalau tidak dari
    # net income. Dulu `if not revenue: return None` membuang SELURUH blok
    # kuartalan begitu tag revenue tidak ketemu — padahal net income dan arus
    # kas operasinya ada. 1.302 dari 4.273 ticker nol kuartal karena ini, dan
    # 15 dari 60 sampel acak (25%) punya net income sampai 2026-06-30 yang
    # ikut terbuang; semua contohnya bank (BY, CCNE, RNST, FBNC, EBMT), yang
    # memang tidak memakai tag `Revenues` — laporan mereka berbasis
    # pendapatan bunga. Margin tetap None tanpa revenue (perhitungannya di
    # extract_quarterly_metrics sudah menjaga itu), tapi tren laba dan arus
    # kasnya sekarang terbaca alih-alih hilang.
    spine = revenue or net_income
    if not spine:
        return None

    end_dates = sorted(spine.keys(), reverse=True)[:max_periods]

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
        # Umur data DIPISAH dari kelengkapannya: `latest_fiscal_date` bikin
        # kuartal 2018 tidak bisa lagi menyamar jadi "kini" di dashboard, dan
        # `revenue_tag` menyebut deret mana yang akhirnya dipakai supaya
        # pilihan tag bisa diaudit tanpa mengulang parsing.
        "latest_fiscal_date": end_dates[0] if end_dates else None,
        "revenue_tag": revenue_tag,
        "revenue_available": bool(revenue),
        # "standard" | "financial_net_revenue" | "financial_composite" | None
        "revenue_basis": revenue_basis,
        "status": "ok" if len(periods) >= 4 and revenue else "limited",
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
