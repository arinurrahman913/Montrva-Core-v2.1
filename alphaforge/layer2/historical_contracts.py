"""Historical tracking contracts — Layer 2 Fase B, stage 6 (post-pipeline)
12_HISTORICAL_TRACKING_JOURNAL.md + Data Contracts §8.

Penyimpanan (v2.0) dan evaluasi (v2.1) sengaja dipisah — cuma evaluasinya
yang boleh menyusul, bukan penyimpanannya (Prinsip #6). HistoricalEntry
menyimpan SNAPSHOT UTUH AggregatorOutput, bukan ringkasan — meringkas hari
ini mengandaikan kita sudah tahu apa yang penting untuk dievaluasi nanti,
padahal itu justru yang belum diketahui.

BATAS ARGUMEN ITU (2026-08-09). "Simpan utuh karena belum tahu apa yang
penting" benar untuk snapshot TERBARU dan salah untuk arsipnya: 97,3% tiap
entry adalah `aggregator_output`, 570 MB untuk 13 hari, dan proyeksi retensi
730 hari ~31 GB. Entry TERAKHIR tiap ticker tetap utuh (di situlah
ketidaktahuan itu nyata); entry yang lebih tua dipangkas jadi bentuk TIPIS di
bawah. Yang disimpan di bentuk tipis dipilih dari satu pertanyaan: "apa yang
diperlukan untuk mengukur ulang apa yang sistem KATAKAN hari itu?" — stance
per lensa, skor tesisnya, halted, konvergensi, dan versi metode reasoning
yang membuat stance itu bisa dibandingkan lintas waktu. Yang dibuang
(key_metrics, flag_responses, context_used, narrative, dst) bisa dihitung
ulang dari Evidence/Knowledge tanggal itu atau memang tidak menjawab
pertanyaan itu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Urutan field di dalam tiap elemen `lenses` pada entry TIPIS. Posisional,
# bukan objek berkunci, karena nama kunci yang diulang 3x per entry x 364
# entry x 4.233 ticker berharga ~157 MB — sementara urutannya cukup dikunci
# di satu tempat. KEMBARANNYA di frontend: THIN_LENS_FIELDS di
# frontend/src/format.js — dua-duanya harus berubah bersama.
THIN_LENS_FIELDS = ("module", "stance", "thesis_score", "confidence_band")


@dataclass
class HistoricalEntry:
    """Satu snapshot AggregatorOutput di waktu tertentu — Data Contracts §8.

    Bentuk PENUH: cuma dipakai entry terakhir tiap ticker. Entry yang lebih
    tua disimpan sebagai dict tipis (`{"thin": true, ...}`, lihat
    `historical.thin_entry`) yang TIDAK memakai dataclass ini — bentuk kedua
    itu sengaja tidak dipaksa masuk ke sini, karena field wajibnya
    (`aggregator_output`, `method_versions`) justru yang dibuang.
    """
    entry_id: str
    analyzed_at: str  # ISO datetime
    aggregator_output: object  # AggregatorOutput (snapshot utuh)
    method_versions: dict[str, str]  # disalin ke level entri, bukan cuma di dalam snapshot
    outcome: dict | None = None  # sengaja None di v2.0 — bentuknya belum diputuskan (v2.1, lihat spec)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class HistoricalTimeline:
    """Timeline entries untuk satu ticker."""
    ticker: str
    total_entries: int = 0
    first_entry_date: str | None = None
    last_entry_date: str | None = None
    entries: list[HistoricalEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
