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


def reset_batch_tracking():
    """Reset batch counter (dipanggil di awal evidence run)."""
    global _batch_counter, _batch_last_time
    _batch_counter = 0
    _batch_last_time = None


def _apply_batch_delay():
    """Jeda tiap YF_EVIDENCE_BATCH_SIZE panggilan network — hanya dipanggil
    di jalur cache-miss supaya re-run yang kena cache tetap cepat."""
    global _batch_counter, _batch_last_time
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


def fetch_price_market_data(ticker: str) -> PriceMarketData:
    """Ambil harga & 1-year historical OHLCV dari Yahoo Finance (cached 6h)."""
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
            price_history=[PriceBar(**b) for b in cached.get("price_history", [])]
        )

    try:
        _apply_batch_delay()

        def _do_fetch():
            t = yf.Ticker(ticker)
            fi = t.fast_info
            hist = t.history(period="1y")
            if hist is None or hist.empty:
                raise ValueError(f"no price data for {ticker}")
            return fi, hist

        fi, hist = retry(_do_fetch, retries=YF_EVIDENCE_RETRIES,
                          backoff_seconds=YF_EVIDENCE_RETRY_BACKOFF_SECONDS,
                          label=f"yahoo_price:{ticker}")

        last_price = fi.get("lastPrice") or (hist["Close"].iloc[-1] if not hist.empty else None)
        market_cap = fi.get("marketCap")
        shares_outstanding = fi.get("shares")
        beta = fi.get("beta")

        # OHLCV dari bar terakhir
        open_price = float(hist["Open"].iloc[-1]) if not hist.empty else None
        high = float(hist["High"].iloc[-1]) if not hist.empty else None
        low = float(hist["Low"].iloc[-1]) if not hist.empty else None
        close = float(hist["Close"].iloc[-1]) if not hist.empty else None
        volume = int(hist["Volume"].iloc[-1]) if not hist.empty else None

        # 52-week high/low
        high_52w = float(hist["High"].max()) if not hist.empty else None
        low_52w = float(hist["Low"].min()) if not hist.empty else None

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

        to_cache = asdict(result)
        to_cache["_metadata"] = {"source": metadata.source, "fetched_at": metadata.fetched_at, "status": metadata.status}
        del to_cache["metadata"]
        cache_set("price_market_data", ticker, to_cache)

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
            industry=cached.get("industry")
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
        return FundamentalData(
            metadata=metadata,
            revenue=None, net_income=None, eps=None, pe_ratio=None,
            debt_to_equity=None, current_ratio=None, quick_ratio=None,
            roe=None, roa=None, operating_margin=None, gross_margin=None,
            free_cash_flow=None, dividend_yield=None, payout_ratio=None,
            book_value_per_share=None, asset_turnover=None, inventory_turnover=None,
            interest_coverage=None
        )


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
            top_holders=[InstitutionalHolder(**h) for h in cached.get("top_holders", [])]
        )

    try:
        info = _fetch_yahoo_info(ticker)
        percentage = _safe_float(info.get("heldPercentInstitutions"))

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

        data = InstitutionalOwnership(metadata=metadata, percentage=percentage, top_holders=top_holders)

        # Cache
        to_cache = {
            "percentage": percentage,
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
            "_metadata": {"source": metadata.source, "fetched_at": metadata.fetched_at, "status": metadata.status},
        })

        return data
    except Exception as e:
        print(f"[yahoo_analyst_estimates:{ticker}] gagal (post-processing/final): {e}", file=sys.stderr)
        return AnalystEstimates(metadata=SourceMetadata(
            source="yahoo_finance", fetched_at=datetime.now(timezone.utc).isoformat(), status="missing"
        ))
