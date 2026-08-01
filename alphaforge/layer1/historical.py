"""Historical tracking untuk LayerScore — ditambahkan pasca-audit (2026-07).

Sebelumnya tidak ada satu pun mekanisme yang melacak LayerScore dari waktu
ke waktu: tiap run cuma snapshot titik-waktu, tidak ada yang tahu apakah
skor 62 hari ini "normal" atau "tidak biasa" dibanding beberapa minggu
terakhir — dan tanpa histori, LayerScore juga tidak pernah bisa divalidasi
terhadap hasil pasar nyata (mis. dibandingkan versus return S&P 500 forward).

Modul ini cuma menyimpan; validasi/backtest adalah langkah terpisah nanti
setelah histori cukup terkumpul (beberapa bulan minimum untuk bermakna
secara statistik).

Satu entry per HARI KALENDER (UTC) — kalau refresh_layer1.py dipanggil
berkali-kali sehari (mis. tiap ~2 jam via scheduled task), entry hari itu
DIGANTI oleh run terbaru, bukan diakumulasi — mendekati konvensi "snapshot
akhir hari" pada deret waktu finansial, dan mencegah file membengkak.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..json_safe import dumps_safe, write_text_atomic

if TYPE_CHECKING:
    from .contracts import MarketContextPackage

MAX_ENTRIES = 730  # ~2 tahun kalender kalau satu entry/hari


def load_history(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _driver_drag(pkg: "MarketContextPackage") -> tuple[dict | None, dict | None]:
    """Kontributor yang paling menarik skor ke atas (driver) & ke bawah (drag),
    memakai (score-50)×weight — konsisten dengan _build_executive_summary.
    Disimpan per-hari supaya tooltip tren bisa jelaskan 'kenapa' tiap titik."""
    contribs = pkg.layer_score.contributions if pkg.layer_score else []
    if not contribs:
        return None, None
    driver = max(contribs, key=lambda c: (c.score - 50.0) * c.weight)
    drag = min(contribs, key=lambda c: (c.score - 50.0) * c.weight)
    d = {"component": driver.component, "score": round(driver.score, 1)} if (driver.score - 50.0) > 2.0 else None
    g = {"component": drag.component, "score": round(drag.score, 1)} if (drag.score - 50.0) < -2.0 else None
    return d, g


def _entry_from_package(pkg: "MarketContextPackage") -> dict | None:
    if not pkg.layer_score:
        return None
    n_ok = sum(1 for c in pkg.components.values() if c.status == "ok")
    top_driver, top_drag = _driver_drag(pkg)
    return {
        "date": pkg.generated_at[:10],  # YYYY-MM-DD, UTC (generated_at pakai now_iso() = UTC)
        "generated_at": pkg.generated_at,
        "final_score": pkg.layer_score.final_score,
        "formula_version": pkg.layer_score.formula_version,
        "band_label": pkg.layer_score.band_label,
        "n_ok": n_ok,
        "n_total": len(pkg.components),
        "confidence_score": round(pkg.context_summary.confidence.score, 1) if pkg.context_summary else None,
        "top_driver": top_driver,
        "top_drag": top_drag,
    }


def append_entry(path: str | Path, pkg: "MarketContextPackage", max_entries: int = MAX_ENTRIES) -> list[dict]:
    """Tambah snapshot LayerScore hari ini ke histori, tulis atomik. Kalau
    sudah ada entry untuk tanggal (UTC) yang sama, GANTI (bukan tambah) —
    lihat catatan modul. Return histori lengkap setelah update."""
    entry = _entry_from_package(pkg)
    if entry is None:
        return load_history(path)  # layer_score kosong (semua komponen gagal) — tidak ada yang dicatat

    history = load_history(path)
    if history and history[-1].get("date") == entry["date"]:
        history[-1] = entry
    else:
        history.append(entry)
    history = history[-max_entries:]

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(p, dumps_safe(history, indent=2, ensure_ascii=False))
    return history
