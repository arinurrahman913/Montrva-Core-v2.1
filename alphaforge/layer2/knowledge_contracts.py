"""Knowledge Profile contracts — 03_LAYER2_SPECS/03_KNOWLEDGE.md

Structured understanding per ticker dari Evidence — fakta terukur, tren, kategori,
tanpa penilaian kualitatif. Jadi input seragam untuk Confidence, Peer, Risk, Reasoning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

SectorType = Literal[
    "Technology", "Healthcare", "Financials", "Energy", "Materials", "Industrials",
    "Consumer Discretionary", "Consumer Staples", "Real Estate", "Utilities", "Communication Services"
]
SizeCategory = Literal["micro", "small", "mid", "large"]
BusinessModel = Literal["subscription", "hardware", "marketplace", "saas", "other", None]


@dataclass
class RevenueTrend:
    """Tren revenue: YoY growth 4Q terakhir, CAGR 3/5 tahun."""
    yoy_q1: float | None = None  # % YoY growth, Q terakhir - 3Q
    yoy_q2: float | None = None  # Q - 2
    yoy_q3: float | None = None  # Q - 1
    yoy_q4: float | None = None  # Q terakhir
    cagr_3y: float | None = None  # Compound annual growth rate, 3 tahun (%)
    cagr_5y: float | None = None  # Compound annual growth rate, 5 tahun (%)


@dataclass
class MarginTrend:
    """Tren margin: gross/operating/net, 4Q terakhir."""
    q1: float | None = None  # margin % (Q - 3)
    q2: float | None = None  # margin % (Q - 2)
    q3: float | None = None  # margin % (Q - 1)
    q4: float | None = None  # margin % (Q terakhir)


@dataclass
class BalanceSheet:
    """Snapshot balance sheet: debt, liquidity, equity position."""
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    cash_and_equivalents: float | None = None  # Nominal value (USD)
    total_debt: float | None = None  # Total debt nominal (USD)
    shareholders_equity: float | None = None  # Total equity (USD)


@dataclass
class CashFlowTrend:
    """Tren cash flow: FCF per kuartal, FCF margin."""
    fcf_q1: float | None = None  # Free cash flow (Q - 3)
    fcf_q2: float | None = None  # Free cash flow (Q - 2)
    fcf_q3: float | None = None  # Free cash flow (Q - 1)
    fcf_q4: float | None = None  # Free cash flow (Q terakhir)
    fcf_margin_q4: float | None = None  # FCF / Revenue % (Q terakhir)


@dataclass
class CapExInfo:
    """Capital expenditure trend & context."""
    capex_nominal_q1: float | None = None  # CapEx USD (Q - 3)
    capex_nominal_q2: float | None = None  # CapEx USD (Q - 2)
    capex_nominal_q3: float | None = None  # CapEx USD (Q - 1)
    capex_nominal_q4: float | None = None  # CapEx USD (Q terakhir)
    capex_pct_revenue_q4: float | None = None  # CapEx / Revenue % (Q terakhir)
    capex_reason: str | None = None  # Alasan dari filing/rilis (mis. "data center expansion", tanpa ditulis bukan = null)


@dataclass
class FinancialHealth:
    """Bagian 2: Kesehatan Finansial."""
    revenue_trend: RevenueTrend
    gross_margin_trend: MarginTrend
    operating_margin_trend: MarginTrend
    net_margin_trend: MarginTrend
    balance_sheet: BalanceSheet
    cash_flow_trend: CashFlowTrend
    capex_info: CapExInfo
    # Snapshot TTM, bukan tren per-kuartal (Yahoo hanya kasih 1 angka trailing,
    # bukan breakdown per-Q seperti margin) — fraksi 0-1+ (bisa >1, mis. ROE
    # tinggi karena buyback besar-besaran menyusutkan equity), BUKAN persen.
    roe: float | None = None
    roa: float | None = None


@dataclass
class CompetitiveStructure:
    """Bagian 3a: Posisi Kompetitif — Struktur."""
    business_model: BusinessModel
    revenue_by_segment: dict[str, float] | None = None  # segment_name: revenue_pct. None jika tidak tersedia
    total_revenue_ttm: float | None = None  # Total revenue trailing 12 months
    employees_count: int | None = None
    tam_estimate: float | None = None  # Total addressable market estimate (USD) jika ada
    tam_source: str | None = None  # Sumber TAM (mis. "10-K filing", "third-party research cited")


@dataclass
class CompetitiveMomentum:
    """Bagian 3b: Posisi Kompetitif — Momentum."""
    segment_growth: dict[str, float] | None = None  # segment: YoY growth %. None jika tidak tersedia
    guidance_trend: Literal["up", "down", "neutral"] | None = None  # vs. konsensus analis
    acceleration_signal: str | None = None  # Deskripsi percepatan/perlambatan (mis. "QoQ growth accelerating", tanpa interpretasi)


@dataclass
class HistoricalTrend:
    """Bagian 4: Tren Historis — performa harga, volatilitas, earnings record."""
    return_1y: float | None = None  # % annual return, past 1 year
    return_3y: float | None = None  # % annual return, past 3 years (CAGR)
    return_5y: float | None = None  # % annual return, past 5 years (CAGR)
    volatility_daily: float | None = None  # std dev harian (%)
    beta: float | None = None  # vs. S&P 500
    earnings_beat_miss_streak: str | None = None  # Deskripsi: "5/8 beat" (5 beat dari 8 kuartal terakhir), dll


@dataclass
class Ownership:
    """Bagian 5: Kepemilikan."""
    institutional_pct: float | None = None  # Persentase owned institusional (0-100)
    insider_pct: float | None = None  # Persentase owned insider (0-100)
    insider_transactions: list[dict] = field(default_factory=list)  # List of {date, type: "buy"|"sell", amount_usd, exec_name}
    insider_filing_activity_30d: int = 0  # Form 4 filings dalam 30 hari terakhir (indicator of insider involvement)


@dataclass
class PriceTargetMetrics:
    """Analyst price target consensus & metrics."""
    current_price: float | None = None
    target_mean: float | None = None
    target_median: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    num_analysts: int | None = None
    upside_pct: float | None = None  # Mean target upside dari current price (%)
    recommendation_key: str | None = None  # "strong_buy", "buy", "hold", "sell", "strong_sell"
    recommendation_pct_buy: float | None = None  # % buy ratings (0-100)
    recommendation_pct_hold: float | None = None  # % hold ratings
    recommendation_pct_sell: float | None = None  # % sell ratings
    price_target_trend_3m: float | None = None  # Change dalam mean target 3 bulan terakhir (%)


@dataclass
class Valuation:
    """Bagian 6: Valuasi — rasio absolut per ticker saja, bukan vs peer."""
    pe_ratio_trailing: float | None = None
    pe_ratio_forward: float | None = None
    ps_ratio: float | None = None  # Price to Sales
    ev_ebitda: float | None = None  # Enterprise Value / EBITDA
    pb_ratio: float | None = None  # Price to Book
    fcf_yield: float | None = None  # FCF / Market Cap (%)
    price_target: PriceTargetMetrics | None = None  # Analyst consensus price target


@dataclass
class GovernanceEvent:
    """Satu peristiwa governance/filing."""
    event_type: str  # "auditor_change", "restatement", "litigation", "unusual_filing", dll
    date: str  # ISO date
    description: str  # Fakta deskriptif tanpa interpretasi


@dataclass
class Governance:
    """Bagian 7: Governance & Filing Events."""
    shares_outstanding_change_12m: float | None = None  # % change dalam 12 bulan terakhir (positive = dilution)
    auditor_changes: list[GovernanceEvent] = field(default_factory=list)
    restatements: list[GovernanceEvent] = field(default_factory=list)
    material_litigation: list[GovernanceEvent] = field(default_factory=list)
    unusual_filings: list[GovernanceEvent] = field(default_factory=list)


@dataclass
class KnowledgeMetadata:
    """Metadata: provenance, completeness tracking."""
    evidence_date: str  # ISO datetime dari Evidence snapshot
    method_version: str  # Version dari Knowledge calculation (mis. "1.0")
    fields_completed: int  # Jumlah field berhasil diisi
    fields_expected: int  # Total field yang diharapkan
    missing_fields: list[str] = field(default_factory=list)  # Nama field (dotted path) yang kosong — Data Contracts §4/§6 V4
    sources_used: list[str] = field(default_factory=list)  # List sumber dari Evidence (yahoo_finance, finnhub, sec_edgar)
    data_quality_notes: str | None = None


@dataclass
class KnowledgeProfile:
    """Knowledge Profile per ticker — 03_LAYER2_SPECS/03_KNOWLEDGE.md.

    Structured understanding dari Evidence: tren, kategorisasi, fakta terukur.
    Tanpa penilaian kualitatif (itu tugas Reasoning modules).
    """
    ticker: str
    exchange: str

    # 1. Identitas & Klasifikasi
    sector: SectorType | None
    size_category: SizeCategory | None  # dari Screening flags
    screening_flags: list[str]  # soft flags dari Screening: "micro_cap", "recent_ipo", "adr", dll

    # 2. Kesehatan Finansial
    financial_health: FinancialHealth

    # 3a & 3b. Posisi Kompetitif
    competitive_structure: CompetitiveStructure
    competitive_momentum: CompetitiveMomentum

    # 4. Tren Historis
    historical_trend: HistoricalTrend

    # 5. Kepemilikan
    ownership: Ownership

    # 6. Valuasi
    valuation: Valuation

    # 7. Governance & Filing Events
    governance: Governance

    # Metadata
    metadata: KnowledgeMetadata

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        from dataclasses import asdict
        return asdict(self)
