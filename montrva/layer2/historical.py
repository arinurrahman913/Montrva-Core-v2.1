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

DUA BENTUK ENTRY (2026-08-09). Entry TERAKHIR tiap ticker penuh; yang lebih
tua dipangkas jadi bentuk tipis oleh `thin_entry()` — alasannya di docstring
historical_contracts.py. Konsekuensi yang harus diingat pembaca berkas ini:
`entries[-1]` boleh diandalkan punya `aggregator_output`, entry sebelumnya
TIDAK. Semua pembaca (backend/app.py ringkasan, TickerModal) sudah menangani
keduanya; penambah pembaca baru harus ikut.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .historical_contracts import THIN_LENS_FIELDS, HistoricalTimeline
from ..json_safe import dumps_safe

if TYPE_CHECKING:
    from .aggregator_contracts import AggregatorOutput

# Keputusan pengguna 2026-07-31 (audit 2026-07-30 item C3): file ini
# menyimpan snapshot AggregatorOutput UTUH per ticker per hari SELAMANYA by
# design (lihat docstring modul) -- 469MB dan bertambah ~82MB/run,
# menahan backend/app.py::_stage_cache di memori tanpa batas dan bikin
# _warm_cache diproyeksikan gagal dalam ~2 minggu run harian.
#
# 730 -> 365 (2026-08-09). Pembenaran lama untuk 2 tahun -- "cukup untuk
# evaluasi horizon menengah (mis. thesis multibagger 1-2 tahun)" -- SALAH:
# evaluasi tesis membaca `personal_history.json`, berkas lain yang punya
# retensinya sendiri; tidak ada satu pembaca pun berkas INI yang melihat
# lebih jauh dari entry terakhir. Angka 365 dipilih supaya masih ada satu
# tahun penuh bahan retrospeksi stance (bentuk tipis, ~257 B/entry) tanpa
# ekor yang mendominasi ukuran berkas.
#
# Ekornyalah yang menentukan ukuran, bukan snapshot penuhnya: 4.233 ticker x
# (11,2 KB penuh + N x 0,257 KB tipis). 365 hari ~433 MB, 180 ~236 MB, 90
# ~141 MB -- kalau nanti perlu dikecilkan lagi, ini kenopnya, bukan isi entry
# tipisnya.
RETENTION_DAYS = 365

# Modul reasoning yang stance-nya disimpan di entry tipis, urutan tetap.
_LENS_ORDER = ("multibagger", "quality_compound", "speculative")


def create_historical_entry(output: AggregatorOutput) -> dict:
    """Bungkus satu AggregatorOutput jadi HistoricalEntry dict siap simpan."""
    return {
        "entry_id": str(uuid.uuid4()),
        "analyzed_at": output.generated_at,
        "aggregator_output": asdict(output),
        "method_versions": dict(output.method_versions),
        "outcome": None,
    }


def _round_or_none(value, digits: int = 2):
    """Pembulatan yang membiarkan None/non-angka lewat apa adanya -- float
    mentah di sini berekor 14 digit (`70.77644910505336`), ~12 B percuma per
    angka, dan tiga di antaranya ada di tiap entry tipis."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value, digits)


def thin_entry(entry: dict) -> dict:
    """Pangkas satu entry penuh jadi bentuk tipis (lihat docstring modul).

    IDEMPOTEN: entry yang sudah tipis dikembalikan apa adanya, karena fungsi
    ini dipanggil tiap run atas seluruh riwayat -- kalau ia memangkas hasil
    pangkasannya sendiri, `lenses` akan kosong di run kedua.

    Entry berbentuk asing (bukan penuh, bukan tipis) juga dikembalikan apa
    adanya, bukan dipaksa: kehilangan diam-diam lebih mahal daripada satu
    entry gemuk yang tersisa.
    """
    if entry.get("thin") is True:
        return entry
    ao = entry.get("aggregator_output")
    if not isinstance(ao, dict):
        return entry

    syn = ao.get("synthesis") or {}
    by_module = {m.get("module"): m for m in (ao.get("module_outputs") or []) if isinstance(m, dict)}
    lenses = []
    # Urutan lensa dikunci ke _LENS_ORDER, bukan ke urutan module_outputs,
    # supaya pembacaan posisional tetap sahih kalau urutan produsen berubah.
    for name in _LENS_ORDER:
        m = by_module.get(name)
        if m is None:
            continue
        lenses.append([
            name,
            m.get("stance"),
            _round_or_none(m.get("thesis_score"), 1),
            (m.get("confidence") or {}).get("band"),
        ])

    return {
        "analyzed_at": entry.get("analyzed_at"),
        "thin": True,
        "halted": ao.get("halted"),
        "full_convergence": syn.get("full_convergence"),
        "surprise": _round_or_none(syn.get("surprise")),
        # Cuma versi reasoning, bukan seluruh `method_versions` (153 B/entry):
        # inilah satu-satunya versi yang membuat stance lintas waktu bisa
        # dibandingkan. Versi tahap lain untuk tanggal itu ada di git.
        "mv_reasoning": (entry.get("method_versions") or ao.get("method_versions") or {}).get("reasoning"),
        "lenses": lenses,
        "outcome": entry.get("outcome"),
    }


def thin_lens_dict(lens: list) -> dict:
    """Baca satu elemen `lenses` posisional jadi dict berkunci -- pembaca
    Python tidak perlu menghafal urutannya (frontend punya kembarannya)."""
    return dict(zip(THIN_LENS_FIELDS, lens))


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
    lagi.

    Lalu menipiskan semua entry KECUALI yang terakhir per ticker. Ini juga
    berlaku untuk ticker yang tidak disentuh run ini: tanpa itu, satu run
    yang melewatkan sebuah ticker akan meninggalkan entry gemuk di tengah
    riwayatnya selamanya. Karena `thin_entry` idempoten, menjalankan ini tiap
    run atas seluruh riwayat aman -- dan itu yang membuat berkas lama ikut
    rapi sendiri tanpa migrasi (scripts/compact_historical.py cuma
    mempercepatnya supaya tidak menunggu run berikutnya)."""
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
        if len(timeline.entries) > 1:
            timeline.entries[:-1] = [thin_entry(e) for e in timeline.entries[:-1]]

    # Record yang tidak pernah sekali pun berisi entry. Fungsi ini tidak bisa
    # MEMBUATNYA -- kunci ticker cuma lahir di loop di atas, dan kelahirannya
    # selalu langsung diikuti append + total_entries += 1 -- tapi juga tidak
    # pernah MEMBUANGnya, sementara load/save mengedarkan setiap kunci apa
    # adanya tiap run. Jadi sisa dari versi kode lama hidup selamanya: 4 ticker
    # (AIFC/FLL/MCAH/SUMA, semuanya hard_excluded di Screening sejak lama) ikut
    # terhitung di "4.245 ticker" dan tampil sebagai baris kosong bertanggal
    # null di Historical, tanpa membawa satu bit informasi pun.
    #
    # Dibuang, bukan disembunyikan di lapisan tampilan: yang salah datanya,
    # bukan cara membacanya. Kalau tickernya lolos screening lagi nanti, loop di
    # atas membuat ulang kuncinya berikut entry pertamanya -- tidak ada yang
    # hilang selain ketiadaan itu sendiri.
    empty = [t for t, tl in timelines.items() if tl.total_entries == 0]
    for ticker in empty:
        del timelines[ticker]

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
