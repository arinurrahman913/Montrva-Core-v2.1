"""Sumber sentimen tambahan untuk market_sentiment.

Baik CBOE put/call ratio maupun AAII Investor Sentiment Survey tidak punya API
resmi gratis (lihat 04_DATA_SOURCES/01_PROVIDERS_OVERVIEW.md §2b; CBOE dicoba
langsung dan membalas 403). Solusi yang dipakai sekarang: KEDUANYA input manual
via `dashboard/data/sentiment_manual.json` (diisi pengguna dari cboe.com /
aaii.com). Absen/rusak/kadaluarsa → tidak dipakai, dilaporkan hilang dengan jujur.

`fetch_put_call()` di bawah ini menarik put/call best-effort dari endpoint
publik CNN Fear & Greed — TIDAK RESMI (bukan API terdokumentasi CNN, cuma URL
yang dipanggil halaman web mereka). Fungsi ini SENGAJA TIDAK DIPANGGIL dari
market_sentiment.py (dimatikan atas permintaan pengguna — proyek ini memilih
hanya sumber resmi walau berarti market_sentiment lebih sering degraded).
Dibiarkan ada (dormant) supaya gampang diaktifkan lagi kalau kebutuhannya
berubah — tinggal panggil dari market_sentiment.py lagi.

Semua skor pada skala 0–100 dengan konvensi yang sama seperti market_sentiment:
tinggi = greed / risk-on, rendah = fear.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ... import cache

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
# Header lengkap ala browser — tanpa ini CNN membalas HTTP 418 (blokir bot).
CNN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cnn.com/markets/fear-and-greed",
    "Origin": "https://www.cnn.com",
}
CACHE_NS = "sentiment"
PUT_CALL_TTL = 12 * 3600
MANUAL_PATH = Path(__file__).resolve().parents[3] / "dashboard" / "data" / "sentiment_manual.json"
AAII_MAX_AGE_DAYS = 30

# --- Sumber OTOMATIS resmi tambahan (CFTC + FINRA) -----------------------------
# Beda dari CNN di atas: keduanya sumber pemerintah/regulator resmi dengan data
# publik gratis tanpa API key, jadi AKTIF DIPANGGIL dari market_sentiment.py
# (sejalan prinsip "hanya sumber resmi"). Hasilnya di-cache (bukan file mentah,
# cuma skor kecil) supaya tidak nembak jaringan tiap refresh.
CFTC_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"  # Legacy COT, futures-only
CFTC_TTL = 12 * 3600            # rilis mingguan (Jumat), 12 jam lebih dari cukup
FINRA_SHORT_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
FINRA_TTL = 12 * 3600          # file harian
# Kalibrasi FINRA short-volume → skala greed (tinggi=greed). Short volume
# konsolidasi mencakup hedging market-maker (bukan murni taruhan bearish), jadi
# ini PROKSI kasar: netral di ~48% short, tiap 1pp geser skor 2.5 poin. Konstanta
# gampang di-tune di sini kalau mau.
FINRA_NEUTRAL_SHORT_PCT = 48.0
FINRA_SLOPE = 2.5


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def fetch_cftc_spx() -> dict | None:
    """Positioning spekulan (noncommercial) di futures E-mini S&P 500 dari
    laporan CFTC Commitments of Traders (resmi, mingguan, gratis via Socrata).

    Skor 0-100 = long / (long + short) × 100 — analog dengan konvensi AAII
    bull/(bull+bear): >50 spekulan net long (greed), <50 net short (fear).
    Return {'score_0_100', 'as_of', 'long', 'short'} atau None (best-effort,
    cached 12 jam)."""
    cached = cache.get(CACHE_NS, "cftc_spx", CFTC_TTL)
    if cached is not None:
        return cached
    params = {
        "$where": "market_and_exchange_names like '%E-MINI S&P 500%' "
                  "AND market_and_exchange_names not like '%MICRO%'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": "1",
    }
    try:
        r = requests.get(CFTC_URL, params=params, headers=CNN_HEADERS, timeout=8)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        row = rows[0]
        long_ = int(row["noncomm_positions_long_all"])
        short = int(row["noncomm_positions_short_all"])
        if long_ + short <= 0:
            return None
        data = {
            "score_0_100": long_ / (long_ + short) * 100.0,
            "as_of": str(row["report_date_as_yyyy_mm_dd"])[:10],
            "long": long_,
            "short": short,
        }
    except Exception:
        return None
    cache.set(CACHE_NS, "cftc_spx", data)
    return data


def fetch_finra_short_volume() -> dict | None:
    """Rasio short-volume pasar (seluruh saham NMS) dari file harian FINRA
    Reg SHO (resmi, gratis). Short volume tinggi → bearish, jadi DIBALIK ke
    skala greed. Ini PROKSI kasar (lihat catatan kalibrasi di atas).

    Return {'score_0_100', 'as_of', 'short_ratio_pct'} atau None. Coba beberapa
    hari bursa terakhir mundur (file belum tentu ada utk hari ini/akhir pekan).
    Best-effort, cached 12 jam."""
    cached = cache.get(CACHE_NS, "finra_short", FINRA_TTL)
    if cached is not None:
        return cached
    today = datetime.now(timezone.utc).date()
    # Maksimal 4 percobaan mundur (hari ini + 3 hari bursa lalu) × timeout 8s
    # = worst-case ~32 dtk, bukan ~90 dtk. Best-effort: gagal → input hilang.
    for back in range(0, 4):
        d = today - timedelta(days=back)
        url = FINRA_SHORT_URL.format(date=d.strftime("%Y%m%d"))
        try:
            r = requests.get(url, headers=CNN_HEADERS, timeout=8)
        except Exception:
            continue
        if r.status_code != 200 or len(r.content) < 200:
            continue
        tot_short = 0.0
        tot_vol = 0.0
        for line in r.text.splitlines()[1:]:  # skip header
            parts = line.split("|")
            if len(parts) < 5:
                continue
            try:
                tot_short += float(parts[2])
                tot_vol += float(parts[4])
            except ValueError:
                continue
        if tot_vol <= 0:
            continue
        short_ratio = tot_short / tot_vol * 100.0
        score = _clamp(50.0 + (FINRA_NEUTRAL_SHORT_PCT - short_ratio) * FINRA_SLOPE)
        data = {"score_0_100": score, "as_of": d.strftime("%Y-%m-%d"), "short_ratio_pct": short_ratio}
        cache.set(CACHE_NS, "finra_short", data)
        return data
    return None


def fetch_put_call() -> dict | None:
    """{'score_0_100': float, 'as_of': 'YYYY-MM-DD'} atau None (best-effort, cached 12 jam)."""
    cached = cache.get(CACHE_NS, "cnn_put_call", PUT_CALL_TTL)
    if cached is not None:
        return cached
    try:
        r = requests.get(CNN_URL, headers=CNN_HEADERS, timeout=10)
        r.raise_for_status()
        pc = r.json()["put_call_options"]
        score = float(pc["score"])
        ts = pc.get("timestamp")
        if ts:
            as_of = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        else:
            as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = {"score_0_100": score, "as_of": as_of}
    except Exception:
        return None
    cache.set(CACHE_NS, "cnn_put_call", data)
    return data


def _fresh_enough(as_of: str) -> bool:
    if not as_of:
        return True  # tanpa tanggal, percayakan ke pengguna
    try:
        d = datetime.strptime(as_of[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return True
    return (datetime.now(timezone.utc) - d).days <= AAII_MAX_AGE_DAYS


def read_manual() -> dict:
    """Baca override manual dari MANUAL_PATH. Return dict yang bisa berisi
    key 'aaii' dan/atau 'put_call' (masing-masing {'score_0_100', 'as_of', ...}).
    File absen/rusak/kadaluarsa → {}."""
    if not MANUAL_PATH.exists():
        return {}
    try:
        raw = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

    out: dict = {}
    aaii = raw.get("aaii")
    if isinstance(aaii, dict):
        bull, bear = aaii.get("bullish_pct"), aaii.get("bearish_pct")
        as_of = aaii.get("as_of", "")
        if isinstance(bull, (int, float)) and isinstance(bear, (int, float)) and (bull + bear) > 0 and _fresh_enough(as_of):
            out["aaii"] = {
                "score_0_100": bull / (bull + bear) * 100.0,
                "bullish_pct": bull,
                "bearish_pct": bear,
                "as_of": as_of,
            }

    pcs = raw.get("put_call_score")
    if isinstance(pcs, (int, float)):
        out["put_call"] = {"score_0_100": float(pcs), "as_of": raw.get("put_call_as_of", "")}

    return out
