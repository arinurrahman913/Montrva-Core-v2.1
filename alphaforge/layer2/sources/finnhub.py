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
import sys
import time
from pathlib import Path

import requests
from datetime import datetime, timedelta, timezone
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

_last_call_time = None


def reset_batch_tracking():
    """Reset rate-limit tracking (dipanggil di awal evidence run)."""
    global _last_call_time
    _last_call_time = None


def _apply_rate_limit():
    """Jeda minimal antar SETIAP panggilan (bukan per-batch) — beneran
    membatasi throughput di bawah 60 req/menit, bukan cuma jeda periodik
    yang bisa kelewat."""
    global _last_call_time
    now = time.time()
    if _last_call_time is not None:
        elapsed = now - _last_call_time
        if elapsed < FINNHUB_MIN_INTERVAL_SECONDS:
            time.sleep(FINNHUB_MIN_INTERVAL_SECONDS - elapsed)
    _last_call_time = time.time()


def fetch_company_news(ticker: str, lookback_days: int = 30) -> NewsCollection:
    """Ambil berita terkini dari Finnhub — dengan rate limit handling."""
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

        return NewsCollection(news=news_list, metadata=metadata)

    except requests.exceptions.RequestException as exc:
        print(f"[finnhub:{ticker}] gagal (final): {exc}", file=sys.stderr)
        metadata = SourceMetadata(
            source="finnhub",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="missing"
        )
        return NewsCollection(news=[], metadata=metadata)
