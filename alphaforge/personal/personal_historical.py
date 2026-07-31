"""Personal historical — snapshot PersonalCallSet per hari per ticker.

Pola SAMA PERSIS dengan alphaforge.layer2.historical (public): snapshot utuh,
bukan ringkasan; `outcome` sengaja `None`, evaluasi ditunda (lihat draft §12).
Historical publik sudah kena pelajaran pahit soal ini -- versi lamanya
membangun evaluasi (record_outcome/compare_recommendations) sebelum bentuk
outcome-nya diputuskan, lalu harus dihapus total waktu D-04 mengubah bentuk
kontraknya. Ditulis ulang di sini (bukan import langsung dari historical.py)
supaya key JSON-nya jujur ("personal_call_set", bukan "aggregator_output"
generik dari reuse mentah) -- selebihnya logikanya identik.

`due_for_review` dihitung SAAT DIBACA, tidak pernah disimpan -- murni
penunjuk "yang mana layak ditinjau ulang", BUKAN vonis "tepat/meleset". Vonis
itu yang justru sengaja ditunda (sama alasan dengan outcome=None di atas).
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from .personal_reasoning import HORIZON_UPPER_DAYS
from ..json_safe import dumps_safe

if TYPE_CHECKING:
    from .personal_contracts import PersonalCallSet


def create_personal_entry(call_set: "PersonalCallSet") -> dict:
    return {
        "entry_id": str(uuid.uuid4()),
        "analyzed_at": call_set.generated_at,
        "personal_call_set": asdict(call_set),
        "method_versions": dict(call_set.method_versions),
        "outcome": None,  # sengaja ditunda -- lihat docstring modul
    }


def _entry_date(entry: dict) -> str:
    return entry["analyzed_at"]


def load_personal_history(history_file: str | Path) -> dict[str, dict]:
    """{ticker: {ticker, total_entries, first_entry_date, last_entry_date,
    entries: [...]}} -- entries dibiarkan dict, tidak direkonstruksi balik ke
    dataclass (sama alasan historical.py: entry lama tidak pernah diedit,
    cuma ditambah & dibaca ulang sebagai JSON)."""
    p = Path(history_file)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def update_personal_timeline(
    timelines: dict[str, dict], new_call_sets: list["PersonalCallSet"],
) -> dict[str, dict]:
    """Satu entry per HARI KALENDER (UTC) per ticker -- re-run di hari yang
    sama menimpa entry hari itu, bukan menambah duplikat (konvensi sama
    dengan historical.py, lihat riwayat bug commit 7caf44c di sana)."""
    for call_set in new_call_sets:
        ticker = call_set.ticker
        if ticker not in timelines:
            timelines[ticker] = {
                "ticker": ticker, "total_entries": 0,
                "first_entry_date": None, "last_entry_date": None, "entries": [],
            }
        timeline = timelines[ticker]
        entry = create_personal_entry(call_set)

        entries = timeline["entries"]
        same_day = (
            entries
            and datetime.fromisoformat(_entry_date(entries[-1])).date()
            == datetime.fromisoformat(_entry_date(entry)).date()
        )
        if same_day:
            entries[-1] = entry
        else:
            entries.append(entry)
            timeline["total_entries"] += 1

        timeline["last_entry_date"] = _entry_date(entry)
        if not timeline["first_entry_date"]:
            timeline["first_entry_date"] = _entry_date(entry)

    return timelines


def save_personal_history(timelines: dict[str, dict], output_file: str | Path) -> None:
    """Tulis atomik (tmp + os.replace) — audit item C8: sebelumnya
    `open(..., "w")` biasa, satu-satunya penulis di codebase yang tidak
    memakai pola ini. Kill di tengah tulis meninggalkan JSON terpotong; dan
    karena berkas ini tidak ikut `backend/app.py::_warm_cache`, kegagalan
    baca-nya baru muncul sebagai HTTP 500 di `/api/personal/*` saat pertama
    kali diminta, tanpa pulih sendiri (tidak seperti stage file lain yang
    kegagalan cache-nya biasanya cuma memicu refetch)."""
    p = Path(output_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(dumps_safe(timelines, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# Berapa lama setelah tanggal katalis sebuah tesis Speculative dinilai.
# Tanpa ini, call yang eksplisit berjangkar ke "earnings 4 Agustus" baru
# dievaluasi di hari ke-28 (24 Agustus) — 20 hari setelah peristiwa yang
# jadi dasar tesisnya lewat, jadi yang terukur sudah bercampur hal lain.
# Buffer kecil, bukan nol: reaksi harga terhadap earnings butuh beberapa
# hari perdagangan untuk mengendap.
ANCHOR_BUFFER_DAYS = 5


def call_due_date(call: dict, analyzed_at: str | None) -> date | None:
    """Kapan SATU call (satu lens) layak dinilai.

    Kalau call punya `horizon_anchor` (cuma Speculative yang punya — tanggal
    katalis nyata), jatuh temponya dipatok ke tanggal itu + buffer. Kalau
    tidak, jatuh dari umur snapshot + batas atas bucket horizon-nya.
    """
    anchor = call.get("horizon_anchor")
    if anchor:
        try:
            return date.fromisoformat(anchor) + timedelta(days=ANCHOR_BUFFER_DAYS)
        except ValueError:
            pass  # format aneh — jatuh balik ke perhitungan berbasis umur
    upper = HORIZON_UPPER_DAYS.get(call.get("horizon"))
    if upper is None or not analyzed_at:
        return None
    try:
        analyzed = datetime.fromisoformat(analyzed_at).date()
    except ValueError:
        return None
    return analyzed + timedelta(days=upper)


def due_for_review(entry: dict, today: date | None = None) -> bool:
    """True kalau SALAH SATU dari 3 lens di entry ini sudah lewat jatuh
    temponya sendiri (lihat call_due_date). Murni soal waktu -- tidak melihat
    apakah tesisnya benar/salah.

    Catatan: ini dipakai untuk penanda "layak ditinjau ulang" di UI, jadi
    "salah satu" memang yang diinginkan. EVALUASI outcome TIDAK boleh memakai
    fungsi ini sebagai gerbang untuk ketiga lens sekaligus -- lihat
    personal_evaluation.evaluate_due_entries, yang menilai per lens.
    """
    today = today or datetime.now().date()
    analyzed_at = entry.get("analyzed_at")
    call_set = entry.get("personal_call_set", {})
    for module in ("multibagger", "quality_compound", "speculative"):
        call = call_set.get(module)
        if not call:
            continue
        due = call_due_date(call, analyzed_at)
        if due is not None and today > due:
            return True
    return False
