"""Confidence Report contracts — Layer 2 Fase B, stage 2: Data Quality Scoring

Shape dikunci di 01_ARCHITECTURE/04_DATA_CONTRACTS.md §5c (bukan lagi TBD
seperti versi awal 05_CONFIDENCE_DATA_QUALITY.md). Mengukur seberapa kuat
DATA saham ini — beda dari ModuleOutput.confidence yang mengukur seberapa
yakin MODUL pada kesimpulannya sendiri (lihat aturan V6, ModuleOutput.
confidence.score <= ConfidenceReport.overall.score).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Band = Literal["low", "medium", "high"]


@dataclass
class OverallConfidence:
    """Skor & kategori confidence keseluruhan."""
    score: float  # 0-100
    band: Band
    limiters: list[str] = field(default_factory=list)  # WAJIB terisi kalau band != "high"


@dataclass
class SectionScore:
    """Kelengkapan + skor satu bagian Knowledge (financial_health, valuation, dll).

    `filled` PECAHAN, bukan selalu bulat. Enam dari tujuh section memakai cek
    boolean sehingga nilainya selalu bulat; `ownership` punya satu komponen
    yang dinilai proporsional (rasio cakupan pemegang institusi), jadi bisa
    mis. 2,8 dari 3. Ditampilkan apa adanya di limiter dan TickerModal --
    "2,8/3" memang lebih informatif daripada dibulatkan jadi 3/3 yang lalu
    bertentangan dengan score-nya sendiri yang 93,3.
    """
    filled: float
    expected: int
    score: float  # 0-100 = filled/expected*100


@dataclass
class PeerPenalty:
    """Penalti dari kualitas peer group (low_sample_size / peer_failures)."""
    applied: bool
    reason: str | None = None


@dataclass
class ContextPenalty:
    """Penalti dari komponen Layer 1 yang degraded/missing saat profil ini disusun."""
    applied: bool
    components_degraded: list[str] = field(default_factory=list)


@dataclass
class RecencyPenalty:
    """Penalti dari umur Evidence — 05_CONFIDENCE_DATA_QUALITY.md §2: 'data
    fundamental yang berumur lebih dari 1 kuartal penuh menurunkan skor'."""
    applied: bool
    age_days: int | None = None
    reason: str | None = None


@dataclass
class ConsistencyPenalty:
    """Penalti dari 05_CONFIDENCE_DATA_QUALITY.md komponen #3: 'apakah data
    dari sumber berbeda (misal Yahoo Finance vs. SEC EDGAR untuk fundamental)
    saling menguatkan atau berbeda signifikan'. Sumbernya evidence.py's
    `_cross_reference_fundamental` (revenue Yahoo vs SEC EDGAR >15% divergen),
    diteruskan lewat KnowledgeMetadata.cross_reference_notes."""
    applied: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class ConfidenceReport:
    """Confidence Report untuk satu ticker — Data Contracts §5c.

    Satu per ticker (beda dari ModuleOutput.confidence yang tiga per ticker,
    satu per modul reasoning).
    """
    ticker: str
    exchange: str
    method_version: str

    overall: OverallConfidence
    by_section: dict[str, SectionScore]  # key = nama section Knowledge
    peer_penalty: PeerPenalty
    context_penalty: ContextPenalty
    recency_penalty: RecencyPenalty
    consistency_penalty: ConsistencyPenalty
    evidence_age_days: int | None

    assessed_at: str = ""  # ISO datetime

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
