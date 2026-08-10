"""Reasoning module contracts — Layer 2 Fase B, stage 4

Bentuk ModuleOutput dikunci di 01_ARCHITECTURE/04_DATA_CONTRACTS.md §6 (D-04)
— identik untuk ketiga modul, isinya bebas. Kosakata `stance` beda per modul
(D-09) supaya tidak bisa dihitung sebagai suara ("2 compelling, 1 weak") —
lihat MULTIBAGGER_STANCES/QUALITY_STANCES/SPECULATIVE_STANCES di bawah.

positive_factors/negative_factors/key_metrics/score_breakdown BUKAN bagian
kontrak yang dikunci spec — dipertahankan sebagai field tambahan (spec cuma
mengunci nama field & tipe yang WAJIB ada, bukan melarang field ekstra)
karena masih dipakai dashboard & berguna untuk debug scoring internal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Module = Literal["multibagger", "quality_compound", "speculative"]
Band = Literal["low", "medium", "high"]
FlagImpact = Literal["none", "lowers_confidence", "changes_stance", "disqualifying"]

MULTIBAGGER_STANCES = ("ruang_terbuka", "ruang_sempit", "ruang_tertutup", "ruang_tak_terbaca")
QUALITY_STANCES = ("compounding_kuat", "compounding_rapuh", "bukan_compounder", "mesin_tak_terbaca")
SPECULATIVE_STANCES = ("asimetri_berkatalis", "asimetri_tanpa_katalis", "tanpa_asimetri", "asimetri_tak_terbaca")

STANCE_VOCAB: dict[Module, tuple[str, ...]] = {
    "multibagger": MULTIBAGGER_STANCES,
    "quality_compound": QUALITY_STANCES,
    "speculative": SPECULATIVE_STANCES,
}
# Kosakata "tak terbaca" per modul — dirujuk validasi V5 (stance ini wajib
# menyebut knowledge_gaps penyebabnya).
UNREADABLE_STANCE: dict[Module, str] = {
    "multibagger": "ruang_tak_terbaca",
    "quality_compound": "mesin_tak_terbaca",
    "speculative": "asimetri_tak_terbaca",
}


@dataclass
class ModuleConfidence:
    """Seberapa yakin MODUL INI pada kesimpulannya sendiri — beda dari
    ConfidenceReport yang mengukur kekuatan DATA (lihat confidence_contracts.py
    docstring & aturan V6: score di sini <= ConfidenceReport.overall.score)."""
    score: float  # 0-100
    band: Band
    limiters: list[str] = field(default_factory=list)  # WAJIB kalau band != "high" (V3)


@dataclass
class FlagResponse:
    """Respons modul terhadap satu Risk Flag severity=tinggi (V1: wajib ada
    tepat 1 entri per flag tinggi; V2: rationale harus spesifik per flag,
    bukan kalimat sama untuk semua)."""
    flag_id: str
    impact: FlagImpact
    rationale: str


@dataclass
class ContextUsage:
    """Satu komponen Layer 1 yang mempengaruhi stance modul ini."""
    component: str  # nama komponen Layer 1, mis. "market_regime"
    influence: str  # bagaimana ia mempengaruhi stance


@dataclass
class ModuleOutput:
    """Output satu reasoning lens — Data Contracts §6 (D-04), identik
    bentuknya untuk ketiga modul."""
    module: Module
    ticker: str
    exchange: str
    method_version: str

    stance: str  # nilai HARUS dari STANCE_VOCAB[module] — lihat validate_module_output
    stance_rationale: str

    confidence: ModuleConfidence
    flag_responses: list[FlagResponse] = field(default_factory=list)
    context_used: list[ContextUsage] = field(default_factory=list)
    knowledge_gaps: list[str] = field(default_factory=list)

    generated_at: str = ""

    # --- Field tambahan non-spec, dipertahankan untuk dashboard & debug ---
    positive_factors: list[str] = field(default_factory=list)
    negative_factors: list[str] = field(default_factory=list)
    key_metrics: dict[str, float | str] = field(default_factory=dict)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    fields_accessed: list[str] = field(default_factory=list)

    # Skor mentah lens ini (0-100, netral di 50) — angka yang sama yang sudah
    # dipakai internal reasoning.py untuk memilih stance, dan yang selama ini
    # cuma ditulis sebagai teks di stance_rationale ("Speculative score 63")
    # lalu dibuang. Dikeluarkan sebagai field supaya konsumen bisa MEMBANDINGKAN
    # kekuatan tesis antar-ticker DALAM satu lens tanpa mem-parsing string.
    # Tetap TIDAK sebanding lintas modul (tiap lens punya faktor & bobotnya
    # sendiri) — sama batasannya dengan stance, jadi ini bukan verdict tunggal
    # yang dilarang D-04.
    thesis_score: float = 50.0

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class ReasoningBundle:
    """3 ModuleOutput independen untuk satu ticker — BUKAN agregat/verdict
    tunggal (D-04 melarang itu di level Aggregator; menyatukannya jadi satu
    skor di sini akan mengulang pelanggaran yang sama satu lapis lebih awal).
    Sintesis non-memampatkan (agreements/divergences) ada di Synthesis —
    lihat aggregator_contracts.py, dibangun di Fase 6."""
    ticker: str
    exchange: str
    multibagger: ModuleOutput
    quality_compound: ModuleOutput
    speculative: ModuleOutput
    generated_at: str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def validate_module_output(
    output: ModuleOutput,
    tinggi_flag_ids: list[str],
    confidence_report_score: float | None,
) -> list[str]:
    """Cek V1-V6 (Data Contracts §6). Mengembalikan list pelanggaran — kosong
    berarti lolos. V1 gagal artinya output SEHARUSNYA ditolak (dipanggil
    sebagai pengaman regresi di reasoning.py, bukan cuma dekorasi)."""
    violations = []

    # V1: tiap flag severity tinggi butuh TEPAT SATU flag_responses dgn flag_id cocok.
    response_ids = [r.flag_id for r in output.flag_responses]
    for flag_id in tinggi_flag_ids:
        count = response_ids.count(flag_id)
        if count != 1:
            violations.append(f"V1: flag '{flag_id}' punya {count} flag_responses (harus tepat 1)")

    # V2: rationale identik di >=2 flag berbeda -> generic_response.
    rationale_counts: dict[str, list[str]] = {}
    for r in output.flag_responses:
        rationale_counts.setdefault(r.rationale, []).append(r.flag_id)
    for rationale, ids in rationale_counts.items():
        if len(ids) >= 2:
            violations.append(f"V2 (generic_response): rationale sama untuk flag {ids}: {rationale!r}")

    # V3: band != high -> limiters wajib tidak kosong.
    if output.confidence.band != "high" and not output.confidence.limiters:
        violations.append(f"V3: confidence.band={output.confidence.band} tapi limiters kosong")

    # V5: stance *_tak_terbaca wajib menyebut knowledge_gaps.
    if output.stance == UNREADABLE_STANCE[output.module] and not output.knowledge_gaps:
        violations.append(f"V5: stance '{output.stance}' tapi knowledge_gaps kosong")

    # V6: confidence.score <= ConfidenceReport.overall.score.
    if confidence_report_score is not None and output.confidence.score > confidence_report_score:
        violations.append(
            f"V6: confidence.score modul ({output.confidence.score}) > "
            f"ConfidenceReport.overall.score ({confidence_report_score})"
        )

    # Sanity tambahan (bukan V-rule bernomor, tapi tanpa ini stance tidak valid sama sekali)
    if output.stance not in STANCE_VOCAB[output.module]:
        violations.append(f"stance '{output.stance}' bukan bagian kosakata {output.module}")

    return violations
