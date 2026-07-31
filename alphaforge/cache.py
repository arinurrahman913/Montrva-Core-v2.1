"""Cache lokal berbasis file, TTL sederhana — 04_DATA_SOURCES/05_RATE_LIMIT_CACHING_STRATEGY.md
prinsip #2: "Caching wajib, bukan opsional."
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .json_safe import dumps_safe

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

# Windows treats these as reserved device names regardless of extension —
# a ticker literally named "CON" (a real NYSE symbol) produces a cache key
# "CON.json" that Windows refuses to create at all
# (OSError: [WinError 6] The handle is invalid), crashing full-market
# screening runs partway through since the ticker list is scanned
# alphabetically. Case-insensitive, matched on the stem only (extension
# doesn't save it — "CON.json" is just as reserved as "CON").
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _path(namespace: str, key: str) -> Path:
    d = CACHE_DIR / namespace
    d.mkdir(parents=True, exist_ok=True)
    safe_key = key.replace("/", "_").replace("\\", "_")
    if safe_key.upper() in _WINDOWS_RESERVED_NAMES:
        safe_key = f"_{safe_key}"
    return d / f"{safe_key}.json"


def get(namespace: str, key: str, ttl_seconds: float):
    p = _path(namespace, key)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if time.time() - payload["cached_at"] > ttl_seconds:
        return None
    return payload["data"]


def set(namespace: str, key: str, data) -> None:
    """Tulis cache secara ATOMIK (tmp unik-per-penulis + os.replace) — audit
    item B4: cache di-key per-CIK, bukan per-ticker, jadi dua thread yang
    fetch ticker BERBEDA tapi CIK SAMA (mis. GOOG/GOOGL, FOX/FOXA) bisa
    menulis ke path cache yang SAMA bersamaan. `write_text()` lama bukan
    atomik (open+write+close biasa) — dua penulis konkuren bisa
    menghasilkan file JSON yang isinya bercampur (torn write). Pembaca
    sebelumnya self-healing lewat cache-miss (JSON gagal parse -> refetch),
    yang menutupi gejalanya tapi tetap boros satu fetch ekstra tiap kali
    terjadi. Tmp file pakai nama UNIK per-penulis (`tempfile.mkstemp`),
    BUKAN suffix ".tmp" tetap — kalau dua penulis konkuren berbagi nama tmp
    yang sama, mereka bisa saling menimpa file TMP satu sama lain sebelum
    salah satu sempat rename, cuma memindahkan torn-write dari file final
    ke file tmp, bukan menghilangkannya."""
    p = _path(namespace, key)
    fd, tmp_path = tempfile.mkstemp(dir=p.parent, prefix=p.stem + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps_safe({"cached_at": time.time(), "data": data}))
        os.replace(tmp_path, p)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_stale(namespace: str, key: str):
    """Baca cache TANPA cek TTL — fallback darurat kalau fetch fresh gagal
    (mis. sumber down). Mendingan pakai data lama yang masih ada daripada
    pipeline berhenti total. Return (data, age_seconds) atau None kalau
    belum pernah di-cache sama sekali."""
    p = _path(namespace, key)
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    age = time.time() - payload["cached_at"]
    return payload["data"], age
