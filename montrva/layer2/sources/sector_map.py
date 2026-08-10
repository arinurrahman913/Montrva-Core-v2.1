"""Sector reference map — GICS-style sector per ticker, dibangun sekali
(atau berkala via scripts/build_sector_map.py) lalu dipakai run_screening
(sector=...) untuk filter cepat tanpa fetch .info per-ticker di setiap
run harian.

Kenapa perlu file terpisah: Screening tidak tahu sektor sebuah ticker —
sektor baru diketahui di tahap Evidence lewat yf.Ticker().info['sector'],
panggilan yang sama persis yang di-rate-limit di sources/yahoo_evidence.py
karena mahal di skala 5000+ ticker. Kalau filter sektor langsung fetch
.info tiap run, tidak lebih cepat dari full scan yang mau dihindari.
Makanya klasifikasi sektor dipisah jadi proses tersendiri yang jarang
di-refresh (sektor perusahaan jarang berubah), hasilnya di-cache lama,
dipakai berulang oleh screening harian yang jadi cepat karena tinggal
baca cache, bukan fetch network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import yfinance as yf

from ...cache import CACHE_DIR, get as cache_get, set as cache_set
from ._retry import retry

SECTOR_MAP_TTL_SECONDS = 90 * 24 * 3600  # 90 hari — sektor jarang berubah

# 11 GICS sector standar (nama sesuai yang biasa dikembalikan Yahoo Finance).
# Dipakai frontend buat daftar tombol; run_screening() match case-insensitive
# jadi variasi kapitalisasi tidak masalah.
KNOWN_SECTORS = [
    "Technology", "Healthcare", "Financial Services", "Energy",
    "Industrials", "Consumer Cyclical", "Consumer Defensive", "Utilities",
    "Real Estate", "Basic Materials", "Communication Services",
]


def load_sector_map() -> dict[str, str]:
    """Baca sector map dari cache untuk dipakai filter. Return {} kalau
    belum pernah dibangun atau sudah lewat TTL (90 hari)."""
    cached = cache_get("sector_map", "global", SECTOR_MAP_TTL_SECONDS)
    if cached is None:
        return {}
    return cached.get("tickers", {})


def load_sector_map_meta() -> dict | None:
    """Metadata map (generated_at, coverage, tickers) TANPA TTL check —
    dipakai build_sector_map.py buat resume dari progress lama, dan dashboard
    buat nampilin status walau mapnya sudah 'stale' menurut TTL (lebih baik
    tampilkan data lama + kapan terakhir dibangun, daripada kosong)."""
    p = CACHE_DIR / "sector_map" / "global.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        return payload.get("data")
    except Exception:
        return None


def save_sector_map(tickers: dict[str, str]) -> None:
    coverage: dict[str, int] = {}
    for sector in tickers.values():
        coverage[sector] = coverage.get(sector, 0) + 1
    cache_set("sector_map", "global", {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_mapped": len(tickers),
        "coverage": coverage,
        "tickers": tickers,
    })


def seed_from_fundamental_cache() -> dict[str, str]:
    """Ambil sector dari cache fundamental_data yang SUDAH ADA (hasil Evidence
    run sebelumnya) — TTL diabaikan sengaja: sektor jarang berubah jadi entry
    lama tetap valid dipakai sebagai seed, menghindari fetch ulang untuk
    ticker yang datanya sudah pernah didapat."""
    out: dict[str, str] = {}
    d = CACHE_DIR / "fundamental_data"
    if not d.exists():
        return out
    for p in d.glob("*.json"):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            sector = payload.get("data", {}).get("sector")
            if sector:
                out[p.stem] = sector
        except Exception:
            continue
    return out


def fetch_sector(ticker: str) -> str | None:
    """Fetch sector satu ticker langsung dari Yahoo — dipakai builder untuk
    ticker yang belum ada di cache manapun. Caller (build_sector_map.py)
    yang atur batching/delay-nya."""
    try:
        # retry() (timeout 60s per percobaan) bukan cuma try/except polos --
        # sama kelas bug dengan yf.download() batch di screening.py (2026-07-28):
        # tanpa timeout, satu ticker yang nyangkut menahan seluruh proses build
        # (resumable, tapi tetap gak ada gunanya nunggu tanpa batas waktu).
        info = retry(lambda: yf.Ticker(ticker).info, retries=1,
                     backoff_seconds=0, label=f"sector:{ticker}")
        return info.get("sector")
    except Exception:
        return None
