"""Historical tracking module — Layer 2 Fase B, stage 6: snapshot storage (v2.0).

12_HISTORICAL_TRACKING_JOURNAL.md: penyimpanan snapshot AggregatorOutput
sejak v2.0 (murah, mulai hari ini); EVALUASI terhadap outcome nyata sengaja
DITUNDA ke v2.1 karena bentuknya ("Return absolut? Relatif index? Horizon
berapa lama, beda per modul?") masih eksplisit "belum diputuskan" di spec
sendiri. Versi sebelumnya sempat implementasi evaluasi (record_outcome,
compare_recommendations, confidence_trend) lebih awal dari keputusan spec-nya
sendiri — dihapus di sini, bukan diadaptasi, karena field yang dipakainya
(recommendation/conviction tunggal) sudah tidak ada lagi (D-04), dan
menebak bentuk outcome sendiri akan mengulang kesalahan yang sama (membuat
keputusan produk yang seharusnya didiskusikan, bukan diasumsikan).

Entries disimpan sebagai dict polos setelah dibuat (bukan direkonstruksi balik
jadi dataclass bersarang saat load) — snapshot AggregatorOutput bersarang
dalam sampai ModuleOutput/Flag/dst, round-trip dataclass penuh tidak
diperlukan karena entry lama tidak pernah dimodifikasi, cuma ditambah &
dibaca ulang sebagai JSON.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .historical_contracts import HistoricalTimeline
from ..json_safe import dumps_safe

if TYPE_CHECKING:
    from .aggregator_contracts import AggregatorOutput

# Keputusan pengguna 2026-07-31 (audit 2026-07-30 item C3): file ini
# menyimpan snapshot AggregatorOutput UTUH per ticker per hari SELAMANYA by
# design (lihat docstring modul) -- 469MB dan bertambah ~82MB/run,
# menahan backend/app.py::_stage_cache di memori tanpa batas dan bikin
# _warm_cache diproyeksikan gagal dalam ~2 minggu run harian. Retensi 2
# tahun (~730 hari) per ticker cukup untuk evaluasi horizon menengah (mis.
# thesis multibagger 1-2 tahun) tanpa membiarkan file tumbuh tanpa batas.
RETENTION_DAYS = 730


def create_historical_entry(output: AggregatorOutput) -> dict:
    """Bungkus satu AggregatorOutput jadi HistoricalEntry dict siap simpan."""
    return {
        "entry_id": str(uuid.uuid4()),
        "analyzed_at": output.generated_at,
        "aggregator_output": asdict(output),
        "method_versions": dict(output.method_versions),
        "outcome": None,
    }


def _entry_date(entry: dict) -> str:
    return entry["analyzed_at"]


def load_historical_timeline(timeline_file: str) -> dict[str, HistoricalTimeline]:
    """Load historical timeline dari file. Entries dibiarkan sebagai dict
    (lihat docstring modul) — cuma total/first/last date yang dibaca ulang
    ke field dataclass HistoricalTimeline."""
    if not Path(timeline_file).exists():
        return {}

    with open(timeline_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    timelines = {}
    for ticker, timeline_dict in data.items():
        timeline = HistoricalTimeline(
            ticker=ticker,
            total_entries=timeline_dict.get("total_entries", 0),
            first_entry_date=timeline_dict.get("first_entry_date"),
            last_entry_date=timeline_dict.get("last_entry_date"),
            entries=list(timeline_dict.get("entries", [])),
        )
        timelines[ticker] = timeline

    return timelines


def _prune_old_entries(timeline: HistoricalTimeline, cutoff: datetime) -> None:
    """Buang entries lebih tua dari `cutoff` dari BUFFER on-disk (audit item
    C3, retensi `RETENTION_DAYS`). `total_entries`/`first_entry_date` TIDAK
    ikut diubah -- keduanya tetap penghitung/tanggal SEUMUR HIDUP ticker ini
    dilacak (dipakai StatCards "Total Snapshots" & kolom "Snapshot Terakhir"
    di HistoricalView), bukan hitungan entries yang masih tersimpan di
    buffer. Entry dengan tanggal tak terbaca disimpan apa adanya (fail-safe,
    bukan dibuang diam-diam)."""
    if not timeline.entries:
        return
    kept = []
    for e in timeline.entries:
        try:
            entry_dt = datetime.fromisoformat(_entry_date(e))
        except (ValueError, TypeError):
            kept.append(e)
            continue
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
        if entry_dt >= cutoff:
            kept.append(e)
    timeline.entries = kept


def update_timeline(
    timelines: dict[str, HistoricalTimeline],
    new_outputs: list[AggregatorOutput],
    retention_days: int = RETENTION_DAYS,
) -> dict[str, HistoricalTimeline]:
    """Update timelines dengan AggregatorOutput baru. Satu entry per HARI
    KALENDER (UTC) per ticker — re-run di hari yang sama menimpa entry hari
    itu, bukan menambah duplikat (lihat riwayat bug di commit 7caf44c).

    Juga memangkas entries lebih tua dari `retention_days` (audit item C3)
    untuk SEMUA ticker di `timelines` -- bukan cuma yang disentuh
    `new_outputs` hari ini -- supaya ticker yang keluar dari screening tidak
    diam-diam menyisakan histori tak terbatas yang tidak pernah dipangkas
    lagi."""
    for output in new_outputs:
        if output.ticker not in timelines:
            timelines[output.ticker] = HistoricalTimeline(ticker=output.ticker)

        timeline = timelines[output.ticker]
        entry = create_historical_entry(output)

        same_day = (
            timeline.entries
            and datetime.fromisoformat(_entry_date(timeline.entries[-1])).date()
            == datetime.fromisoformat(_entry_date(entry)).date()
        )
        if same_day:
            timeline.entries[-1] = entry
        else:
            timeline.entries.append(entry)
            timeline.total_entries += 1

        timeline.last_entry_date = _entry_date(entry)
        if not timeline.first_entry_date:
            timeline.first_entry_date = _entry_date(entry)

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for timeline in timelines.values():
        _prune_old_entries(timeline, cutoff)

    return timelines


def save_historical_timeline(timelines: dict[str, HistoricalTimeline], output_file: str) -> None:
    data = {ticker: timeline.to_dict() for ticker, timeline in timelines.items()}
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(dumps_safe(data, indent=2, ensure_ascii=False))


def get_entry_history(timeline: HistoricalTimeline, days_back: int | None = None) -> list[dict]:
    """Get entry history untuk satu ticker, urut kronologis."""
    entries = list(timeline.entries)
    if days_back:
        from datetime import timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        entries = [e for e in entries if datetime.fromisoformat(_entry_date(e)) >= cutoff]
    return sorted(entries, key=_entry_date)
