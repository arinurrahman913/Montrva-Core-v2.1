"""Aggregator module — Layer 2 Fase B, stage 5 (final): AggregatorOutput + Synthesis.

D-04: TIDAK PERNAH memampatkan 3 ModuleOutput jadi satu skor/verdict di sini
— itu justru yang dilarang. module_outputs ditampilkan berdampingan; Synthesis
memetakan kesepakatan/perbedaan, tidak meringkasnya jadi rekomendasi.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .aggregator_contracts import (
    AggregatorOutput, Synthesis, ClaimAgreement, ClaimDivergence, ModulePosition,
    SynthesisConfidence, KnowledgeRef, PopulationBaseline,
)

if TYPE_CHECKING:
    from .catalyst_contracts import CatalystSet
    from .confidence_contracts import ConfidenceReport
    from .knowledge_contracts import KnowledgeProfile
    from .peer_contracts import PeerComparisonResult
    from .reasoning_contracts import ReasoningBundle
    from .risk_contracts import RiskAssessment

AGGREGATOR_METHOD_VERSION = "1.0"
SYNTHESIS_METHOD_VERSION = "1.0"

# Tier di AXIS MASING-MASING MODUL SENDIRI — dipakai buat menilai "searah"
# vs "berlawanan" tanpa membandingkan antar kosakata secara langsung (yang
# dilarang D-09). Ini murni posisi ordinal di dalam satu lensa (sah per D-04:
# "di dalam satu kosakata urutan tetap ada"), bukan skor gabungan.
_BULLISH_STANCES = {"ruang_terbuka", "compounding_kuat", "asimetri_berkatalis"}
_BEARISH_STANCES = {"ruang_tertutup", "bukan_compounder", "tanpa_asimetri"}
# sisanya (ruang_sempit/compounding_rapuh/asimetri_tanpa_katalis/*_tak_terbaca) = netral


def _tier(stance: str) -> str:
    if stance in _BULLISH_STANCES:
        return "bullish"
    if stance in _BEARISH_STANCES:
        return "bearish"
    return "neutral"


def _module_list(bundle: ReasoningBundle) -> list:
    """Urutan tetap (spec §7): multibagger, quality_compound, speculative."""
    return [bundle.multibagger, bundle.quality_compound, bundle.speculative]


def _stance_combo(bundle: ReasoningBundle) -> tuple[str, str, str]:
    return (bundle.multibagger.stance, bundle.quality_compound.stance, bundle.speculative.stance)


def compute_population_baseline(
    bundles: list[ReasoningBundle], session_id: str
) -> tuple[dict[tuple, float], PopulationBaseline]:
    """P(kombinasi stance) di seluruh populasi sesi ini (D-14) — BARRIER:
    synthesis satu ticker pun tidak bisa dihitung sebelum ini selesai,
    beda dari semua tahap sebelumnya yang murni per-ticker independen."""
    combo_counts = Counter(_stance_combo(b) for b in bundles)
    total = len(bundles)
    freq = {combo: count / total for combo, count in combo_counts.items()} if total > 0 else {}
    return freq, PopulationBaseline(session_id=session_id, sample_size=total)


def _surprise(bundle: ReasoningBundle, freq: dict[tuple, float]) -> float:
    """-log P(kombinasi stance ticker ini | populasi sesi). Karena freq
    dihitung DARI populasi yang termasuk ticker ini sendiri, p selalu > 0
    by construction (tidak akan pernah -log(0)=inf) — fallback di bawah
    murni defensif untuk kasus yang seharusnya tidak tercapai."""
    p = freq.get(_stance_combo(bundle))
    if not p:
        return 0.0
    return -math.log(p)


def _build_synthesis(
    bundle: ReasoningBundle, surprise: float, population_baseline: PopulationBaseline
) -> Synthesis:
    outputs = _module_list(bundle)
    tiers = {o.module: _tier(o.stance) for o in outputs}
    bullish = [o for o in outputs if tiers[o.module] == "bullish"]
    bearish = [o for o in outputs if tiers[o.module] == "bearish"]

    # --- Agreements: 2+ modul di tier sama (bullish ATAU bearish) yang
    # berbagi minimal 1 key_metrics — claim disitasi ke metrik spesifik. ---
    agreements: list[ClaimAgreement] = []
    for group, label in ((bullish, "bullish"), (bearish, "bearish")):
        if len(group) < 2:
            continue
        by_name = {o.module: o for o in group}
        metric_owners: dict[str, list[str]] = {}
        for o in group:
            for m in o.key_metrics:
                metric_owners.setdefault(m, []).append(o.module)
        for metric, owners in metric_owners.items():
            if len(owners) < 2:
                continue
            values = {name: by_name[name].key_metrics[metric] for name in owners}
            agreements.append(ClaimAgreement(
                claim=f"{', '.join(owners)} searah ({label}) pada {metric}: {values}",
                modules=owners,
                citations=[f"module.{m}.key_metrics.{metric}" for m in owners],
            ))

    # --- Divergences: pasangan bullish x bearish, root_cause diagnosis
    # mekanis (bukan tebakan) dari knowledge_gaps/flag_responses/key_metrics. ---
    divergences: list[ClaimDivergence] = []
    for a in bullish:
        for b in bearish:
            shared_metrics = set(a.key_metrics) & set(b.key_metrics)
            a_flags = {r.flag_id: r.impact for r in a.flag_responses}
            b_flags = {r.flag_id: r.impact for r in b.flag_responses}
            differing_flag_impact = any(
                a_flags.get(fid) != b_flags.get(fid) for fid in set(a_flags) | set(b_flags)
            )
            if max(len(a.knowledge_gaps), len(b.knowledge_gaps)) >= 2:
                root_cause = "knowledge_gap"
            elif differing_flag_impact:
                root_cause = "flag_response"
            elif not shared_metrics:
                root_cause = "different_fields"
            else:
                root_cause = "different_weights"

            citations = [f"module.{a.module}.stance", f"module.{b.module}.stance"]
            citations += [f"module.{a.module}.key_metrics.{m}" for m in shared_metrics]

            divergences.append(ClaimDivergence(
                claim=f"{a.module} bullish, {b.module} bearish",
                modules=[
                    ModulePosition(module=a.module, position=f"{a.stance}: {a.stance_rationale}"),
                    ModulePosition(module=b.module, position=f"{b.stance}: {b.stance_rationale}"),
                ],
                root_cause=root_cause,
                citations=citations,
            ))

    # Narasi render deterministik dari field terstruktur di atas — bukan
    # karangan bebas (sama seperti larangan D-06/D-10 di Layer 1's ContextSummary).
    narrative_parts = []
    if agreements:
        narrative_parts.append(f"{len(agreements)} titik searah antar modul.")
    if divergences:
        narrative_parts.append(f"{len(divergences)} titik berbeda pandangan antar modul.")
    if not agreements and not divergences:
        narrative_parts.append("Tidak ada pola searah maupun berlawanan yang jelas antar modul (netral/tak-terbaca).")
    narrative = " ".join(narrative_parts)

    # confidence = TERENDAH dari 3 modul (S4) — bukan dirata-rata, supaya
    # satu modul yang buta tidak tersembunyi di balik dua yang percaya diri.
    lowest = min(outputs, key=lambda o: o.confidence.score)
    synthesis_confidence = SynthesisConfidence(
        score=lowest.confidence.score, band=lowest.confidence.band,
        limiters=list(lowest.confidence.limiters),
    )

    full_convergence = (
        all(tiers[o.module] == "bullish" for o in outputs)
        or all(tiers[o.module] == "bearish" for o in outputs)
    )

    return Synthesis(
        method_version=SYNTHESIS_METHOD_VERSION,
        agreements=agreements,
        divergences=divergences,
        narrative=narrative,
        confidence=synthesis_confidence,
        full_convergence=full_convergence,
        surprise=round(surprise, 4),
        population_baseline=population_baseline,
    )


def aggregate_output(
    ticker: str,
    exchange: str,
    session_id: str,
    knowledge: KnowledgeProfile | None,
    catalyst: CatalystSet | None,
    confidence: ConfidenceReport | None,
    risk: RiskAssessment | None,
    reasoning: ReasoningBundle | None,
    population_freq: dict[tuple, float],
    population_baseline: PopulationBaseline,
) -> AggregatorOutput:
    """Bangun AggregatorOutput satu ticker. Kalau halted=true (Risk severity
    ekstrem triggered): module_outputs kosong, synthesis None, risk_flags
    tetap lengkap — sesuai spec "Kalau halted=true"."""
    halted = bool(risk and risk.halted)
    halt_reason = risk.halt_reason if halted else None

    if halted or reasoning is None:
        module_outputs: list = []
        synthesis = None
    else:
        module_outputs = _module_list(reasoning)
        surprise = _surprise(reasoning, population_freq)
        synthesis = _build_synthesis(reasoning, surprise, population_baseline)

    method_versions = {
        "knowledge": knowledge.metadata.method_version if knowledge else "unknown",
        "confidence": confidence.method_version if confidence else "unknown",
        "risk": "1.0",
        "reasoning": module_outputs[0].method_version if module_outputs else "unknown",
        "catalyst": catalyst.method_version if catalyst else "unknown",
        "peer": "1.0",
        "aggregator": AGGREGATOR_METHOD_VERSION,
        "synthesis": SYNTHESIS_METHOD_VERSION,
    }

    knowledge_ref = (
        KnowledgeRef(ticker=ticker, evidence_snapshot_date=knowledge.metadata.evidence_date)
        if knowledge else None
    )

    return AggregatorOutput(
        ticker=ticker,
        exchange=exchange,
        session_id=session_id,
        market_context_ref=session_id,
        knowledge_ref=knowledge_ref,
        module_outputs=module_outputs,
        synthesis=synthesis,
        catalysts=catalyst,
        confidence_report=confidence,
        risk_flags=list(risk.flags) if risk else [],
        halted=halted,
        halt_reason=halt_reason,
        method_versions=method_versions,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def run_aggregator(
    profiles: list[KnowledgeProfile],
    peers: list[PeerComparisonResult] | None = None,
    confidences: list[ConfidenceReport] | None = None,
    risks: list[RiskAssessment] | None = None,
    reasonings: list[ReasoningBundle] | None = None,
    catalysts: list[CatalystSet] | None = None,
    session_id: str | None = None,
) -> list[AggregatorOutput]:
    """Run aggregator untuk semua profiles.

    `peers` diterima tapi tidak lagi dipakai langsung di sini — info
    relatif-peer yang relevan sudah masuk key_metrics tiap ModuleOutput
    (Quality/Multibagger, lihat Fase 4), bukan ringkasan percentile tunggal
    di Aggregator (itu justru pola verdict-tunggal yang dilarang D-04).
    Parameter dipertahankan untuk kompatibilitas signature caller.
    """
    del peers  # sengaja tidak dipakai, lihat docstring
    session_id = session_id or datetime.now(timezone.utc).strftime("session-%Y%m%dT%H%M%S")

    confidence_map = {c.ticker: c for c in (confidences or [])}
    risk_map = {r.ticker: r for r in (risks or [])}
    reasoning_map = {r.ticker: r for r in (reasonings or [])}
    catalyst_map = {c.ticker: c for c in (catalysts or [])}

    population_freq, population_baseline = compute_population_baseline(reasonings or [], session_id)

    outputs = []
    for profile in profiles:
        output = aggregate_output(
            ticker=profile.ticker,
            exchange=profile.exchange,
            session_id=session_id,
            knowledge=profile,
            catalyst=catalyst_map.get(profile.ticker),
            confidence=confidence_map.get(profile.ticker),
            risk=risk_map.get(profile.ticker),
            reasoning=reasoning_map.get(profile.ticker),
            population_freq=population_freq,
            population_baseline=population_baseline,
        )
        outputs.append(output)
    return outputs
