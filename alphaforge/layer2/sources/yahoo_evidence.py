"""Fetch fundamental data & institutional ownership dari Yahoo Finance.

Evidence stage: lengkap dari Yahoo fast_info, extended historical OHLCV,
banyak fundamental fields. Dengan caching untuk performance.

Batching & delay antar request mengikuti pola yang sama dengan
sources/finnhub.py — perlu untuk full-market run (5000+ ticker) supaya
tidak kena throttle/soft-block dari Yahoo karena panggilan serial tanpa jeda.

Lihat 03_LAYER2_SPECS/02_EVIDENCE.md §1.1-1.2.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
import yfinance as yf
from ...cache import get as cache_get, set as cache_set
from ..contracts import (
    SourceMetadata, PriceMarketData, PriceBar, FundamentalData, InstitutionalOwnership,
    InstitutionalHolder, CompanyProfile, AnalystEstimates, EpsSurprise, RevenueEstimatePeriod
)
from ._retry import retry

def _safe_float(value) -> float | None:
    """Yahoo's .info dict occasionally returns non-numeric junk for fields
    that are supposed to be numeric (seen live on a full-market run:
    trailingPE came back as the literal string "Infinity" for 6 real
    tickers — BILL/CAL/CPSH/CRON/TALK/ZSQR, all loss-making — crashing
    Risk's `val.pe_ratio_trailing > 100` downstream with a str/int
    TypeError). Coerce to float or None instead of trusting the API's type
    contract. inf/-inf/nan are explicitly rejected too, not just non-numeric
    strings — float("Infinity") succeeds and would silently produce a real
    Python inf, but 03_KNOWLEDGE.md §6 is explicit that a ratio which is
    mathematically meaningless (P/E for a loss-making company) must be
    null+missing, not an extreme number."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # f != f catches NaN
        return None
    return f


# Field yang None-nya kemungkinan besar "memang tidak berlaku" (perusahaan
# tidak bagi dividen) bukan gagal fetch — Yahoo `.info` balikin None untuk
# kedua kasus sekaligus, jadi ini heuristik nama-field, bukan kepastian dari
# sumber. Field lain yang None dianggap "unavailable" (gagal fetch/kosong
# tanpa pola jelas).
_FUNDAMENTAL_NOT_APPLICABLE_FIELDS = ("dividend_yield", "payout_ratio")
_FUNDAMENTAL_RATIO_FIELDS = (
    "revenue", "net_income", "eps", "pe_ratio", "debt_to_equity", "current_ratio",
    "quick_ratio", "roe", "roa", "operating_margin", "gross_margin", "free_cash_flow",
    "dividend_yield", "payout_ratio", "book_value_per_share", "asset_turnover",
    "inventory_turnover", "interest_coverage", "shares_outstanding_change_12m",
)


def _classify_fundamental_availability(data) -> dict:
    reasons = {}
    for f in _FUNDAMENTAL_RATIO_FIELDS:
        if getattr(data, f) is None:
            reasons[f] = "not_applicable" if f in _FUNDAMENTAL_NOT_APPLICABLE_FIELDS else "unavailable"
    return reasons


# Field ini secara sifat adalah konsensus/estimasi analis, bukan angka aktual
# — ditandai "estimated" kalau ADA nilainya. recommendation_key ditandai
# "unverified" terpisah karena dikenal sering stale/tidak konsisten di Yahoo.
_ANALYST_ESTIMATED_FIELDS = (
    "target_low", "target_high", "target_mean", "target_median",
    "recommendation_mean", "num_analyst_opinions",
)


def _classify_analyst_estimates(data) -> tuple[dict, dict]:
    availability = {}
    quality = {}
    for f in _ANALYST_ESTIMATED_FIELDS:
        v = getattr(data, f)
        if v is None:
            availability[f] = "unavailable"
        else:
            quality[f] = "estimated"
    if data.recommendation_key is None:
        availability["recommendation_key"] = "unavailable"
    else:
        quality["recommendation_key"] = "unverified"
    return availability, quality


PRICE_CACHE_TTL = 6 * 3600  # 6 jam
FUNDAMENTAL_CACHE_TTL = 24 * 3600  # 24 jam
OWNERSHIP_CACHE_TTL = 24 * 3600  # 24 jam
YAHOO_INFO_CACHE_TTL = 24 * 3600  # 24 jam — sama dengan 2 di atas, sengaja disamakan

