"""Fetch company news dari Finnhub dengan rate limiting.

Free tier: 60 req/menit.

Lihat 03_LAYER2_SPECS/02_EVIDENCE.md §1.4.

Rate limiter sebelumnya nge-jeda tiap N panggilan ("batch"), bukan tiap
panggilan — bocor: N panggilan sekuensial di full-market run udah makan
waktu lebih lama dari jeda-nya sendiri (masing-masing ~150-300ms, N=30
panggilan = 4.5-9 detik) sebelum delay sempat "nyala", jadi throughput
efektif bisa nembus limit 60/menit yang free tier. Diganti minimum-interval
antar SETIAP panggilan — cara yang benar-benar membatasi rate, bukan
sekadar jeda periodik.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path

import requests
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from ...cache import get as cache_get, set as cache_set
from ..contracts import SourceMetadata, CompanyNews, NewsCollection
from ._retry import retry

# Auto-load dari .env di root repo (gitignored) — sebelumnya modul ini TIDAK
# panggil load_dotenv() sendiri, cuma numpang kebetulan kalau
# layer1/sources/fred.py (yang punya load_dotenv()) sempat ter-import lebih
# dulu. Itu fragile: kalau finnhub.py di-import duluan (mis. lewat
# `alphaforge.layer2.evidence`), FINNHUB_API_KEY ke-baca None walau .env
# sudah diisi, dan module-level constant di bawah tidak pernah dibaca ulang.
# Sekarang self-contained, sama seperti fred.py.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
except ImportError:
    pass

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
# 60 req/menit -> minimal ~1.0s antar panggilan; 1.05s kasih sedikit buffer.
FINNHUB_MIN_INTERVAL_SECONDS = float(os.environ.get("FINNHUB_MIN_INTERVAL_SECONDS", "1.05"))
FINNHUB_RETRIES = 2
FINNHUB_RETRY_BACKOFF_SECONDS = 3.0
# Lihat docstring fetch_company_news untuk kenapa 12 jam.
FINNHUB_NEWS_CACHE_TTL = float(os.environ.get("FINNHUB_NEWS_CACHE_TTL", 12 * 3600))

_last_call_time = None
# Evidence sekarang fetch multi-ticker concurrent (lihat evidence.py
# EVIDENCE_WORKERS) — tanpa lock ini, check-then-sleep di bawah adalah race
# condition klasik: 2+ thread bisa baca _last_call_time yang sama, sama-sama
# lolos cek "elapsed >= MIN_INTERVAL", lalu request beneran nembus limit
# 60/menit Finnhub. Lock bikin seluruh check+sleep+update atomic, jadi
# rate-limit tetap dihormati SECARA GLOBAL walau banyak thread motret ticker
# yang beda-beda bersamaan.
_lock = threading.Lock()


def _redact(value) -> str:
    """Buang API key dari teks apa pun sebelum dicetak/di-log.

    API key Finnhub dikirim sebagai query string (`token=...`), dan
    `requests` memformat HTTPError sebagai "401 Client Error: Unauthorized
    for url: <URL LENGKAP>" — termasuk token-nya. Teks itu dulu dicetak apa
    adanya ke stderr, lalu (sejak backend menyimpan stderr penuh saat gagal)
    ikut tertulis plaintext ke logs/refresh_failure_*.log DAN bisa muncul di
    /api/refresh/status, endpoint tanpa autentikasi pada server yang
    mendengarkan di 0.0.0.0 — jadi kredensial hidup bisa terbaca di layar.
    Kalau key kedaluwarsa, ini terjadi 4065 kali dalam satu run.
    """
    text = str(value)
    if FINNHUB_API_KEY:
        text = text.replace(FINNHUB_API_KEY, "***REDACTED***")
    # Jaring pengaman kalau URL membawa token lain (mis. setelah redirect).
    return re.sub(r"(token=)[^&\s]+", r"\1***REDACTED***", text)


def reset_batch_tracking():
    """Reset rate-limit tracking (dipanggil di awal evidence run)."""
    global _last_call_time
    with _lock:
        _last_call_time = None


def _apply_rate_limit():
    """Jeda minimal antar SETIAP panggilan (bukan per-batch) — beneran
    membatasi throughput di bawah 60 req/menit, bukan cuma jeda periodik
    yang bisa kelewat."""
    global _last_call_time
    with _lock:
        now = time.time()
        if _last_call_time is not None:
            elapsed = now - _last_call_time
            if elapsed < FINNHUB_MIN_INTERVAL_SECONDS:
                time.sleep(FINNHUB_MIN_INTERVAL_SECONDS - elapsed)
        _last_call_time = time.time()


def fetch_company_news(ticker: str, lookback_days: int = 30) -> NewsCollection:
    """Ambil berita terkini dari Finnhub — dengan cache + rate limit handling.

    CACHE (baru 2026-07-30) adalah pengubah durasi terbesar di seluruh pipeline.
    Sebelumnya ini SATU-SATUNYA sumber tanpa cache sama sekali: setiap run
    full-market menembak 4062 permintaan, dan karena free tier Finnhub dibatasi
    60 req/menit, rate limiter global (`_apply_rate_limit`, 1.05s per panggilan)
    memaksa tahap Evidence memakan ~71 menit — lantai keras yang TIDAK bisa
    ditembus paralelisasi, karena batasnya per-provider, bukan per-thread.
    Terukur: Evidence 74 menit pada run 2026-07-30, hanya 3 menit di atas lantai
    itu — artinya concurrency 5-thread sudah nyaris sempurna dan sisa waktunya
    murni menunggu Finnhub.

    TTL 12 jam aman secara makna: jendela berita yang diminta 30 hari ke belakang
    dan pipeline dijalankan harian, jadi isinya praktis tidak berubah dalam
    setengah hari. Ini membuat run ulang dalam 12 jam melewati ~71 menit itu
    sepenuhnya.
    """
    cached = cache_get("finnhub_news", ticker, FINNHUB_NEWS_CACHE_TTL)
    if cached is not None:
        meta = cached.get("_metadata", {})
        return NewsCollection(
            news=[CompanyNews(**n) for n in cached.get("news", [])],
            metadata=SourceMetadata(**meta) if meta else SourceMetadata(
                source="finnhub", fetched_at=datetime.now(timezone.utc).isoformat(), status="ok"
            ),
        )

    if not FINNHUB_API_KEY:
        metadata = SourceMetadata(
            source="finnhub",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="missing"
        )
        return NewsCollection(news=[], metadata=metadata)

    try:
        _apply_rate_limit()

        to_date = datetime.now(timezone.utc).date().isoformat()
        from_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date().isoformat()

        url = f"{FINNHUB_BASE_URL}/company-news"
        params = {
            "symbol": ticker,
            "from": from_date,
            "to": to_date,
            "token": FINNHUB_API_KEY,
        }

        def _do_fetch():
            r = requests.get(url, params=params, timeout=10)
            # 429 = rate limited — layak diretry (mungkin transient burst);
            # 403 = premium-only endpoint, tidak akan pernah berhasil walau
            # diretry, jadi diperlakukan beda (lihat bawah).
            if r.status_code == 429:
                raise requests.exceptions.RequestException(f"429 rate limited untuk {ticker}")
            return r

        resp = retry(_do_fetch, retries=FINNHUB_RETRIES,
                     backoff_seconds=FINNHUB_RETRY_BACKOFF_SECONDS,
                     label=f"finnhub:{ticker}")

        if resp.status_code == 403:
            metadata = SourceMetadata(
                source="finnhub",
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="missing"
            )
            return NewsCollection(news=[], metadata=metadata)

        resp.raise_for_status()
        data = resp.json()

        news_list = []
        for item in data:
            news_id = item.get("id")
            news_list.append(CompanyNews(
                headline=item.get("headline", ""),
                source=item.get("source", ""),
                published_at=datetime.fromtimestamp(item.get("datetime", 0), tz=timezone.utc).isoformat(),
                url=item.get("url"),
                evidence_id=str(news_id) if news_id is not None else None,
            ))

        metadata = SourceMetadata(
            source="finnhub",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="ok" if news_list else "degraded"
        )

        # Hasil "degraded" (nihil berita) ikut di-cache: itu jawaban yang sah dan
        # justru kasus paling sering pada ticker kecil — tanpa ini, ticker-ticker
        # itulah yang terus-terusan menghabiskan kuota rate limit setiap run.
        cache_set("finnhub_news", ticker, {
            "news": [asdict(n) for n in news_list],
            "_metadata": {"source": metadata.source, "fetched_at": metadata.fetched_at,
                          "status": metadata.status},
        })

        return NewsCollection(news=news_list, metadata=metadata)

    except requests.exceptions.RequestException as exc:
        print(f"[finnhub:{ticker}] gagal (final): {_redact(exc)}", file=sys.stderr)
        metadata = SourceMetadata(
            source="finnhub",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="missing"
        )
        return NewsCollection(news=[], metadata=metadata)
