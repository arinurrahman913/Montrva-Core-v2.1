"""Cache lokal berbasis file, TTL sederhana — 04_DATA_SOURCES/05_RATE_LIMIT_CACHING_STRATEGY.md
prinsip #2: "Caching wajib, bukan opsional."
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from .json_safe import dumps_safe

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

# os.replace ditolak Windows kalau file tujuan sedang dibuka proses lain.
# 5 = ERROR_ACCESS_DENIED, 32 = ERROR_SHARING_VIOLATION. Keduanya transien
# (proses lain akan menutup filenya), jadi layak dicoba ulang sebentar
# sebelum menyerah. Nilai lain BUKAN kontensi -- dilempar seperti biasa.
_CONTENTION_WINERRORS = {5, 32}
_REPLACE_ATTEMPTS = 3
_REPLACE_BACKOFF_S = 0.15


def _discard(tmp_path: str) -> None:
    try:
        os.unlink(tmp_path)
    except OSError:
        pass

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
    ke file tmp, bukan menghilangkannya.

    Nama tmp unik menutup torn-write, TAPI TIDAK menutup kegagalan
    `os.replace` itu sendiri: di Windows, rename ditolak (`WinError 5 Access
    is denied` / `WinError 32 sharing violation`) kalau file TUJUAN sedang
    dibuka proses lain. Terjadi nyata 2026-08-01 — job terjadwal
    AlphaForge-Layer1-Refresh (tiap 2 jam) beririsan dengan run pipeline
    manual, keduanya menarik sector ETF yang sama, dan `XLP_1y_1d` gagal
    di-rename.

    Yang membuatnya merusak bukan kegagalan itu sendiri, melainkan bahwa dia
    DILEMPAR ke pemanggil. Di layer1/components/money_flow.py seluruh loop
    fetch ada dalam satu `try`, dan sumber Yahoo mengambil + menyimpan dalam
    satu panggilan — sehingga gagal MENABUNG terbaca sebagai gagal MENGAMBIL,
    dan komponennya jadi `missing` padahal datanya sudah di tangan. Layer
    Score lalu dihitung dari 12 komponen dengan bobot dinormalisasi ulang,
    naik 1,6 poin tanpa sebab pasar.

    Karena itu: retry singkat (kontensi begini transien), lalu MENYERAH DIAM
    -DIAM ke WARNING dan return normal. Cache adalah optimasi, bukan sumber
    kebenaran; gagal menulisnya tidak boleh pernah menggagalkan perhitungan
    yang datanya sudah lengkap. Kegagalan menulis ISI file (disk penuh,
    encoding) tetap dilempar seperti biasa — itu bukan kontensi."""
    p = _path(namespace, key)
    fd, tmp_path = tempfile.mkstemp(dir=p.parent, prefix=p.stem + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dumps_safe({"cached_at": time.time(), "data": data}))
    except BaseException:
        _discard(tmp_path)
        raise

    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp_path, p)
            return
        except PermissionError:
            if attempt + 1 < _REPLACE_ATTEMPTS:
                time.sleep(_REPLACE_BACKOFF_S)
        except OSError as exc:
            if getattr(exc, "winerror", None) in _CONTENTION_WINERRORS and attempt + 1 < _REPLACE_ATTEMPTS:
                time.sleep(_REPLACE_BACKOFF_S)
            else:
                _discard(tmp_path)
                raise

    _discard(tmp_path)
    log.warning(
        "Cache tidak tersimpan (kontensi tulis, %d percobaan): %s/%s -- "
        "perhitungan tetap jalan pakai data yang sudah diambil",
        _REPLACE_ATTEMPTS, namespace, key,
    )


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
