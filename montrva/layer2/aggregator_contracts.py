"""Aggregator Output contracts — Layer 2 Fase B, stage 5 (final).

Bentuk dikunci di 01_ARCHITECTURE/04_DATA_CONTRACTS.md §7 (D-04/D-07).
AggregatorOutput MENAMPILKAN 3 ModuleOutput berdampingan + Synthesis yang
memetakan (bukan memampatkan) — DILARANG punya field verdict/score/rank/
recommendation (D-04), termasuk di dalam Synthesis sendiri (D-07). Ini
gantiin FinalRecommendation lama yang justru punya persis field-field itu.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

RootCause = Literal[
    "different_fields", "different_weights", "knowledge_gap",
    "flag_response", "context_reading",
]


@dataclass
class KnowledgeRef:
    ticker: str
    evidence_snapshot_date: str


@dataclass
class ClaimAgreement:
    """Titik di mana ketiga modul (atau subset yang relevan) searah."""
    claim: str
    modules: list[str]
    citations: list[str]  # field spesifik, mis. "module.multibagger.key_metrics.return_1y"


@dataclass
class ModulePosition:
    module: str
    position: str  # ringkas: stance + alasan singkat


@dataclass
class ClaimDivergence:
    claim: str
    modules: list[ModulePosition]
    root_cause: RootCause
    citations: list[str]


@dataclass
class SynthesisConfidence:
    score: float
    band: Literal["low", "medium", "high"]
    limiters: list[str] = field(default_factory=list)


@dataclass
class PopulationBaseline:
    session_id: str
    sample_size: int


@dataclass
class Synthesis:
    """Peta konvergensi — D-07. `confidence` = TERENDAH dari 3 modul (S4),
    tidak dihitung sendiri/dirata-rata. `surprise` butuh populasi utuh sesi
    ini (D-14), dihitung di run_aggregator setelah semua ReasoningBundle
    selesai, bukan per-ticker terisolasi seperti tahap sebelumnya."""
    method_version: str
    agreements: list[ClaimAgreement] = field(default_factory=list)
    divergences: list[ClaimDivergence] = field(default_factory=list)
    narrative: str = ""
    confidence: SynthesisConfidence | None = None
    full_convergence: bool = False
    surprise: float = 0.0
    population_baseline: PopulationBaseline | None = None


@dataclass
class AggregatorOutput:
    """Output final per ticker — Data Contracts §7.

    DILARANG punya verdict/score/rank/recommendation (D-04) — kalau butuh
    "kesimpulan", baca module_outputs (3 lensa) + synthesis (petanya), bukan
    field ringkasan di sini.
    """
    ticker: str
    exchange: str
    session_id: str
    market_context_ref: str
    knowledge_ref: KnowledgeRef | None

    module_outputs: list  # list[ModuleOutput], selalu 3 & urutan tetap kecuali halted=true (kosong)
    synthesis: Synthesis | None  # None kalau halted=true
    catalysts: object | None  # CatalystSet | None
    confidence_report: object | None  # ConfidenceReport | None
    risk_flags: list  # list[Flag] — eksplisit, bukan disembunyikan

    halted: bool = False
    halt_reason: str | None = None

    method_versions: dict[str, str] = field(default_factory=dict)
    generated_at: str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