YF_EVIDENCE_RETRIES = 2
YF_EVIDENCE_RETRY_BACKOFF_SECONDS = 3.0

COMPANY_PROFILE_CACHE_TTL = 24 * 3600  # 24 jam
ANALYST_ESTIMATES_CACHE_TTL = 24 * 3600  # 24 jam

YF_EVIDENCE_BATCH_SIZE = int(os.environ.get("YF_EVIDENCE_BATCH_SIZE", "20"))
YF_EVIDENCE_BATCH_DELAY_SECONDS = float(os.environ.get("YF_EVIDENCE_BATCH_DELAY_SECONDS", "2.0"))

_batch_counter = 0
_batch_last_time = None
# Sama seperti finnhub.py/sec_parser.py: Evidence sekarang fetch multi-ticker
# concurrent (EVIDENCE_WORKERS di evidence.py) — tanpa lock, _batch_counter
# ini sendiri jadi race condition (increment bukan atomic), dan beberapa
# thread bisa masuk blok sleep bersamaan/berantakan hitungannya.
_lock = threading.Lock()


def reset_batch_tracking():
    """Reset batch counter (dipanggil di awal evidence run)."""
    global _batch_counter, _batch_last_time
    with _lock:
        _batch_counter = 0
        _batch_last_time = None


def _apply_batch_delay():
    """Jeda tiap YF_EVIDENCE_BATCH_SIZE panggilan network — hanya dipanggil
    di jalur cache-miss supaya re-run yang kena cache tetap cepat."""
    global _batch_counter, _batch_last_time
    with _lock:
        _batch_counter += 1
        if _batch_counter >= YF_EVIDENCE_BATCH_SIZE:
            if _batch_last_time is None:
                _batch_last_time = time.time()
            elapsed = time.time() - _batch_last_time
            if elapsed < YF_EVIDENCE_BATCH_DELAY_SECONDS:
                time.sleep(YF_EVIDENCE_BATCH_DELAY_SECONDS - elapsed)
            _batch_counter = 0
            _batch_last_time = time.time()


def _fetch_yahoo_info(ticker: str) -> dict:
    """Fetch `t.info` SEKALI, di-cache & dipakai bareng oleh fetch_fundamental_data
    dan fetch_institutional_ownership — sebelumnya dua-duanya masing-masing
    bikin `yf.Ticker(ticker).info` sendiri-sendiri untuk field dari respons
    yang sama persis (2x network call untuk data yang identik). Sekarang
    siapa pun yang panggil duluan yang bayar network cost-nya; yang kedua
    otomatis kena cache "yahoo_info" (24h) tanpa perlu tahu soal itu."""
    cached = cache_get("yahoo_info", ticker, YAHOO_INFO_CACHE_TTL)
    if cached is not None:
        return cached

    _apply_batch_delay()

    def _do_fetch():
        return yf.Ticker(ticker).info

    info = retry(_do_fetch, retries=YF_EVIDENCE_RETRIES,
                 backoff_seconds=YF_EVIDENCE_RETRY_BACKOFF_SECONDS,
                 label=f"yahoo_info:{ticker}")
    cache_set("yahoo_info", ticker, info)
    return info


# Jumlah bar harian terakhir yang dipertahankan utuh saat dipersist (~1 tahun bursa).
PRICE_HISTORY_DAILY_BARS = 252


def _downsample_price_history(bars: list[PriceBar]) -> list[PriceBar]:
    """Ringkas price_history untuk dipersist: bar harian utuh ~1 tahun terakhir,
    sisanya (tahun ke-2 s/d ke-5) jadi 1 bar per bulan kalender.

    Kenapa perlu: fetch `period="5y"` menghasilkan ~1254 bar/ticker, dan pada
    4065 ticker itu bikin evidence.json ~1.3GB. Run 2026-07-30 mati dengan
    MemoryError persis di titik tulis evidence.json (RAM 8GB, ~3GB bebas):
    `_atomic_write` menumpuk salinan struktur (asdict -> _sanitize -> string
    json -> bytes UTF-8) sekaligus. Backend juga tidak sanggup, karena
    `backend/app.py::_get_stage` men-json.load SELURUH file lalu menahannya
    permanen di `_stage_cache` (sudah ~2GB RAM di era evidence.json 340MB).

    Bar harian yang lama tidak dipakai siapa pun: Knowledge hanya butuh harga
    acuan ~1/3/5 tahun lalu (`knowledge_helpers.calculate_returns`, kini
    berbasis TANGGAL, bukan jumlah bar) dan chart jangka panjang cukup dengan
    resolusi bulanan. Cache per-ticker tetap menyimpan 5 tahun harian penuh —
    ini murni memangkas apa yang ikut dipersist ke evidence.json.
    """
    if len(bars) <= PRICE_HISTORY_DAILY_BARS:
        return bars
    recent = bars[-PRICE_HISTORY_DAILY_BARS:]
    older = bars[:-PRICE_HISTORY_DAILY_BARS]
    # `older` kronologis, jadi penulisan terakhir per kunci "YYYY-MM" otomatis
    # menyisakan bar hari bursa TERAKHIR di bulan itu.
    monthly: dict[str, PriceBar] = {}
    for bar in older:
        monthly[bar.date[:7]] = bar
    return [monthly[key] for key in sorted(monthly)] + recent


