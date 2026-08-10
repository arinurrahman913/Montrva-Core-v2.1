"""On-demand live quote fetch — Level 3 "freshness": current price at the
moment a ticker's detail is opened, not the pre-generated Evidence snapshot.

Deliberately separate from yahoo_evidence.py's fetch_price_market_data():
that one pulls a full 1-year OHLCV history (slow, meant for the batch
pipeline with a 6h cache). This one only reads yfinance's fast_info (a
single lightweight quote lookup, no history download) and is meant to be
called synchronously from a web request, so it needs a hard timeout and a
short in-memory cache — yfinance's first call in a process can take up to
~30s (cold HTTP session), and without a timeout that would hang the
request. Not a replacement for Evidence; this only ever backs the "live"
badge on the ticker detail modal.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone

import yfinance as yf

_executor = ThreadPoolExecutor(max_workers=4)
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 30
_FETCH_TIMEOUT_SECONDS = 8


def _fetch_blocking(ticker: str) -> dict:
    fi = yf.Ticker(ticker).fast_info
    last = fi.get("lastPrice")
    prev = fi.get("previousClose") or fi.get("regularMarketPreviousClose")
    change_pct = ((last - prev) / prev * 100) if last is not None and prev else None
    return {
        "ticker": ticker,
        "last_price": last,
        "previous_close": prev,
        "change_pct": change_pct,
        "day_high": fi.get("dayHigh"),
        "day_low": fi.get("dayLow"),
        "market_cap": fi.get("marketCap"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "stale": False,
    }


def fetch_live_quote(ticker: str) -> dict:
    """Best-effort fresh quote. Never raises — returns {"stale": True, "error": ...}
    on timeout/failure so callers (the Flask endpoint) can fall back gracefully."""
    ticker = ticker.upper()
    now = time.time()

    cached = _cache.get(ticker)
    if cached and cached[0] > now:
        return cached[1]

    future = _executor.submit(_fetch_blocking, ticker)
    try:
        data = future.result(timeout=_FETCH_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        return {"ticker": ticker, "stale": True, "error": "timed out fetching live quote"}
    except Exception as e:
        return {"ticker": ticker, "stale": True, "error": str(e)}

    _cache[ticker] = (now + _CACHE_TTL_SECONDS, data)
    return data
