"""Helper functions untuk Knowledge calculations — tren, technical indicators, financial metrics."""
from __future__ import annotations

import math
from bisect import bisect_left
from datetime import date, timedelta

from .contracts import PriceBar


# Toleransi pencarian harga acuan: bar terdekat ke target tanggal masih dipakai
# kalau selisihnya <= ini. 45 hari cukup longgar untuk bar bulanan (jarak antar
# bar <=31 hari) sekaligus cukup ketat untuk menolak histori yang benar-benar
# tidak mencapai window yang diminta (mis. ticker IPO 2 tahun lalu tidak boleh
# dapat return_5y dari bar tertuanya).
_ANCHOR_TOLERANCE_DAYS = 45


def _is_finite_number(value) -> bool:
    """True hanya untuk angka nyata. `close` bisa None (NaN yang di-null-kan
    json_safe saat serialisasi) — dipakai di beberapa tempat, jadi satu tempat."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _bar_gap_days(prev_bar: PriceBar, curr_bar: PriceBar) -> int | None:
    """Jarak kalender antar dua bar dalam hari, None kalau tanggalnya tak terbaca."""
    try:
        return (date.fromisoformat(curr_bar.date) - date.fromisoformat(prev_bar.date)).days
    except (ValueError, TypeError):
        return None


def _find_price_on_or_before(sorted_dates: list[str], prices_by_date: dict[str, float],
                             target: date) -> float | None:
    """Harga penutupan pada bar terdekat ke `target` (arah mana pun), asalkan
    selisihnya <= _ANCHOR_TOLERANCE_DAYS. None kalau histori tidak menjangkau."""
    target_str = target.isoformat()
    # sorted_dates terurut ISO (YYYY-MM-DD) = terurut kronologis, jadi bisa bisect.
    idx = bisect_left(sorted_dates, target_str)
    best: tuple[int, str] | None = None
    for cand_idx in (idx - 1, idx):
        if 0 <= cand_idx < len(sorted_dates):
            cand = sorted_dates[cand_idx]
            gap = abs((date.fromisoformat(cand) - target).days)
            if best is None or gap < best[0]:
                best = (gap, cand)
    if best is None or best[0] > _ANCHOR_TOLERANCE_DAYS:
        return None
    return prices_by_date[best[1]]


def calculate_returns(price_history: list[PriceBar]) -> dict[str, float | None]:
    """Calculate returns dari price history.

    Returns: {
        'return_1y': % return, 1 tahun terakhir (atau seluruh histori kalau lebih pendek),
        'return_3y': % annual return (CAGR), 3 tahun terakhir,
        'return_5y': % annual return (CAGR), 5 tahun terakhir
    }

    Berbasis TANGGAL, bukan jumlah bar. Versi lama memakai `len(bars)` sebagai
    proksi waktu (`>= 750` bar untuk 3 tahun, `len - 366` untuk 1 tahun) — dua
    masalah nyata:

    1. `len - 366` mundur 366 *hari bursa* (~1.45 tahun kalender, karena
       setahun hanya ~252 hari bursa), jadi "return_1y" sebenarnya mengukur
       ~17 bulan. Sekarang dihitung dari tanggal sebenarnya.
    2. Proksi itu rapuh terhadap kerapatan bar. price_history yang dipersist
       kini diringkas (harian 1 tahun terakhir + bulanan tahun ke-2..5, lihat
       `sources/yahoo_evidence._downsample_price_history`), jadi ~300 bar
       mewakili 5 tahun — ambang berbasis hitungan bar akan salah menyimpulkan
       "histori kurang dari 3 tahun" dan mematikan return_3y/return_5y.
    """
    if not price_history or len(price_history) < 2:
        return {'return_1y': None, 'return_3y': None, 'return_5y': None}

    # Bars sudah sorted chronologically, last = most recent.
    # `close` WAJIB divalidasi, bukan dipakai langsung: bar dengan harga NaN
    # (baris kosong dari pandas) di-null-kan di batas serialisasi oleh
    # json_safe.dumps_safe, jadi cache bisa berisi `close: null` yang kembali
    # sebagai PriceBar(close=None). Versi sebelumnya langsung membandingkan
    # `first_price <= 0` dan meledak `TypeError: '<=' not supported between
    # NoneType and int`; karena run_knowledge menangkap exception per-ticker,
    # ticker itu hilang dari knowledge.json tanpa jejak sama sekali — persis
    # selisih 4065 evidence vs 4030 knowledge di run 2026-07-30.
    prices_by_date = {
        bar.date: float(bar.close)
        for bar in price_history
        if bar.date and isinstance(bar.close, (int, float)) and not isinstance(bar.close, bool)
        and math.isfinite(bar.close)
    }
    if len(prices_by_date) < 2:
        return {'return_1y': None, 'return_3y': None, 'return_5y': None}
    sorted_dates = sorted(prices_by_date.keys())

    latest_date_str = sorted_dates[-1]
    latest_price = prices_by_date[latest_date_str]
    first_price = prices_by_date[sorted_dates[0]]

    if first_price <= 0:
        return {'return_1y': None, 'return_3y': None, 'return_5y': None}

    try:
        latest_date = date.fromisoformat(latest_date_str)
    except ValueError:
        # Format tanggal tak terduga — jangan mengarang angka, laporkan kosong.
        return {'return_1y': None, 'return_3y': None, 'return_5y': None}

    span_days = (latest_date - date.fromisoformat(sorted_dates[0])).days

    # 1-year return. Kalau seluruh histori <1 tahun, seluruh histori ITU
    # window-nya (bukan diannualisasi — melebih-lebihkan return ticker baru).
    if span_days <= 365:
        return_1y = ((latest_price - first_price) / first_price) * 100
    else:
        price_1y_ago = _find_price_on_or_before(
            sorted_dates, prices_by_date, latest_date - timedelta(days=365))
        return_1y = (((latest_price - price_1y_ago) / price_1y_ago) * 100
                     if price_1y_ago and price_1y_ago > 0 else None)

    price_3y_ago = _find_price_on_or_before(
        sorted_dates, prices_by_date, latest_date - timedelta(days=365 * 3))
    return_3y = _cagr(price_3y_ago, latest_price, 3) if price_3y_ago else None

    price_5y_ago = _find_price_on_or_before(
        sorted_dates, prices_by_date, latest_date - timedelta(days=365 * 5))
    return_5y = _cagr(price_5y_ago, latest_price, 5) if price_5y_ago else None

    return {
        'return_1y': return_1y,
        'return_3y': return_3y,
        'return_5y': return_5y
    }


def _cagr(start_price: float | None, end_price: float, years: float) -> float | None:
    """Calculate CAGR (Compound Annual Growth Rate)."""
    if not start_price or start_price <= 0 or end_price <= 0 or years <= 0:
        return None
    return (pow(end_price / start_price, 1 / years) - 1) * 100


# Jarak maksimum antar dua bar agar selisihnya masih sah disebut "return harian".
# 7 hari menampung akhir pekan + libur bursa panjang, tapi menolak lompatan
# bulanan (~30 hari).
_DAILY_GAP_MAX_DAYS = 7


def calculate_volatility(price_history: list[PriceBar]) -> float | None:
    """Volatilitas harian (std dev % return antar hari bursa berurutan).

    HANYA memakai pasangan bar yang benar-benar berjarak harian. Sejak
    `sources/yahoo_evidence._downsample_price_history`, price_history yang
    dipersist TIDAK lagi berjarak seragam: ~48 bar BULANAN (tahun ke-2..5)
    diikuti 252 bar harian. Versi sebelumnya menghitung selisih antar SEMUA
    bar berurutan lalu melabelinya "harian", sehingga ~16% sampelnya adalah
    gerakan sebulan — yang deviasinya ~sqrt(21) ≈ 4.6x lebih besar.

    Dampak terukur pada 353 ticker nyata: volatilitas membengkak rata-rata
    1.98x (maks 4.14x). Ambang `> 5.0` di risk.py melonjak dari 22% ke 59%
    ticker (red flag `high_volatility` palsu), dan `> 4.0` di reasoning.py
    dari 33% ke 75% ("High volatility - trading opportunity" untuk hampir
    semua ticker, sekaligus membuat cabang `< 1.0` praktis tak terjangkau).

    Menyaring berdasarkan jarak TANGGAL, bukan sekadar mengambil 252 bar
    terakhir, supaya benar juga untuk histori harian penuh (mis. jalur CLI
    atau kalau downsampling diubah/dimatikan) tanpa bergantung pada konstanta
    di modul lain.
    """
    if not price_history or len(price_history) < 2:
        return None

    daily_returns = []
    for i in range(1, len(price_history)):
        prev_bar = price_history[i - 1]
        curr_bar = price_history[i]
        prev_close, curr_close = prev_bar.close, curr_bar.close
        # `close` bisa None: NaN dari bar kosong pandas di-null-kan di batas
        # serialisasi oleh json_safe, lalu kembali sebagai PriceBar(close=None).
        # Tanpa penjagaan ini `prev_close > 0` melempar TypeError, ditelan
        # except per-ticker di run_knowledge, dan ticker itu hilang dari
        # knowledge.json tanpa jejak (sisa selisih 4065 evidence vs 4063
        # knowledge pada run 2026-07-30).
        if not _is_finite_number(prev_close) or not _is_finite_number(curr_close):
            continue
        if prev_close <= 0:
            continue
        gap = _bar_gap_days(prev_bar, curr_bar)
        if gap is None or gap > _DAILY_GAP_MAX_DAYS:
            continue
        daily_returns.append(((curr_close - prev_close) / prev_close) * 100)

    if len(daily_returns) < 2:
        return None

    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
    std_dev = math.sqrt(variance)

    return std_dev


def calculate_high_low_52w(price_history: list[PriceBar]) -> dict[str, float | None]:
    """52-week high/low dari price history.

    Saat ini TIDAK dipanggil dari mana pun — knowledge.py mengambil
    high_52w/low_52w langsung dari Evidence. Tetap dijaga terhadap
    `close=None` (lihat catatan di calculate_volatility) supaya tidak
    meledak kalau suatu saat dipakai lagi.

    Catatan: `[-252:]` di bawah kebetulan persis memilih blok harian yang
    dipertahankan `_downsample_price_history` (PRICE_HISTORY_DAILY_BARS=252).
    Kalau konstanta itu berubah, potongan di sini ikut salah diam-diam.
    """
    if not price_history:
        return {'high_52w': None, 'low_52w': None}

    # 52-week = roughly 252 trading days
    relevant_bars = price_history[-252:] if len(price_history) >= 252 else price_history

    closes = [bar.close for bar in relevant_bars if _is_finite_number(bar.close)]
    if not closes:
        return {'high_52w': None, 'low_52w': None}

    return {
        'high_52w': max(closes),
        'low_52w': min(closes)
    }


def calculate_financial_metrics(
    revenue: float | None,
    net_income: float | None,
    free_cash_flow: float | None,
    market_cap: float | None,
    shares_outstanding: int | None,
    last_price: float | None,
    eps: float | None,
    book_value_per_share: float | None = None
) -> dict[str, float | None]:
    """Calculate derived financial metrics dari fundamental data."""
    metrics = {
        'ps_ratio': None,
        'fcf_yield_pct': None,
        'price_to_fcf': None,
        'net_margin_pct': None,
        'fcf_to_net_income_pct': None,
        'pb_ratio': None
    }

    # P/S ratio
    if market_cap and market_cap > 0 and revenue and revenue > 0:
        metrics['ps_ratio'] = market_cap / revenue

    # FCF yield -- audit item B5: `free_cash_flow and ...` dulu membuang FCF
    # tepat 0.0 (breakeven) sebagai "data hilang". market_cap TIDAK ikut
    # diubah (pembagi harus > 0, sudah benar).
    if free_cash_flow is not None and market_cap and market_cap > 0:
        metrics['fcf_yield_pct'] = (free_cash_flow / market_cap) * 100

    # Price to FCF
    if free_cash_flow and free_cash_flow > 0 and shares_outstanding and shares_outstanding > 0 and last_price and last_price > 0:
        fcf_per_share = free_cash_flow / shares_outstanding
        if fcf_per_share > 0:
            metrics['price_to_fcf'] = last_price / fcf_per_share

    # Net margin
    if net_income is not None and revenue and revenue > 0:
        metrics['net_margin_pct'] = (net_income / revenue) * 100

    # FCF to Net Income
    if free_cash_flow is not None and net_income and net_income > 0:
        metrics['fcf_to_net_income_pct'] = (free_cash_flow / net_income) * 100

    # P/B ratio
    if last_price and last_price > 0 and book_value_per_share and book_value_per_share > 0:
        metrics['pb_ratio'] = last_price / book_value_per_share

    return metrics


def compute_financial_trends(quarterly_data: list | None) -> dict:
    """#2 Financial Trends: Compute YoY growth, margin trends dari quarterly data.

    Input: list of QuarterlyFundamental (most recent first)
    Returns: {
        'revenue_yoy_q1': float % or None,  # Q-3 vs prior year Q-3
        'revenue_yoy_q2': float % or None,  # Q-2
        'revenue_yoy_q3': float % or None,  # Q-1
        'revenue_yoy_q4': float % or None,  # Q (most recent)
        'gross_margin_q1': float % or None,  # Q-3
        'gross_margin_q2': float % or None,
        'gross_margin_q3': float % or None,
        'gross_margin_q4': float % or None,
        'operating_margin_q1': float % or None,
        'operating_margin_q2': float % or None,
        'operating_margin_q3': float % or None,
        'operating_margin_q4': float % or None,
        'net_margin_q1': float % or None,
        'net_margin_q2': float % or None,
        'net_margin_q3': float % or None,
        'net_margin_q4': float % or None,
        'capex_pct_revenue_q1': float % or None,
        'capex_pct_revenue_q2': float % or None,
        'capex_pct_revenue_q3': float % or None,
        'capex_pct_revenue_q4': float % or None,
        'revenue_cagr_3y': float % or None,  # TTM kini vs TTM 3 tahun lalu
        'revenue_cagr_5y': float % or None,  # butuh 24 kuartal tersimpan
    }
    """
    if not quarterly_data or len(quarterly_data) < 4:
        return {}  # Need at least 4 quarters

    trends = {}

    # Process last 4 quarters (most recent first)
    # quarterly_data[0] is most recent (Q), [1] is Q-1, [2] is Q-2, [3] is Q-3
    for i in range(min(4, len(quarterly_data))):
        period = quarterly_data[i]
        q_num = 4 - i  # q4, q3, q2, q1
        q_key = f"q{q_num}"

        # YoY growth: compare to same quarter last year (typically i+4 in list)
        if len(quarterly_data) > i + 4:
            prior_year = quarterly_data[i + 4]
            if hasattr(period, 'revenue') and hasattr(prior_year, 'revenue'):
                rev = getattr(period, 'revenue')
                prior_rev = getattr(prior_year, 'revenue')
                # Audit item B5: `rev and ...` dulu membuang revenue yang
                # jatuh tepat ke 0.0 sebagai "data hilang" -- itu justru
                # sinyal fundamental paling mengkhawatirkan yang bisa
                # muncul. prior_rev (pembagi) TIDAK ikut diubah (tetap
                # harus > 0, sudah benar).
                if rev is not None and prior_rev is not None and prior_rev > 0:
                    yoy = ((rev - prior_rev) / prior_rev) * 100
                    trends[f"revenue_yoy_{q_key}"] = yoy

        # Margins
        if hasattr(period, 'revenue') and getattr(period, 'revenue'):
            rev = getattr(period, 'revenue')
            if rev > 0:
                # Gross margin
                if hasattr(period, 'gross_profit') and getattr(period, 'gross_profit'):
                    gp = getattr(period, 'gross_profit')
                    trends[f"gross_margin_{q_key}"] = (gp / rev) * 100
                # Operating margin
                if hasattr(period, 'operating_income') and getattr(period, 'operating_income'):
                    oi = getattr(period, 'operating_income')
                    trends[f"operating_margin_{q_key}"] = (oi / rev) * 100
                # Net margin
                if hasattr(period, 'net_income') and getattr(period, 'net_income'):
                    ni = getattr(period, 'net_income')
                    trends[f"net_margin_{q_key}"] = (ni / rev) * 100
                # CapEx as % revenue -- audit item B5: capex == 0 normal
                # untuk perusahaan software asset-light, tapi truthy check
                # dulu membuang justru kelompok itu sebagai "data hilang".
                if hasattr(period, 'capital_expenditures') and getattr(period, 'capital_expenditures') is not None:
                    capex = getattr(period, 'capital_expenditures')
                    trends[f"capex_pct_revenue_{q_key}"] = (capex / rev) * 100

    for years in (3, 5):
        cagr = _revenue_cagr(quarterly_data, years)
        if cagr is not None:
            trends[f"revenue_cagr_{years}y"] = cagr

    return trends


def _ttm_revenue(quarterly_data: list, start: int) -> float | None:
    """Revenue 12 bulan dari 4 kuartal berurutan mulai indeks `start`.

    Dijumlahkan dari kuartal, bukan diambil dari angka TTM Yahoo, supaya
    pembilang dan penyebut CAGR berasal dari deret dan definisi tag yang sama
    persis (lihat pemilihan tag di sec_parser._extract_quarterly_series).
    """
    if start + 4 > len(quarterly_data):
        return None
    total = 0.0
    for period in quarterly_data[start:start + 4]:
        rev = getattr(period, "revenue", None)
        if rev is None:
            return None  # satu kuartal bolong = TTM-nya bukan 12 bulan
        total += rev
    return total


def _revenue_cagr(quarterly_data: list, years: int) -> float | None:
    """CAGR revenue (%) atas `years` tahun, TTM-kini vs TTM-`years`-tahun-lalu.

    Dua TTM dipakai (bukan kuartal tunggal) supaya musiman tidak terbaca
    sebagai pertumbuhan. Butuh 4*(years+1) kuartal: 16 untuk 3Y, 24 untuk 5Y —
    dan itu alasan `fetch_quarterly_financials` menyimpan 24 kuartal.

    None kalau TTM pembanding <= 0: CAGR dari basis nol/negatif tidak punya
    arti (akar dari rasio negatif), dan mengembalikan angka apa pun di situ
    lebih buruk daripada mengaku tidak tahu.
    """
    now = _ttm_revenue(quarterly_data, 0)
    then = _ttm_revenue(quarterly_data, 4 * years)
    if now is None or then is None or then <= 0 or now < 0:
        return None
    return ((now / then) ** (1 / years) - 1) * 100


def infer_size_category(market_cap: float | None, soft_flags: list[str]) -> str | None:
    """Infer size category dari market cap + soft flags dari Screening."""
    if not market_cap:
        # Check flags
        if "micro_cap" in soft_flags:
            return "micro"
        if "small_cap" in soft_flags or "recent_ipo" in soft_flags:
            return "small"
        if "mid_cap" in soft_flags:
            return "mid"
        if "large_cap" in soft_flags:
            return "large"
        return None

    # Thresholds (typical market cap ranges)
    if market_cap < 300_000_000:
        return "micro"
    elif market_cap < 2_000_000_000:
        return "small"
    elif market_cap < 10_000_000_000:
        return "mid"
    else:
        return "large"


# Pemetaan industry (Yahoo, GICS-style) -> BusinessModel (subscription/
# hardware/marketplace/saas/other) -- CUMA industry yang KLASIFIKASINYA
# JELAS dipetakan ke salah satu dari 4 kategori spesifik; industri tradisional
# (Biotechnology, Bank, Asuransi, REIT, Oil & Gas, dll -- mayoritas dari 144
# industry yang muncul di data live) SENGAJA jatuh ke fallback "other" alih-
# alih dipaksa masuk kategori yang tidak cocok. "other" itu jawaban VALID
# (perusahaan ini memang bukan salah satu dari 4 model itu), bukan "tidak
# tahu" -- beda dari None (industry-nya sendiri tidak diketahui).
# Dibangun dari daftar 144 industry unik yang nyata muncul di data live
# (audit 2026-07-29), bukan tebakan buta -- lihat _infer_business_model.
_INDUSTRY_BUSINESS_MODEL: dict[str, str] = {
    # saas -- software recurring, jelas
    "Software - Application": "saas",
    "Software - Infrastructure": "saas",
    # hardware -- produsen barang fisik, jelas
    "Medical Devices": "hardware",
    "Medical Instruments & Supplies": "hardware",
    "Semiconductors": "hardware",
    "Semiconductor Equipment & Materials": "hardware",
    "Computer Hardware": "hardware",
    "Communication Equipment": "hardware",
    "Electronic Components": "hardware",
    "Electronics & Computer Distribution": "hardware",
    "Aerospace & Defense": "hardware",
    "Auto Parts": "hardware",
    "Specialty Industrial Machinery": "hardware",
    "Building Products & Equipment": "hardware",
    "Electrical Equipment & Parts": "hardware",
    "Scientific & Technical Instruments": "hardware",
    "Farm & Heavy Construction Machinery": "hardware",
    "Consumer Electronics": "hardware",
    "Tools & Accessories": "hardware",
    # marketplace -- platform penghubung penjual/pembeli, jelas
    "Internet Retail": "marketplace",
    "Financial Data & Stock Exchanges": "marketplace",
}


def infer_business_model(industry: str | None) -> str | None:
    """Infer BusinessModel dari industry Yahoo -- None kalau industry sendiri
    tidak diketahui, "other" (bukan None) kalau industry diketahui tapi bukan
    salah satu dari 4 kategori spesifik yang dipetakan di atas."""
    if not industry:
        return None
    return _INDUSTRY_BUSINESS_MODEL.get(industry, "other")


def _snapshot_date(snapshot) -> date | None:
    """Tanggal PriceTargetSnapshot sebagai objek `date`, None kalau tak terbaca.

    Akses atribut (bukan dict) — konsisten dengan pemakaian `.target_mean` di
    calculate_price_target_metrics; pemanggilnya selalu mengirim dataclass."""
    raw = getattr(snapshot, "date", None)
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def calculate_price_target_metrics(
    current_price: float | None,
    analyst_estimates: dict | None,
    price_target_history: list | None = None
) -> dict[str, float | None | str]:
    """Calculate analyst consensus price target metrics.

    Args:
        current_price: Current stock price
        analyst_estimates: Dict dengan target_mean, target_median, target_low, target_high,
                          num_analyst_opinions, recommendation_mean, recommendation_key
        price_target_history: List of PriceTargetSnapshot untuk calculate trend

    Returns:
        Dict dengan upside_pct, recommendation breakdown, trend_3m, dll
    """
    metrics = {
        'current_price': current_price,
        'target_mean': None,
        'target_median': None,
        'target_low': None,
        'target_high': None,
        'num_analysts': None,
        'upside_pct': None,
        'recommendation_key': None,
        'recommendation_pct_buy': None,
        'recommendation_pct_hold': None,
        'recommendation_pct_sell': None,
        'price_target_trend_3m': None
    }

    if not analyst_estimates or not current_price or current_price <= 0:
        return metrics

    metrics['target_mean'] = analyst_estimates.get('target_mean')
    metrics['target_median'] = analyst_estimates.get('target_median')
    metrics['target_low'] = analyst_estimates.get('target_low')
    metrics['target_high'] = analyst_estimates.get('target_high')
    metrics['num_analysts'] = analyst_estimates.get('num_analyst_opinions')
    metrics['recommendation_key'] = analyst_estimates.get('recommendation_key')

    if metrics['target_mean'] and metrics['target_mean'] > 0:
        metrics['upside_pct'] = ((metrics['target_mean'] - current_price) / current_price) * 100

    # Tren 3 BULAN, dibatasi berdasarkan TANGGAL. Versi sebelumnya memakai
    # price_target_history[0] (entri tertua yang ada) sebagai pembanding tanpa
    # filter tanggal apa pun — karena price_target.sync_price_target_history()
    # menambah satu snapshot per hari sampai MAX_SNAPSHOTS_PER_TICKER (400,
    # ~13 bulan), window-nya membesar terus setiap hari pipeline jalan. Setelah
    # 9 bulan berjalan, angka yang dilabeli "3M" sebenarnya perubahan 9 bulan,
    # sementara reasoning.py tetap mencetaknya sebagai "over 3M".
    if price_target_history and len(price_target_history) >= 2:
        latest = price_target_history[-1]
        cutoff = _snapshot_date(latest)
        baseline = None
        if cutoff is not None:
            target_date = cutoff - timedelta(days=90)
            # Snapshot terlama yang MASIH di dalam/dekat window 3 bulan:
            # ambil yang paling tua namun >= target_date; kalau tidak ada yang
            # memenuhi (histori masih pendek), pakai yang tertua yang ada asalkan
            # tidak lebih lama dari toleransi, supaya tidak diam-diam melebar.
            in_window = [s for s in price_target_history[:-1]
                         if (d := _snapshot_date(s)) is not None and d >= target_date]
            if in_window:
                baseline = in_window[0]
            else:
                oldest = price_target_history[0]
                d = _snapshot_date(oldest)
                if d is not None and (cutoff - d).days <= 90 + _ANCHOR_TOLERANCE_DAYS:
                    baseline = oldest
        if baseline is not None and baseline is not latest:
            if baseline.target_mean and latest.target_mean and baseline.target_mean > 0:
                metrics['price_target_trend_3m'] = (
                    (latest.target_mean - baseline.target_mean) / baseline.target_mean
                ) * 100

    return metrics