def fetch_price_market_data(ticker: str) -> PriceMarketData:
    """Ambil harga & 5-year historical OHLCV dari Yahoo Finance (cached 6h).

    Yang di-cache: 5 tahun bar harian penuh. Yang DIKEMBALIKAN (dan ikut ke
    evidence.json): versi ringkas — lihat `_downsample_price_history`."""
    cached = cache_get("price_market_data", ticker, PRICE_CACHE_TTL)
    if cached is not None:
        meta = cached.get("_metadata", {})
        return PriceMarketData(
            metadata=SourceMetadata(**meta) if meta else SourceMetadata(
                source="yahoo_finance", fetched_at=datetime.now(timezone.utc).isoformat(), status="ok"
            ),
            last_price=cached.get("last_price"),
            open=cached.get("open"),
            high=cached.get("high"),
            low=cached.get("low"),
            close=cached.get("close"),
            volume=cached.get("volume"),
            market_cap=cached.get("market_cap"),
            shares_outstanding=cached.get("shares_outstanding"),
            beta=cached.get("beta"),
            high_52w=cached.get("high_52w"),
            low_52w=cached.get("low_52w"),
            price_history=_downsample_price_history(
                [PriceBar(**b) for b in cached.get("price_history", [])]
            ),
        )

    try:
        _apply_batch_delay()

        def _do_fetch():
            t = yf.Ticker(ticker)
            fi = t.fast_info
            # period="5y" (bukan "1y") -- Knowledge.calculate_returns() sudah
            # PUNYA logika CAGR return_3y/return_5y sejak lama (butuh >=756/
            # >=1260 hari trading), tapi dorman selamanya karena price_history
            # yang dikasih cuma 1 tahun (~251 bar), gak pernah cukup buat cari
            # harga 3/5 tahun lalu (audit 2026-07-29). Ini bikin evidence.json
            # (sudah 340MB) tumbuh ~5x di price_history -- trade-off yang
            # disetujui pengguna demi mengaktifkan return_3y/return_5y.
            hist = t.history(period="5y")
            if hist is None or hist.empty:
                raise ValueError(f"no price data for {ticker}")
            return fi, hist

        fi, hist = retry(_do_fetch, retries=YF_EVIDENCE_RETRIES,
                          backoff_seconds=YF_EVIDENCE_RETRY_BACKOFF_SECONDS,
                          label=f"yahoo_price:{ticker}")

        last_price = fi.get("lastPrice") or (hist["Close"].iloc[-1] if not hist.empty else None)
        market_cap = fi.get("marketCap")
        shares_outstanding = fi.get("shares")
        # fast_info TIDAK punya key "beta" sama sekali -- diverifikasi langsung
        # ke yfinance (dict fast_info nyata tidak mengandung "beta" sama sekali),
        # jadi fi.get("beta") SELALU None: 0% terisi di 4055 ticker live, ikut
        # bikin plafon Confidence.historical_trend macet di 50% padahal
        # harusnya bisa 66.7% (audit 2026-07-29). Nilainya ADA di `.info`
        # (objek berbeda, dicache lewat _fetch_yahoo_info yang dipakai bareng
        # fundamental/ownership/company_profile -- kalau fungsi lain sudah
        # manggil duluan hari ini, ini cache hit, bukan network call baru).
        # Try/except sendiri supaya kegagalan di sini tidak menggagalkan
        # seluruh price_market_data yang sudah berhasil di atas.
        try:
            beta = _fetch_yahoo_info(ticker).get("beta")
        except Exception as exc:
            print(f"[yahoo_price:{ticker}] gagal ambil beta dari .info, lanjut tanpa: {exc}", file=sys.stderr)
            beta = None

        # OHLCV dari bar terakhir
        open_price = float(hist["Open"].iloc[-1]) if not hist.empty else None
        high = float(hist["High"].iloc[-1]) if not hist.empty else None
        low = float(hist["Low"].iloc[-1]) if not hist.empty else None
        close = float(hist["Close"].iloc[-1]) if not hist.empty else None
        volume = int(hist["Volume"].iloc[-1]) if not hist.empty else None

        # 52-week high/low -- HARUS diambil dari 1 tahun terakhir saja
        # (~252 hari trading), bukan seluruh `hist` yang sekarang 5 tahun
        # (kalau dibiarkan, "high_52w" diam-diam jadi high-5-tahun begitu
        # period fetch berubah -- bug yang gampang lolos kalau tidak
        # disengaja diperbaiki bareng perubahan period di atas).
        hist_1y = hist.tail(252)
        high_52w = float(hist_1y["High"].max()) if not hist_1y.empty else None
        low_52w = float(hist_1y["Low"].min()) if not hist_1y.empty else None

        # Convert historical data ke PriceBar list
        price_history = []
        for idx, row in hist.iterrows():
            date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, 'strftime') else str(idx)
            price_history.append(PriceBar(
                date=date_str,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"])
            ))

        metadata = SourceMetadata(
            source="yahoo_finance",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="ok"
        )

        result = PriceMarketData(
            metadata=metadata,
            last_price=last_price,
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
            market_cap=market_cap,
            shares_outstanding=shares_outstanding,
            beta=beta,
            high_52w=high_52w,
            low_52w=low_52w,
            price_history=price_history
        )

        # Cache menyimpan 5 tahun harian PENUH (masih berguna kalau suatu saat
        # butuh detail harian lama tanpa fetch ulang)...
        to_cache = asdict(result)
        to_cache["_metadata"] = {"source": metadata.source, "fetched_at": metadata.fetched_at, "status": metadata.status}
        del to_cache["metadata"]
        cache_set("price_market_data", ticker, to_cache)

        # ...tapi yang mengalir ke evidence.json diringkas dulu.
        result.price_history = _downsample_price_history(price_history)
        return result
    except Exception as e:
        print(f"[yahoo_price:{ticker}] gagal (post-processing/final): {e}", file=sys.stderr)
        metadata = SourceMetadata(
            source="yahoo_finance",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="missing"
        )
        return PriceMarketData(
            metadata=metadata,
            last_price=None, open=None, high=None, low=None, close=None,
            volume=None, market_cap=None, shares_outstanding=None, beta=None,
            high_52w=None, low_52w=None, price_history=[]
        )


