"""Peer Comparison output contracts — 06_PEER_RELATIVE_COMPARISON.md

Perbandingan ticker terhadap peer group (industri sejenis).
Fase B: butuh populasi Knowledge lengkap dari Fase A.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class PeerMetricComparison:
    """Satu metrik dibanding dengan peer group."""
    metric_name: str  # "pe_ratio", "gross_margin", "revenue_growth", dll
    ticker_value: float | None
    peer_group_median: float | None
    peer_group_min: float | None
    peer_group_max: float | None
    peer_group_count: int  # Jumlah peer dalam kalkulasi
    percentile: float | None  # 0-100: posisi ticker di peer group (50=median, >50=better than median)
    status: Literal["ok", "low_sample_size", "missing"]
    direction: Literal["higher_is_better", "lower_is_better"]  # Metric interpretation: tinggi baik atau rendah baik


@dataclass
class PeerGroupInfo:
    """Informasi peer group untuk ticker."""
    ticker: str
    sector: str | None
    industry: str | None
    peer_tickers: list[str]  # Tickers dalam peer group
    group_size: int
    peer_failures: list[str] = field(default_factory=list)  # Ticker yang gagal diambil data-nya


@dataclass
class PeerComparisonResult:
    """Hasil Peer Comparison untuk satu ticker — 06_PEER_RELATIVE_COMPARISON.md.

    Output Fase B: bandingkan ticker terhadap peer group industri.
    """
    ticker: str
    exchange: str
    peer_group: PeerGroupInfo
    generated_at: str  # ISO datetime
    peer_group_basis: Literal["screening_universe", "manual"] = "screening_universe"
    low_sample_size: bool = False  # True kalau peer_group.group_size < 3 (Data Contracts §5)

    # Metric comparisons (TBD: mana saja yang dibandingkan)
    pe_ratio_comparison: PeerMetricComparison | None = None
    ps_ratio_comparison: PeerMetricComparison | None = None
    pb_ratio_comparison: PeerMetricComparison | None = None
    fcf_yield_comparison: PeerMetricComparison | None = None

    gross_margin_comparison: PeerMetricComparison | None = None
    operating_margin_comparison: PeerMetricComparison | None = None
    net_margin_comparison: PeerMetricComparison | None = None

    revenue_growth_comparison: PeerMetricComparison | None = None
    roe_comparison: PeerMetricComparison | None = None
    roa_comparison: PeerMetricComparison | None = None

    debt_to_equity_comparison: PeerMetricComparison | None = None

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
