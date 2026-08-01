"""Kunci eksklusif antar-proses untuk run yang menulis cache & file tahap.

Kenapa ada: 2026-08-01 job terjadwal `AlphaForge-Layer1-Refresh` (tiap 2 jam)
menyala di tengah run `refresh_full_pipeline.py` manual. Keduanya menarik
sector ETF yang sama ke direktori cache yang sama, `os.replace` kalah balapan,
dan komponen `money_flow` jadi `missing` — Layer Score lalu dihitung dari 12
komponen dengan bobot dinormalisasi ulang. Sebelumnya (catatan audit) hal
serupa pernah mengorupsi 55 file cache lewat tombol Generate.

cache.py sekarang menangani kontensi tulis dengan retry lalu menyerah diam-
diam, jadi tabrakan tidak lagi merusak PERHITUNGAN. Kunci ini menutup hal
yang berbeda dan tidak bisa ditutup retry: dua run penuh yang sama-sama
menulis 10 file tahap di akhir bisa meninggalkan campuran dua sesi di
dashboard/data/ — persis kondisi yang dideteksi /api/consistency. Retry tidak
menolong di situ karena kedua penulisan sama-sama "berhasil".

Desain sengaja sederhana:

- Pendatang kedua LANGSUNG KELUAR, tidak mengantre. Untuk job terjadwal,
  mengantre justru salah — run berikutnya menumpuk di belakang run 3 jam.
- Kunci basi WAJIB ditangani, bukan kasus teoretis: di proyek ini pernah ada
  proses zombie yang "hidup" 21 jam, dan restart Flask pernah membunuh anak
  prosesnya di tengah jalan. Tanpa penanganan, satu crash mengunci selamanya.
- Tanpa dependensi baru. `psutil` tidak ada di requirements.txt, jadi cek pid
  memakai ctypes (Windows) / os.kill(pid, 0) (POSIX).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent.parent / ".cache" / "pipeline.lock"

# Di atas run penuh terukur (~3 jam 10 menit, 2026-08-01). Kunci yang lebih
# tua dari ini dianggap basi walau pid-nya masih hidup -- pid bisa terpakai
# ulang oleh proses lain setelah crash.
DEFAULT_MAX_AGE_S = 5 * 3600


class AlreadyRunning(RuntimeError):
    """Proses lain sedang memegang kunci."""

    def __init__(self, info: dict):
        self.info = info
        age = info.get("age_seconds")
        age_txt = f"{age / 60:.0f} menit" if isinstance(age, (int, float)) else "?"
        super().__init__(
            f"Run lain sedang jalan: {info.get('script')} (pid {info.get('pid')}), "
            f"mulai {info.get('started_at')} ({age_txt} lalu). "
            f"Jalankan dengan --force-unlock kalau yakin itu sisa proses mati."
        )


def _pid_alive(pid: int) -> bool:
    """True kalau pid masih ada. Tidak memastikan itu proses yang SAMA (pid
    bisa terpakai ulang) -- penjaga umur di read_lock() yang menangani itu."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        # PROCESS_QUERY_LIMITED_INFORMATION: hak paling kecil yang cukup untuk
        # sekadar membuka handle, jadi tidak perlu privilese tinggi.
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # ada, cuma milik user lain
    return True


def read_lock(max_age_s: float = DEFAULT_MAX_AGE_S) -> dict | None:
    """Isi kunci yang sedang aktif, atau None kalau tidak ada / sudah basi.

    Dipakai juga oleh /api/refresh/status supaya statusnya jujur melintasi
    restart Flask -- sebelumnya status disimpan di memori proses, sehingga
    setelah Flask restart dia melaporkan running=false padahal ada pekerjaan
    yang belum kelar."""
    try:
        raw = LOCK_PATH.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return None

    try:
        info = json.loads(raw)
    except ValueError:
        # Kunci rusak (mis. tertulis separuh saat crash) -- perlakukan sebagai
        # basi, jangan biarkan file tak terbaca mengunci sistem selamanya.
        return None

    started = info.get("started_epoch")
    info["age_seconds"] = (time.time() - started) if isinstance(started, (int, float)) else None

    if info["age_seconds"] is not None and info["age_seconds"] > max_age_s:
        return None
    if not _pid_alive(int(info.get("pid", -1))):
        return None
    return info


def acquire(script: str, max_age_s: float = DEFAULT_MAX_AGE_S, force: bool = False) -> None:
    """Ambil kunci, atau lempar AlreadyRunning kalau proses lain memegangnya.

    force=True membuang kunci yang ada tanpa bertanya -- hanya untuk pemulihan
    manual, jangan dipakai di job terjadwal."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)

    if force:
        release(check_owner=False)

    payload = json.dumps({
        "pid": os.getpid(),
        "script": script,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "started_epoch": time.time(),
        "argv": " ".join(sys.argv[:3]),
    })

    for _ in range(2):
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            active = read_lock(max_age_s)
            if active is not None:
                raise AlreadyRunning(active)
            # Basi (pid mati / kelewat tua / isinya rusak) -> buang, coba lagi.
            release(check_owner=False)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        return

    raise AlreadyRunning(read_lock(max_age_s) or {"script": "?", "pid": "?"})


def release(check_owner: bool = True) -> None:
    """Lepas kunci. check_owner=True (default) hanya menghapus kalau kunci itu
    milik proses ini -- supaya proses yang gagal acquire tidak ikut menghapus
    kunci milik run yang sah saat blok finally-nya jalan."""
    if check_owner:
        try:
            info = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            if int(info.get("pid", -1)) != os.getpid():
                return
        except (OSError, ValueError):
            return
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass


class run_lock:
    """Context manager tipis di atas acquire/release."""

    def __init__(self, script: str, max_age_s: float = DEFAULT_MAX_AGE_S, force: bool = False):
        self._script = script
        self._max_age_s = max_age_s
        self._force = force

    def __enter__(self) -> "run_lock":
        acquire(self._script, self._max_age_s, self._force)
        return self

    def __exit__(self, *exc) -> None:
        release()