def fetch_fundamental_data(ticker: str) -> FundamentalData:
    """Ambil fundamental lengkap dari Yahoo Finance (cached 24h)."""
    # Check cache
    cached = cache_get("fundamental_data", ticker, FUNDAMENTAL_CACHE_TTL)
    if cached is not None:
        meta = cached.get("_metadata", {})
        return FundamentalData(
            metadata=SourceMetadata(**meta) if meta else SourceMetadata(
                source="yahoo_finance", fetched_at=datetime.now(timezone.utc).isoformat(), status="ok"
            ),
            revenue=cached.get("revenue"),
            net_income=cached.get("net_income"),
            eps=cached.get("eps"),
            pe_ratio=cached.get("pe_ratio"),
            debt_to_equity=cached.get("debt_to_equity"),
            current_ratio=cached.get("current_ratio"),
            quick_ratio=cached.get("quick_ratio"),
            roe=cached.get("roe"),
            roa=cached.get("roa"),
            operating_margin=cached.get("operating_margin"),
            gross_margin=cached.get("gross_margin"),
            free_cash_flow=cached.get("free_cash_flow"),
            dividend_yield=cached.get("dividend_yield"),
            payout_ratio=cached.get("payout_ratio"),
            book_value_per_share=cached.get("book_value_per_share"),
            asset_turnover=cached.get("asset_turnover"),
            inventory_turnover=cached.get("inventory_turnover"),
            interest_coverage=cached.get("interest_coverage"),
            sector=cached.get("sector"),
            industry=cached.get("industry"),
            field_availability=cached.get("field_availability", {}),
            field_quality=cached.get("field_quality", {}),
        )

    try:
        info = _fetch_yahoo_info(ticker)

        metadata = SourceMetadata(
            source="yahoo_finance",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="ok"
        )

        data = FundamentalData(
            metadata=metadata,
            revenue=_safe_float(info.get("totalRevenue")),
            net_income=_safe_float(info.get("netIncomeToCommon")),
            eps=_safe_float(info.get("trailingEps")),
            pe_ratio=_safe_float(info.get("trailingPE")),
            debt_to_equity=_safe_float(info.get("debtToEquity")),
            current_ratio=_safe_float(info.get("currentRatio")),
            quick_ratio=_safe_float(info.get("quickRatio")),
            roe=_safe_float(info.get("returnOnEquity")),
            roa=_safe_float(info.get("returnOnAssets")),
            operating_margin=_safe_float(info.get("operatingMargins")),
            gross_margin=_safe_float(info.get("grossMargins")),
            free_cash_flow=_safe_float(info.get("freeCashflow")),
            dividend_yield=_safe_float(info.get("dividendYield")),
            payout_ratio=_safe_float(info.get("payoutRatio")),
            book_value_per_share=_safe_float(info.get("bookValue")),
            asset_turnover=_safe_float(info.get("assetTurnover")),
            inventory_turnover=_safe_float(info.get("inventoryTurnover")),
            interest_coverage=_safe_float(info.get("interestCoverage")),
            sector=info.get("sector"),
            industry=info.get("industry")
        )
        data.field_availability = _classify_fundamental_availability(data)

        # Cache
        to_cache = {
            "revenue": data.revenue,
            "net_income": data.net_income,
            "eps": data.eps,
            "pe_ratio": data.pe_ratio,
            "debt_to_equity": data.debt_to_equity,
            "current_ratio": data.current_ratio,
            "quick_ratio": data.quick_ratio,
            "roe": data.roe,
            "roa": data.roa,
            "operating_margin": data.operating_margin,
            "gross_margin": data.gross_margin,
            "free_cash_flow": data.free_cash_flow,
            "dividend_yield": data.dividend_yield,
            "payout_ratio": data.payout_ratio,
            "book_value_per_share": data.book_value_per_share,
            "asset_turnover": data.asset_turnover,
            "inventory_turnover": data.inventory_turnover,
            "interest_coverage": data.interest_coverage,
            "sector": data.sector,
            "industry": data.industry,
            "field_availability": data.field_availability,
            "field_quality": data.field_quality,
            "_metadata": {"source": metadata.source, "fetched_at": metadata.fetched_at, "status": metadata.status}
        }
        cache_set("fundamental_data", ticker, to_cache)

        return data
    except Exception as e:
        print(f"[yahoo_fundamental:{ticker}] gagal (post-processing/final): {e}", file=sys.stderr)
        metadata = SourceMetadata(
            source="yahoo_finance",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="missing"
        )
        fallback = FundamentalData(
            metadata=metadata,
            revenue=None, net_income=None, eps=None, pe_ratio=None,
            debt_to_equity=None, current_ratio=None, quick_ratio=None,
            roe=None, roa=None, operating_margin=None, gross_margin=None,
            free_cash_flow=None, dividend_yield=None, payout_ratio=None,
            book_value_per_share=None, asset_turnover=None, inventory_turnover=None,
            interest_coverage=None
        )
        fallback.field_availability = _classify_fundamental_availability(fallback)
        return fallback


