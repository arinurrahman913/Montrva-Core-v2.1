"""Wrapper tipis di atas yfinance. Titik kegagalan tunggal terbesar sistem
(lihat 04_DATA_SOURCES/01_PROVIDERS_OVERVIEW.md §2b) — semua pemanggil wajib
menangkap exception dan mengubahnya jadi status=missing, bukan membiarkan
pipeline berhenti.

Sebelumnya tidak ada caching sama sekali di sini — beda dengan Layer 2
Evidence yang punya kebijakan cache eksplisit (6 jam price, 24 jam
fundamental). Setiap run Layer 1 penuh melakukan ~40+ panggilan Yahoo tanpa
cache (sector_rotation saja 24 kali), jadi rerun yang sering (mis. refresh
dashboard berkala) beresiko kena rate limit dan lebih lambat dari perlu.
history() sekarang cache 6 jam, selaras dengan TTL price Layer 2.
"""
from __future__ import annotations

import io

import yfinance as yf
import pandas as pd

from ... import cache
from ...layer2.sources._retry import retry

HISTORY_CACHE_TTL = 6 * 3600  # 6 jam, selaras PRICE_CACHE_TTL Layer 2 Evidence


def history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    cache_key = f"{ticker}_{period}_{interval}"
    cached_json = cache.get("layer1_yahoo_history", cache_key, HISTORY_CACHE_TTL)
    if cached_json is not None:
        df = pd.read_json(io.StringIO(cached_json), orient="split")
        df.index = pd.to_datetime(df.index)
        return df

    # retry() (timeout 60s per percobaan lewat worker thread) -- bukan cuma
    # panggilan polos. Ditemukan live 2026-07-28: yf.download() batch tanpa
    # timeout bisa nyangkut 20+ menit tanpa exception; Layer 1 melakukan
    # ~40+ panggilan Yahoo tanpa cache per run (lihat docstring modul), jadi
    # rentan kelas bug yang sama kalau satu ticker saja macet.
    df = retry(lambda: yf.Ticker(ticker).history(period=period, interval=interval),
               retries=1, backoff_seconds=0, label=f"layer1_history:{ticker}")
    if df is None or df.empty:
        raise ValueError(f"no data returned for {ticker}")

    cache.set(
        "layer1_yahoo_history",
        cache_key,
        df.to_json(orient="split", date_format="iso", double_precision=15),
    )
    return df


def last_close(ticker: str, period: str = "5d") -> float:
    df = history(ticker, period=period)
    return float(df["Close"].iloc[-1])


def pct_change(ticker: str, days: int, period: str = "1y") -> float:
    """Perubahan persentase close hari terakhir vs `days` hari (kalender bursa) sebelumnya."""
    df = history(ticker, period=period)
    if len(df) <= days:
        raise ValueError(f"insufficient history for {ticker}: {len(df)} rows, need > {days}")
    last = float(df["Close"].iloc[-1])
    prior = float(df["Close"].iloc[-1 - days])
    return (last - prior) / prior * 100.0
