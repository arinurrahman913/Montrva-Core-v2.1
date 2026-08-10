"""Retry+backoff+logging helper dipakai semua sources/* — pola yang sama
persis dipakai di screening.py & listing.py (lihat commit fix Screening),
diekstrak ke sini karena Evidence butuh pola identik di 6+ tempat berbeda
(Yahoo x3, Finnhub, SEC EDGAR, SEC parser). Kegagalan sekarang selalu ada
jejaknya di stderr, bukan silently downgrade ke status="missing" tanpa
penjelasan sama sekali soal sumber mana yang gagal dan kenapa.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError
from typing import Callable, TypeVar

T = TypeVar("T")

# Beberapa penyedia menaruh kredensial di query string (Finnhub: `token=`,
# FRED: `api_key=`). `requests` menuliskan URL LENGKAP ke dalam pesan
# HTTPError/ConnectionError, dan helper ini mencetak pesan itu ke stderr —
# yang sejak 2026-07-30 disimpan utuh ke logs/refresh_failure_*.log dan bisa
# ikut muncul di /api/refresh/status. Disaring di sini supaya berlaku untuk
# SEMUA pemanggil, bukan hanya modul yang ingat menyaring sendiri.
_CREDENTIAL_QS = re.compile(r"((?:token|api_key|apikey|key)=)[^&\s]+", re.IGNORECASE)


def _safe(exc) -> str:
    return _CREDENTIAL_QS.sub(r"\1***REDACTED***", str(exc))

# Seen live: a yfinance call that never raised and never returned — no
# exception for the except/retry loop below to even catch — stalled a full
# pipeline run for 40+ minutes. fn() runs in a worker thread so retry() can
# bound the wait with future.result(timeout=...); Python has no API to kill
# a running thread, so a stuck fn() is simply abandoned (it finishes or
# stays blocked on its own time, off the critical path) while retry() moves
# on to the next attempt instead of hanging with it.
DEFAULT_TIMEOUT_SECONDS = 60.0
_POOL_SIZE = 32
# Audit item C4: ThreadPoolExecutor TIDAK PUNYA cara membunuh worker yang
# sedang jalan -- begitu future.result(timeout=...) menyerah pada satu
# fn() yang macet, thread di baliknya tetap hidup, terjebak, SELAMANYA
# menempati satu slot dari _POOL_SIZE. Kerugian menumpuk sepanjang run
# panjang (pernah teramati: satu panggilan yfinance macet 40+ menit tanpa
# exception); kalau cukup banyak slot habis, retry() berikutnya SELALU
# timeout (tidak ada worker bebas untuk dijalankan), bukan cuma lambat.
# Begitu >= _MAX_ABANDONED_BEFORE_RECYCLE submission dianggap ditinggalkan
# (timeout, bukan exception biasa -- exception biasa berarti workernya
# BERES, cuma hasilnya gagal, itu bukan kebocoran), pool lama diganti pool
# baru. Pool lama TIDAK di-shutdown(wait=True) -- itu akan menunggu worker
# yang justru tidak akan pernah selesai. Dibiarkan begitu saja: Python
# garbage-collect objek executor-nya begitu tidak ada referensi tersisa;
# thread yang macet di dalamnya tetap hidup sebagai thread yatim piatu
# (tidak berbahaya untuk proses lanjut jalan, cuma memori kecil per thread).
_MAX_ABANDONED_BEFORE_RECYCLE = 8

_pool_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=_POOL_SIZE, thread_name_prefix="retry-fetch")
_abandoned_count = 0


def _submit(fn: Callable[[], T]):
    with _pool_lock:
        return _executor.submit(fn)


def _note_abandoned(label: str) -> None:
    global _executor, _abandoned_count
    with _pool_lock:
        _abandoned_count += 1
        if _abandoned_count >= _MAX_ABANDONED_BEFORE_RECYCLE:
            print(f"[{label}] {_abandoned_count} worker retry-fetch diduga macet permanen -- "
                  f"membuat pool baru ({_POOL_SIZE} slot) untuk pulihkan kapasitas", file=sys.stderr)
            _executor = ThreadPoolExecutor(max_workers=_POOL_SIZE, thread_name_prefix="retry-fetch")
            _abandoned_count = 0


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
            future = _submit(fn)
            return future.result(timeout=timeout_seconds)
        except _FutureTimeoutError:
            last_exc = TimeoutError(f"{label} timed out after {timeout_seconds}s")
            _note_abandoned(label)
            if attempt < retries:
                print(f"[{label}] percobaan {attempt}/{retries} timeout ({timeout_seconds}s) — retry dalam {backoff_seconds}s",
                      file=sys.stderr)
                time.sleep(backoff_seconds)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                print(f"[{label}] percobaan {attempt}/{retries} gagal: {_safe(exc)} — retry dalam {backoff_seconds}s",
                      file=sys.stderr)
                time.sleep(backoff_seconds)
    print(f"[{label}] gagal total setelah {retries}x percobaan: {_safe(last_exc)}", file=sys.stderr)
    raise last_exc