def _fetch_institutional_holders_detail(ticker: str) -> list[dict]:
    """Fetch daftar top institusi pemegang saham (Yahoo Finance
    `institutional_holders` — agregasi Yahoo dari SEC 13F, bukan parsing
    manual filing). Endpoint terpisah dari `.info`, jadi ini panggilan
    network baru per ticker (bukan bagian dari konsolidasi sebelumnya)."""
    def _do_fetch():
        df = yf.Ticker(ticker).institutional_holders
        if df is None or df.empty:
            return []
        holders = []
        for _, row in df.iterrows():
            date_val = row.get("Date Reported")
            pct_held = row.get("pctHeld")
            pct_change = row.get("pctChange")
            holders.append({
                "holder": str(row.get("Holder", "")),
                "shares": int(row["Shares"]) if row.get("Shares") is not None else None,
                "pct_held": float(pct_held) * 100 if pct_held is not None else None,
                "value_usd": float(row["Value"]) if row.get("Value") is not None else None,
                "date_reported": date_val.strftime("%Y-%m-%d") if hasattr(date_val, "strftime") else (str(date_val) if date_val is not None else None),
                "pct_change": float(pct_change) * 100 if pct_change is not None else None,
            })
        return holders

    # Ikut throttle yang sama dengan endpoint Yahoo lain. Sebelumnya fungsi ini
    # (dan _fetch_earnings_history/_fetch_revenue_estimate) melewati
    # _apply_batch_delay sama sekali — tidak terlalu terasa saat Evidence masih
    # serial, tapi sejak jalan 5 thread paralel (EVIDENCE_WORKERS) justru cuma
    # ketiga endpoint INI yang dihantam tanpa jeda, sementara sisanya diatur.
    # Akibatnya field-fieldnya kelihatan "tidak tersedia" padahal sebenarnya
    # kena throttle Yahoo.
    _apply_batch_delay()
    return retry(_do_fetch, retries=YF_EVIDENCE_RETRIES,
                 backoff_seconds=YF_EVIDENCE_RETRY_BACKOFF_SECONDS,
                 label=f"yahoo_holders:{ticker}")


