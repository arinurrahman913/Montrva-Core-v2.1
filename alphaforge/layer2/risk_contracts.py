"""Risk & Red-Flag module contracts — Layer 2 Fase B, stage 3

Deteksi anomali dan warning signs:
- Governance anomalies (auditor changes, restatements, litigation)
- Financial extremes (margins, debt levels, valuation)
- Momentum reversals (guidance misses, earnings streak breaks)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class RedFlag:
    """Satu warning sign yang terdeteksi."""
    flag_type: Literal[
        "auditor_change", "restatement", "litigation", "unusual_filing",
        "high_debt", "low_liquidity", "declining_margin", "negative_fcf",
        "valuation_extreme", "severe_drawdown",
        "guidance_miss", "earnings_streak_break", "high_volatility"
    ]
    severity: Literal["high", "medium", "low"]  # Risk level
    description: str  # Penjelasan flag
    context: str | None = None  # Detail konteks
    affected_metrics: list[str] = field(default_factory=list)  # Metrik yang terdampak


FlagCategory = Literal["accounting", "governance", "litigation", "dilution", "insider", "listing_status"]
FlagSeverity = Literal["tinggi", "ekstrem"]
FlagStatus = Literal["triggered", "undetermined"]
# Kenapa sebuah status "undetermined" terjadi — dipakai reasoning.py untuk
# memutuskan apakah ia layak menurunkan confidence modul:
#   no_source       — pemeriksaannya tidak punya sumber data untuk ticker MANA
#                     PUN (litigasi, insider). Menghukumnya = penalti konstan
#                     ke seluruh universe, yang tidak membedakan apa pun.
#   ticker_specific — data ada untuk ticker lain, tapi tidak untuk yang ini
#                     (fetch gagal, riwayat filing lebih pendek dari jendela,
#                     baseline shares tidak tersedia). Ini informatif.
#   available       — pemeriksaannya berjalan penuh (status "triggered").
FlagAvailability = Literal["no_source", "ticker_specific", "available"]


@dataclass
class Flag:
    """Flag sesuai 04_RISK_REDFLAG_CHECK.md v2.0.0 + Data Contracts §7 —
    bentuknya dikunci, dirujuk AggregatorOutput.risk_flags.

    Beda dari RedFlag di atas (yang menilai kesehatan finansial/valuasi/
    momentum — bukan scope spec ini): Flag di sini cuma untuk 6 pemeriksaan
    yang eksplisit disebut spec (dilusi, pergantian auditor, restatement,
    litigasi, insider selling, fraud/delisting), masing-masing dipetakan ke
    SATU field Knowledge bagian 5/7 yang eksplisit (lihat tabel pemetaan di
    spec) — bukan kombinasi banyak metrik seperti RedFlag.
    """
    flag_id: str  # stabil lintas sesi, mis. "dilution_12m" — dirujuk flag_responses[].flag_id
    category: FlagCategory
    severity: FlagSeverity
    status: FlagStatus
    knowledge_refs: list[str]  # field Knowledge yang memicunya
    evidence_note: str  # fakta pemicunya, bukan penilaian
    source: str = ""  # sumber data mentah yang memicu (mis. "SEC EDGAR filings") — untuk transparansi, beda dari knowledge_refs yang nunjuk field internal
    availability: FlagAvailability = "available"
    method_version: str = "1.0"


@dataclass
class RiskAssessment:
    """Risk & Red-Flag assessment untuk satu ticker — Fase B output.

    Mengidentifikasi warning signs yang perlu dipertimbangkan dalam reasoning.
    """
    ticker: str
    exchange: str

    # Overall risk
    risk_score: float  # 0-100: 0=low risk, 100=high risk
    risk_rating: Literal["low", "medium", "high", "critical"]  # Kategorisasi

    # Risk categories
    governance_risk_score: float  # 0-100
    financial_risk_score: float  # 0-100
    momentum_risk_score: float  # 0-100
    valuation_risk_score: float  # 0-100

    # Detected flags (financial/valuation/momentum signals — bukan scope
    # 04_RISK_REDFLAG_CHECK.md, lihat docstring Flag)
    red_flags: list[RedFlag] = field(default_factory=list)
    high_severity_count: int = 0  # Jumlah high-severity flags
    medium_severity_count: int = 0  # Jumlah medium-severity flags
    low_severity_count: int = 0  # Jumlah low-severity flags

    # 04_RISK_REDFLAG_CHECK.md v2.0.0 — 6 pemeriksaan spec (dilusi, auditor,
    # restatement, litigasi, insider selling, fraud/delisting), dirujuk
    # AggregatorOutput.risk_flags & ModuleOutput.flag_responses (Fase 4).
    flags: list[Flag] = field(default_factory=list)
    # True kalau ada flag severity=ekstrem berstatus triggered — saham
    # berhenti di titik ini, tidak diteruskan ke 3 modul reasoning (lihat
    # "Cara Kerja" di spec, dan AggregatorOutput.halted §7).
    halted: bool = False
    halt_reason: str | None = None

    # Risk summary
    risk_notes: str = ""  # Catatan risk
    recommended_risk_adjustment: float = 0.0  # -0.3 to +0.3 adjustment untuk confidence

    # Metadata
    assessed_at: str = ""  # ISO datetime

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
