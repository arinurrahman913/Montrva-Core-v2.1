"""Bentuk paket Layer 1, mengunci 01_ARCHITECTURE/04_DATA_CONTRACTS.md §3.

Explainability overhaul (2026-07): setiap ComponentReading sekarang bawa
evidence/rule/threshold/confidence/freshness/contribution/conflicts, bukan
cuma value+status+narrative. Field yang komponen isi sendiri (evidence,
rule, thresholds, raw_score) vs field yang diisi pipeline.py setelah semua
komponen terkumpul (confidence, data_freshness, contribution, conflicts)
— lihat pipeline.py untuk pembagian tanggung jawabnya.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Literal

Status = Literal["ok", "degraded", "missing"]
Kind = Literal["direct", "derived"]
Freshness = Literal["fresh", "acceptable", "stale"]


@dataclass
class Source:
    provider: str
    fetched_at: str


@dataclass
class Evidence:
    """Satu titik data mentah yang benar-benar dipakai komponen — field,
    nilai, tanggal data (bukan waktu fetch), dan sumber spesifiknya
    (seri FRED / ticker Yahoo), supaya bisa ditelusuri manual kalau perlu."""
    field: str
    value: Any
    as_of: str
    source: str


@dataclass
class Threshold:
    """Satu ambang batas numerik yang benar-benar dipakai untuk klasifikasi."""
    label: str
    operator: str
    value: float


@dataclass
class Contribution:
    """Kontribusi komponen ini ke Layer Score akhir."""
    score: float  # 0-100, raw_score komponen (arah sinyal sebelum ditimbang)
    weight: float  # 0-1, bobot tetap komponen ini
    weighted: float  # score * weight — nilai yang benar-benar masuk ke final_score


@dataclass
class ComponentReading:
    name: str
    value: Any
    status: Status
    kind: Kind
    method_version: str | None = None
    inputs: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    note: str | None = None
    narrative: str | None = None
    narrative_version: str | None = None
    # --- diisi komponen sendiri ---
    evidence: list[Evidence] = field(default_factory=list)
    rule: str | None = None
    thresholds: list[Threshold] = field(default_factory=list)
    raw_score: float | None = None  # 0-100, sebelum ditimbang — None kalau status != ok
    # --- diisi pipeline.py setelah semua komponen terkumpul ---
    confidence: float | None = None  # 0-100, confidence khusus komponen ini
    data_freshness: Freshness | None = None
    contribution: Contribution | None = None
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Confidence:
    score: float
    band: Literal["low", "medium", "high"]
    limiters: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass
class ScoreContribution:
    component: str
    score: float
    weight: float
    weighted: float


@dataclass
class LayerScore:
    """Skor kondisi pasar komposit Layer 1 (0-100, beda dari Confidence yang
    mengukur kualitas data) — rata-rata tertimbang raw_score tiap komponen
    yang status=ok, dinormalisasi ulang supaya komponen degraded/missing
    tidak diam-diam menyeret skor ke nilai default yang salah."""
    final_score: float
    formula_version: str
    contributions: list[ScoreContribution]
    excluded: list[str]
    reasoning: str
    band_label: str = ""  # label regime dari final_score: Risk-Off/Neutral/Neutral Positive/Risk-On

    def to_dict(self) -> dict:
        return {
            "final_score": self.final_score,
            "formula_version": self.formula_version,
            "band_label": self.band_label,
            "contributions": [asdict(c) for c in self.contributions],
            "excluded": self.excluded,
            "reasoning": self.reasoning,
        }


@dataclass
class ContextSummary:
    method_version: str
    narrative: str
    confidence: Confidence
    components_degraded: list[str]
    executive_summary: str = ""  # 1-2 kalimat "so what" — jawaban tindakan, bukan deskripsi status
    kind: Kind = "derived"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "method_version": self.method_version,
            "executive_summary": self.executive_summary,
            "narrative": self.narrative,
            "confidence": asdict(self.confidence),
            "components_degraded": self.components_degraded,
        }


@dataclass
class MarketContextPackage:
    session_id: str
    components: dict[str, ComponentReading]
    context_summary: ContextSummary
    layer_score: LayerScore | None = None
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    spx_history: list[dict] | None = None  # validation chart: [{date, price, ma50, ma200, norm_score}, ...] last 90 days
    data_quality_summary: dict | None = None  # {total, ok, degraded, fresh, acceptable, stale, confidence_band}

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "generated_at": self.generated_at,
            "components": {k: v.to_dict() for k, v in self.components.items()},
            "context_summary": self.context_summary.to_dict(),
            "layer_score": self.layer_score.to_dict() if self.layer_score else None,
            "spx_history": self.spx_history,
            "data_quality_summary": self.data_quality_summary,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