def fetch_institutional_ownership(ticker: str) -> InstitutionalOwnership:
    """Ambil kepemilikan institusional: persentase agregat + top holder detail (cached 24h)."""
    cached = cache_get("institutional_ownership", ticker, OWNERSHIP_CACHE_TTL)
    if cached is not None:
        meta = cached.get("_metadata", {})
        return InstitutionalOwnership(
            metadata=SourceMetadata(**meta) if meta else SourceMetadata(
                source="yahoo_finance", fetched_at=datetime.now(timezone.utc).isoformat(), status="ok"
            ),
            percentage=cached.get("percentage"),
            # .get() bukan langsung index -- entry cache lama (sebelum audit
            # 2026-07-29) tidak punya key ini sama sekali, harus degradasi
            # anggun ke None, bukan KeyError.
            insider_percentage=cached.get("insider_percentage"),
            top_holders=[InstitutionalHolder(**h) for h in cached.get("top_holders", [])]
        )

    try:
        info = _fetch_yahoo_info(ticker)
        percentage = _safe_float(info.get("heldPercentInstitutions"))
        # heldPercentInsiders ada di objek .info yang SAMA (sudah di-fetch di
        # baris atas buat heldPercentInstitutions) -- BUKAN network call baru.
        # Field ini sebelumnya tidak pernah diambil sama sekali (0% terisi di
        # 4055 ticker live), bukan karena datanya gak ada.
        insider_percentage = _safe_float(info.get("heldPercentInsiders"))

        try:
            holders_raw = _fetch_institutional_holders_detail(ticker)
        except Exception as exc:
            print(f"[yahoo_holders:{ticker}] gagal (post-processing/final), lanjut tanpa detail holder: {exc}", file=sys.stderr)
            holders_raw = []
        top_holders = [InstitutionalHolder(**h) for h in holders_raw]

        metadata = SourceMetadata(
            source="yahoo_finance",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="ok" if percentage is not None else "missing"
        )

        data = InstitutionalOwnership(
            metadata=metadata, percentage=percentage, insider_percentage=insider_percentage, top_holders=top_holders,
        )

        # Cache
        to_cache = {
            "percentage": percentage,
            "insider_percentage": insider_percentage,
            "top_holders": holders_raw,
            "_metadata": {"source": metadata.source, "fetched_at": metadata.fetched_at, "status": metadata.status}
        }
        cache_set("institutional_ownership", ticker, to_cache)

        return data
    except Exception as e:
        print(f"[yahoo_ownership:{ticker}] gagal (post-processing/final): {e}", file=sys.stderr)
        metadata = SourceMetadata(
            source="yahoo_finance",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="missing"
        )
        return InstitutionalOwnership(metadata=metadata)


