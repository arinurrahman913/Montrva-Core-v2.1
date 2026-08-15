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
import sys
import threading

import yfinance as yf
import pandas as pd

from ... import cache
from ...layer2.sources._retry import retry

HISTORY_CACHE_TTL = 6 * 3600  # 6 jam, selaras PRICE_CACHE_TTL Layer 2 Evidence

# Dulu retries=1 -- yang berarti SATU percobaan tanpa retry sama sekali (loop
# di retry() berjalan `range(1, retries+1)`). Satu kali menggantung 60 detik =
# komponen itu hilang untuk seluruh run, tanpa percobaan kedua. Terjadi nyata
# 14 Agu 2026 02:15: 6 dari 13 komponen (Commodity, DXY, Market Regime, Money
# Flow, Sector Rotation, VIX) serentak MISSING karena gangguan jaringan
# beberapa menit; run ulang 15 menit kemudian langsung 13/13 ok. Layer 2 sudah
# dapat retry+backoff sejak sprint 2026-07-21, Layer 1 tidak pernah ikut.
HISTORY_RETRIES = 2
HISTORY_BACKOFF_SECONDS = 3.0

# Batas umur cache yang masih boleh dipakai sebagai jaring pengaman. Bukan
# angka kosmetik: di ATAS ini, deret yang dikembalikan sudah cukup tua sehingga
# `data_freshness` komponennya akan jatuh ke "stale" sendiri (cadence harian:
# >3 hari), jadi tidak ada gunanya menyajikannya seolah-olah bacaan pasar hari
# ini -- lebih jujur MISSING.
STALE_FALLBACK_MAX_AGE = 3 * 24 * 3600

# Pencatat per-THREAD, dan itu penting: pipeline.py menjalankan ke-12 komponen
# leaf di ThreadPoolExecutor terpisah (satu komponen = satu thread), jadi
# thread-local memetakan "fetch mana yang jatuh ke cache basi" ke KOMPONEN yang
# memanggilnya tanpa perlu menebak-nebak dari nama ticker di teks evidence --
# pencocokan string ke format produsen persis kelas bug yang sudah 6 kali kena
# di proyek ini. Pemanggil di luar capture (mis. _get_spx_validation_history di
# thread utama) otomatis tidak mencatat apa-apa.
_capture = threading.local()


def begin_capture() -> None:
    """Mulai mencatat fallback cache basi untuk thread ini."""
    _capture.entries = []


def pop_capture() -> list[tuple[str, float]]:
    """Ambil & hentikan catatan thread ini -> [(ticker, umur_detik), ...]."""
    entries = getattr(_capture, "entries", None)
    _capture.entries = None
    return entries or []


def _record_stale(ticker: str, age_seconds: float) -> None:
    entries = getattr(_capture, "entries", None)
    if entries is not None:
        entries.append((ticker, age_seconds))


def _to_frame(payload: str) -> pd.DataFrame:
    df = pd.read_json(io.StringIO(payload), orient="split")
    df.index = pd.to_datetime(df.index)
    return df


def history(ticker: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    cache_key = f"{ticker}_{period}_{interval}"
    cached_json = cache.get("layer1_yahoo_history", cache_key, HISTORY_CACHE_TTL)
    if cached_json is not None:
        return _to_frame(cached_json)

    # retry() (timeout 60s per percobaan lewat worker thread) -- bukan cuma
    # panggilan polos. Ditemukan live 2026-07-28: yf.download() batch tanpa
    # timeout bisa nyangkut 20+ menit tanpa exception; Layer 1 melakukan
    # ~40+ panggilan Yahoo tanpa cache per run (lihat docstring modul), jadi
    # rentan kelas bug yang sama kalau satu ticker saja macet.
    try:
        df = retry(lambda: yf.Ticker(ticker).history(period=period, interval=interval),
                   retries=HISTORY_RETRIES, backoff_seconds=HISTORY_BACKOFF_SECONDS,
                   label=f"layer1_history:{ticker}")
        if df is None or df.empty:
            raise ValueError(f"no data returned for {ticker}")
    except Exception:
        # Jaring pengaman yang SUDAH ADA tapi tidak pernah dipakai di sini:
        # cache.get_stale() dibuat untuk listing.py (`8ee63bb`) persis untuk
        # kasus ini. Pada kegagalan 14 Agu, deret yang dibutuhkan keenam
        # komponen itu tersimpan lengkap di .cache/layer1_yahoo_history/
        # berumur 21 jam -- TTL 6 jam sudah lewat, jadi datanya diabaikan dan
        # komponennya dilaporkan MISSING. Deret harga 3-5 tahun yang berumur
        # semalam masih deret yang sama; yang berubah cuma satu bar terakhir.
        stale = cache.get_stale("layer1_yahoo_history", cache_key)
        if stale is None:
            raise
        payload, age = stale
        if age > STALE_FALLBACK_MAX_AGE:
            raise
        _record_stale(ticker, age)
        print(f"[layer1_history:{ticker}] fetch gagal — memakai cache berumur "
              f"{age / 3600:.1f} jam sebagai jaring pengaman", file=sys.stderr)
        return _to_frame(payload)

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
