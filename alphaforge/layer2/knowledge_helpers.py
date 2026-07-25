"""Helper functions untuk Knowledge calculations — tren, technical indicators, financial metrics."""
from __future__ import annotations

import math
from .contracts import PriceBar


def calculate_returns(price_history: list[PriceBar]) -> dict[str, float | None]:
    """Calculate returns dari price history.

    Returns: {
        'return_1y': % annual return, past 1 year (atau max available),
        'return_3y': % annual return (CAGR), past 3 years,
        'return_5y': % annual return (CAGR), past 5 years
    }

    Catatan: return_3y/return_5y butuh >=756/>=1260 trading days histori.
    Evidence saat ini cuma fetch `period="1y"` dari Yahoo (~252 bar) —
    lihat sources/yahoo_evidence.py fetch_price_market_data — jadi kedua
    field ini SELALU None untuk semua ticker sampai window fetch itu
    diperlebar. Ini keterbatasan struktural yang disengaja didokumentasikan
    di sini (bukan diperlebar sekarang — nambah histori 3-5x lipat per
    ticker berdampak ke biaya bandwidth/waktu Evidence di skala full-market,
    keputusan terpisah dari sekadar "fix bug kalkulasi").
    """
    if not price_history or len(price_history) < 2:
        return {'return_1y': None, 'return_3y': None, 'return_5y': None}

    # Bars sudah sorted chronologically, last = most recent
    prices_by_date = {bar.date: bar.close for bar in price_history}
    sorted_dates = sorted(prices_by_date.keys())

    latest_price = prices_by_date[sorted_dates[-1]]
    first_price = prices_by_date[sorted_dates[0]]

    if first_price <= 0:
        return {'return_1y': None, 'return_3y': None, 'return_5y': None}

    # Calculate based on available history
    total_days = len(sorted_dates) - 1
    total_return_pct = ((latest_price - first_price) / first_price) * 100

    # 1-year return. Kalau histori total <=365 hari, ya itu returnnya
    # (semua histori yang ada = window "1 tahun"). Kalau histori lebih
    # panjang, HARUS ambil harga dari ~365 hari lalu (bukan first_price,
    # yang bisa jadi dari bertahun-tahun lalu) — bug sebelumnya dulu pakai
    # first_price + _cagr(...,1) seolah-olah first_price itu persis 1 tahun
    # lalu, padahal bisa jauh lebih lama, menghasilkan angka annualized
    # yang salah total.
    if total_days <= 365:
        return_1y = total_return_pct
    else:
        idx_1y_ago = max(0, len(sorted_dates) - 366)
        price_1y_ago = prices_by_date[sorted_dates[idx_1y_ago]]
        return_1y = ((latest_price - price_1y_ago) / price_1y_ago) * 100 if price_1y_ago > 0 else None

    # 3-year CAGR (roughly 756 trading days = 3 years)
    price_3y_ago = None
    date_3y_ago = None
    if total_days >= 756:
        date_3y_ago = sorted_dates[max(0, len(sorted_dates) - 756)]
        price_3y_ago = prices_by_date[date_3y_ago]

    return_3y = _cagr(price_3y_ago, latest_price, 3) if price_3y_ago else None

    # 5-year CAGR (roughly 1260 trading days = 5 years)
    price_5y_ago = None
    if total_days >= 1260:
        price_5y_ago = prices_by_date[sorted_dates[max(0, len(sorted_dates) - 1260)]]

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


def calculate_volatility(price_history: list[PriceBar]) -> float | None:
    """Calculate daily volatility (std dev of daily returns %)."""
    if not price_history or len(price_history) < 2:
        return None

    daily_returns = []
    for i in range(1, len(price_history)):
        prev_close = price_history[i - 1].close
        curr_close = price_history[i].close
        if prev_close > 0:
            ret = ((curr_close - prev_close) / prev_close) * 100
            daily_returns.append(ret)

    if len(daily_returns) < 2:
        return None

    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
    std_dev = math.sqrt(variance)

    return std_dev


def calculate_high_low_52w(price_history: list[PriceBar]) -> dict[str, float | None]:
    """Calculate 52-week high/low dari price history."""
    if not price_history:
        return {'high_52w': None, 'low_52w': None}

    # 52-week = roughly 252 trading days
    relevant_bars = price_history[-252:] if len(price_history) >= 252 else price_history

    closes = [bar.close for bar in relevant_bars]
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

    # FCF yield
    if free_cash_flow and market_cap and market_cap > 0:
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
                if rev and prior_rev and prior_rev > 0:
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
                # CapEx as % revenue
                if hasattr(period, 'capital_expenditures') and getattr(period, 'capital_expenditures'):
                    capex = getattr(period, 'capital_expenditures')
                    trends[f"capex_pct_revenue_{q_key}"] = (capex / rev) * 100

    return trends


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

    if price_target_history and len(price_target_history) >= 2:
        oldest = price_target_history[0]
        latest = price_target_history[-1]
        if oldest.target_mean and latest.target_mean and oldest.target_mean > 0:
            metrics['price_target_trend_3m'] = (
                (latest.target_mean - oldest.target_mean) / oldest.target_mean
            ) * 100

    return metrics