def fetch_company_profile(ticker: str) -> CompanyProfile:
    """Identitas & deskripsi perusahaan — dari field `.info` yang sudah
    ke-cache lewat `_fetch_yahoo_info` (dipakai bareng fundamental/ownership),
    field-field ini sebelumnya dibuang tanpa diekstrak. TIDAK ADA network
    call baru."""
    cached = cache_get("company_profile", ticker, COMPANY_PROFILE_CACHE_TTL)
    if cached is not None:
        meta = cached.get("_metadata", {})
        return CompanyProfile(
            metadata=SourceMetadata(**meta) if meta else SourceMetadata(
                source="yahoo_finance", fetched_at=datetime.now(timezone.utc).isoformat(), status="ok"
            ),
            long_name=cached.get("long_name"),
            business_summary=cached.get("business_summary"),
            website=cached.get("website"),
            employees=cached.get("employees"),
            city=cached.get("city"),
            country=cached.get("country"),
        )

    try:
        info = _fetch_yahoo_info(ticker)
        long_name = info.get("longName") or info.get("shortName")
        employees = info.get("fullTimeEmployees")

        metadata = SourceMetadata(
            source="yahoo_finance",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="ok" if long_name else "missing"
        )

        data = CompanyProfile(
            metadata=metadata,
            long_name=long_name,
            business_summary=info.get("longBusinessSummary"),
            website=info.get("website"),
            employees=int(employees) if isinstance(employees, (int, float)) else None,
            city=info.get("city"),
            country=info.get("country"),
        )

        cache_set("company_profile", ticker, {
            "long_name": data.long_name,
            "business_summary": data.business_summary,
            "website": data.website,
            "employees": data.employees,
            "city": data.city,
            "country": data.country,
            "_metadata": {"source": metadata.source, "fetched_at": metadata.fetched_at, "status": metadata.status},
        })

        return data
    except Exception as e:
        print(f"[yahoo_company_profile:{ticker}] gagal (post-processing/final): {e}", file=sys.stderr)
        return CompanyProfile(metadata=SourceMetadata(
            source="yahoo_finance", fetched_at=datetime.now(timezone.utc).isoformat(), status="missing"
        ))


def _fetch_earnings_history(ticker: str) -> list[dict]:
    """EPS actual vs estimate analis, 4 kuartal terakhir dilaporkan
    (`Ticker.earnings_history`) — endpoint terpisah dari `.info`, network
    call baru per ticker. surprise_pct dikonversi ke skala percentage-point
    (12.4 = 12.4%), konsisten dengan konvensi net_margin_q4/return_1y di
    fundamental data — bukan skala fraksi seperti institutional percentage."""
    def _do_fetch():
        df = yf.Ticker(ticker).earnings_history
        if df is None or df.empty:
            return []
        out = []
        for idx, row in df.iterrows():
            surprise = _safe_float(row.get("surprisePercent"))
            out.append({
                "quarter": idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx),
                "eps_actual": _safe_float(row.get("epsActual")),
                "eps_estimate": _safe_float(row.get("epsEstimate")),
                "surprise_pct": surprise * 100 if surprise is not None else None,
            })
        return out

    _apply_batch_delay()  # lihat catatan di _fetch_institutional_holders_detail
    return retry(_do_fetch, retries=YF_EVIDENCE_RETRIES,
                 backoff_seconds=YF_EVIDENCE_RETRY_BACKOFF_SECONDS,
                 label=f"yahoo_earnings_history:{ticker}")


def _fetch_revenue_estimate(ticker: str) -> list[dict]:
    """Konsensus revenue analis forward — kuartal ini/depan, tahun ini/depan
    (`Ticker.revenue_estimate`) — network call baru per ticker. Cuma
    forward-looking, TIDAK ada histori revenue-estimate-vs-actual bertahun-
    tahun via yfinance gratis (beda dari earnings_history yang historis)."""
    def _do_fetch():
        df = yf.Ticker(ticker).revenue_estimate
        if df is None or df.empty:
            return []
        out = []
        for idx, row in df.iterrows():
            growth = _safe_float(row.get("growth"))
            num_analysts = row.get("numberOfAnalysts")
            out.append({
                "period": str(idx),
                "avg": _safe_float(row.get("avg")),
                "low": _safe_float(row.get("low")),
                "high": _safe_float(row.get("high")),
                "growth": growth * 100 if growth is not None else None,
                "num_analysts": int(num_analysts) if isinstance(num_analysts, (int, float)) and num_analysts == num_analysts else None,
            })
        return out

    _apply_batch_delay()  # lihat catatan di _fetch_institutional_holders_detail
    return retry(_do_fetch, retries=YF_EVIDENCE_RETRIES,
                 backoff_seconds=YF_EVIDENCE_RETRY_BACKOFF_SECONDS,
                 label=f"yahoo_revenue_estimate:{ticker}")


