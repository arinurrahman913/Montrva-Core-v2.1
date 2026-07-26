"""Catalyst Set contracts — 10_CATALYST_TRACKING.md + Data Contracts §5b (D-11).

Katalis = fakta tentang KALENDER (peristiwa mendatang yang menggerakkan harga:
earnings, produk, regulasi), BUKAN fakta tentang keadaan perusahaan — itulah
kenapa ia terpisah dari Knowledge (umur simpan beda: earnings 19 Juli mati
20 Juli, margin 42% tidak). Tiap katalis wajib punya `expires_at` eksplisit.

Dihasilkan Fase A, per-ticker, diturunkan dari Evidence. Konsumen utama:
Speculative Module. Konsumen sekunder: Multibagger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CatalystKind = Literal["earnings", "dividend", "product", "regulatory", "filing", "other"]
# scheduled = tanggal pasti terkonfirmasi; expected = tanggal estimasi;
# rumored = belum resmi (mis. rumor akuisisi) — rumored WAJIB menurunkan
# confidence, bukan menaikkan stance (lihat spec "Kepastian Bertingkat").
Certainty = Literal["scheduled", "expected", "rumored"]
CatalystStatus = Literal["ok", "degraded", "missing"]

# Lifecycle di mana posisi catalyst ini SEKARANG, berbeda dari `Certainty`
# (soal kepastian TANGGAL) dan dari `CatalystStatus` (soal kelengkapan DATA).
# Dihitung dengan bandingkan snapshot hari ini vs kemarin — lihat
# catalyst_history.py. "scheduled"/"monitoring" default untuk catalyst yang
# masih aktif dalam horizon; "delayed"/"completed"/"cancelled" cuma bisa
# diketahui lintas-run (satu snapshot tidak cukup).
LifecycleStatus = Literal["scheduled", "monitoring", "delayed", "completed", "cancelled"]


@dataclass
class CatalystSource:
    """Provenance satu katalis."""
    provider: str  # mis. "yahoo_finance"
    fetched_at: str  # ISO datetime


@dataclass
class Catalyst:
    """Satu peristiwa mendatang. `expected_at` bisa tanggal tunggal (str ISO)
    atau rentang {from, to} kalau tak pasti — direpresentasikan sebagai
    expected_at (tanggal utama/awal) + expected_at_end (None kalau tanggal
    tunggal, terisi kalau rentang) supaya tetap JSON-serializable flat."""
    catalyst_id: str  # stabil per (ticker, kind, tanggal), mis. "earnings_2026-08-14"
    kind: CatalystKind
    expected_at: str  # ISO date — tanggal (atau awal rentang)
    certainty: Certainty
    expires_at: str  # ISO date — kapan katalis berhenti relevan
    source: CatalystSource
    expected_at_end: str | None = None  # ISO date — akhir rentang, None kalau tanggal tunggal
    lifecycle_status: LifecycleStatus = "scheduled"
    previous_expected_at: str | None = None  # cuma terisi kalau lifecycle_status == "delayed"


@dataclass
class ResolvedCatalyst:
    """Satu catalyst yang sudah keluar dari CatalystSet aktif — completed
    (waktu lewat wajar) atau cancelled (hilang dari sumber sebelum waktunya).
    Ini histori-nya, disimpan cross-run (lihat catalyst_history.py)."""
    kind: str
    expected_at: str
    lifecycle_status: Literal["completed", "cancelled"]
    resolved_on: str  # ISO date — kapan status ini terdeteksi
    outcome_note: str | None = None  # mis. earnings actual vs estimate, best-effort


@dataclass
class CatalystSet:
    """Kumpulan katalis untuk satu ticker — Data Contracts §5b."""
    ticker: str
    method_version: str
    horizon_days: int  # jendela ke depan yang dipindai
    catalysts: list[Catalyst] = field(default_factory=list)
    status: CatalystStatus = "ok"
    resolved_history: list[ResolvedCatalyst] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @property
    def has_upcoming(self) -> bool:
        """True kalau ada minimal 1 katalis terjadwal/diperkirakan (bukan
        cuma rumor) — dipakai Speculative untuk membedakan 'asimetri
        berkatalis' dari 'asimetri tanpa katalis'."""
        return any(c.certainty in ("scheduled", "expected") for c in self.catalysts)
