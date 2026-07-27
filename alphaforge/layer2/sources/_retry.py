"""Retry+backoff+logging helper dipakai semua sources/* — pola yang sama
persis dipakai di screening.py & listing.py (lihat commit fix Screening),
diekstrak ke sini karena Evidence butuh pola identik di 6+ tempat berbeda
(Yahoo x3, Finnhub, SEC EDGAR, SEC parser). Kegagalan sekarang selalu ada
jejaknya di stderr, bukan silently downgrade ke status="missing" tanpa
penjelasan sama sekali soal sumber mana yang gagal dan kenapa.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError
from typing import Callable, TypeVar

T = TypeVar("T")

# Seen live: a yfinance call that never raised and never returned — no
# exception for the except/retry loop below to even catch — stalled a full
# pipeline run for 40+ minutes. fn() runs in a worker thread so retry() can
# bound the wait with future.result(timeout=...); Python has no API to kill
# a running thread, so a stuck fn() is simply abandoned (it finishes or
# stays blocked on its own time, off the critical path) while retry() moves
# on to the next attempt instead of hanging with it.
DEFAULT_TIMEOUT_SECONDS = 60.0
_executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="retry-fetch")


def retry(fn: Callable[[], T], *, retries: int, backoff_seconds: float, label: str,
          timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> T:
    """Panggil fn() sampai `retries` kali (backoff linear antar percobaan,
    tidak dipanggil setelah percobaan terakhir), tiap percobaan dibatasi
    `timeout_seconds`. Kalau semua gagal (atau timeout), log ke stderr lalu
    re-raise exception terakhir — caller yang putuskan mau fallback ke
    status="missing" atau propagate lebih jauh."""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            future = _executor.submit(fn)
            return future.result(timeout=timeout_seconds)
        except _FutureTimeoutError:
            last_exc = TimeoutError(f"{label} timed out after {timeout_seconds}s")
            if attempt < retries:
                print(f"[{label}] percobaan {attempt}/{retries} timeout ({timeout_seconds}s) — retry dalam {backoff_seconds}s",
                      file=sys.stderr)
                time.sleep(backoff_seconds)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                print(f"[{label}] percobaan {attempt}/{retries} gagal: {exc} — retry dalam {backoff_seconds}s",
                      file=sys.stderr)
                time.sleep(backoff_seconds)
    print(f"[{label}] gagal total setelah {retries}x percobaan: {last_exc}", file=sys.stderr)
    raise last_exc