def fetch_analyst_estimates(ticker: str) -> AnalystEstimates:
    """Konsensus analis: price target & rating dari `.info` (gratis, sudah
    ke-cache) + EPS surprise history & revenue estimate forward (network
    call baru per ticker, lihat _fetch_earnings_history/_fetch_revenue_estimate)."""
    cached = cache_get("analyst_estimates", ticker, ANALYST_ESTIMATES_CACHE_TTL)
    if cached is not None:
        meta = cached.get("_metadata", {})
        return AnalystEstimates(
            metadata=SourceMetadata(**meta) if meta else SourceMetadata(
                source="yahoo_finance", fetched_at=datetime.now(timezone.utc).isoformat(), status="ok"
            ),
            target_low=cached.get("target_low"),
            target_high=cached.get("target_high"),
            target_mean=cached.get("target_mean"),
            target_median=cached.get("target_median"),
            recommendation_mean=cached.get("recommendation_mean"),
            recommendation_key=cached.get("recommendation_key"),
            num_analyst_opinions=cached.get("num_analyst_opinions"),
            eps_surprise_history=[EpsSurprise(**e) for e in cached.get("eps_surprise_history", [])],
            revenue_estimates=[RevenueEstimatePeriod(**r) for r in cached.get("revenue_estimates", [])],
            field_availability=cached.get("field_availability", {}),
            field_quality=cached.get("field_quality", {}),
        )

    try:
        info = _fetch_yahoo_info(ticker)
        target_mean = _safe_float(info.get("targetMeanPrice"))

        try:
            eps_history_raw = _fetch_earnings_history(ticker)
        except Exception as exc:
            print(f"[yahoo_analyst_estimates:{ticker}] gagal earnings_history, lanjut tanpa: {exc}", file=sys.stderr)
            eps_history_raw = []
        try:
            revenue_est_raw = _fetch_revenue_estimate(ticker)
        except Exception as exc:
            print(f"[yahoo_analyst_estimates:{ticker}] gagal revenue_estimate, lanjut tanpa: {exc}", file=sys.stderr)
            revenue_est_raw = []

        num_opinions = info.get("numberOfAnalystOpinions")
        metadata = SourceMetadata(
            source="yahoo_finance",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="ok" if (target_mean is not None or eps_history_raw or revenue_est_raw) else "missing"
        )

        data = AnalystEstimates(
            metadata=metadata,
            target_low=_safe_float(info.get("targetLowPrice")),
            target_high=_safe_float(info.get("targetHighPrice")),
            target_mean=target_mean,
            target_median=_safe_float(info.get("targetMedianPrice")),
            recommendation_mean=_safe_float(info.get("recommendationMean")),
            recommendation_key=info.get("recommendationKey"),
            num_analyst_opinions=int(num_opinions) if isinstance(num_opinions, (int, float)) else None,
            eps_surprise_history=[EpsSurprise(**e) for e in eps_history_raw],
            revenue_estimates=[RevenueEstimatePeriod(**r) for r in revenue_est_raw],
        )
        data.field_availability, data.field_quality = _classify_analyst_estimates(data)

        cache_set("analyst_estimates", ticker, {
            "target_low": data.target_low,
            "target_high": data.target_high,
            "target_mean": data.target_mean,
            "target_median": data.target_median,
            "recommendation_mean": data.recommendation_mean,
            "recommendation_key": data.recommendation_key,
            "num_analyst_opinions": data.num_analyst_opinions,
            "eps_surprise_history": eps_history_raw,
            "revenue_estimates": revenue_est_raw,
            "field_availability": data.field_availability,
            "field_quality": data.field_quality,
            "_metadata": {"source": metadata.source, "fetched_at": metadata.fetched_at, "status": metadata.status},
        })

        return data
    except Exception as e:
        print(f"[yahoo_analyst_estimates:{ticker}] gagal (post-processing/final): {e}", file=sys.stderr)
        fallback = AnalystEstimates(metadata=SourceMetadata(
            source="yahoo_finance", fetched_at=datetime.now(timezone.utc).isoformat(), status="missing"
        ))
        fallback.field_availability, fallback.field_quality = _classify_analyst_estimates(fallback)
        return fallback
