"""Reasoning modules — Layer 2 Fase B, stage 4

3 independent reasoning lens dengan field access control (D-12) + output
dalam bentuk ModuleOutput yang dikunci Data Contracts §6 (D-04).

Skor & kriteria internal (fh.net_margin_trend.q4 > 5, dst) DIPERTAHANKAN
dari versi sebelumnya — 07/08/09_MODULE_*.md menandai bobot & kriteria ini
"didiskusikan terpisah, belum final", jadi Fase 4 ini cuma membungkusnya ke
kontrak ModuleOutput yang baru (stance per-modul, confidence terpisah dari
stance, flag_responses, knowledge_gaps), TIDAK mengkalibrasi ulang angkanya.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .reasoning_contracts import (
    ModuleOutput, ModuleConfidence, FlagResponse, ContextUsage, ReasoningBundle,
    MULTIBAGGER_STANCES, QUALITY_STANCES, SPECULATIVE_STANCES,
    validate_module_output,
)

if TYPE_CHECKING:
    from .catalyst_contracts import CatalystSet
    from .confidence_contracts import ConfidenceReport
    from .contracts import InstitutionalActivity
    from .knowledge_contracts import KnowledgeProfile
    from .peer_contracts import PeerComparisonResult
    from .risk_contracts import RiskAssessment
    from ..layer1.contracts import MarketContextPackage

METHOD_VERSION = "2.0"

# Komponen Layer 1 yang relevan per lensa — keputusan desain eksplisit
# (mirip semangat D-12 buat Knowledge), bukan "semua komponen buat semua
# lensa" supaya context_used tetap fokus & tidak generik. Multibagger fokus
# growth/ekspansi (sektor+siklus), Quality fokus macro backdrop compounder
# jangka panjang (kredit+yield), Speculative fokus profil risiko (VIX+regime).
LAYER1_RELEVANCE: dict[str, list[str]] = {
    "multibagger": ["sector_rotation", "business_cycle_stage"],
    "quality_compound": ["credit_spread", "yield_curve"],
    "speculative": ["volatility_index", "market_regime"],
}


def _build_context_used(layer1: "MarketContextPackage | None", module: str) -> list[ContextUsage]:
    """Bangun ContextUsage dari komponen Layer 1 yang relevan buat modul ini
    (lihat LAYER1_RELEVANCE). Influence text pakai narrative komponen APA
    ADANYA (kalimat pertama saja, biar ringkas) — bukan interpretasi baru,
    supaya tetap fakta bukan opini (sama prinsipnya dengan positive_factors/
    negative_factors di modul ini)."""
    if layer1 is None:
        return []
    result = []
    for component_name in LAYER1_RELEVANCE.get(module, []):
        comp = layer1.components.get(component_name)
        if comp is None:
            continue
        if comp.status != "ok" or not comp.narrative:
            influence = f"Data {component_name.replace('_', ' ')} {comp.status} — tidak bisa dipakai sebagai konteks saat ini"
        else:
            # Split di akhir kalimat asli (titik diikuti spasi), BUKAN titik
            # pertama — narrative sering berisi angka desimal ("+10.5pp")
            # yang juga punya titik tapi bukan akhir kalimat.
            first_sentence = re.split(r"(?<=[.!?])\s+", comp.narrative.strip(), maxsplit=1)[0]
            influence = first_sentence
        result.append(ContextUsage(component=component_name, influence=influence))
    return result

# Field yang bisa muncul di missing_fields (lihat knowledge.py
# _count_completed_fields) yang relevan untuk tiap modul, dibatasi akses D-12
# (03_KNOWLEDGE.md "Akses Field Per Modul"). Catatan jujur: missing_fields
# saat ini cuma melacak 13 field (bagian 2/4/5/6) — bagian 3a/3b/7 belum
# punya completeness-tracking sendiri (lihat Fase 1), jadi knowledge_gaps di
# bawah tidak lengkap untuk bagian itu, bukan berarti bagian itu selalu utuh.
MODULE_KNOWLEDGE_GAP_FIELDS = {
    "multibagger": {
        "historical_trend.return_1y", "historical_trend.return_3y", "historical_trend.return_5y",
        "historical_trend.volatility_daily", "historical_trend.earnings_beat_miss_streak",
        "valuation.pe_ratio_trailing", "valuation.ps_ratio", "valuation.pb_ratio", "valuation.fcf_yield",
        "valuation.price_target.upside_pct",
    },
    "quality_compound": {
        "financial_health.balance_sheet.debt_to_equity", "financial_health.balance_sheet.current_ratio",
        "financial_health.balance_sheet.quick_ratio", "financial_health.cash_flow_trend.fcf_q4",
        "historical_trend.return_1y", "historical_trend.return_3y", "historical_trend.return_5y",
        "historical_trend.volatility_daily", "historical_trend.earnings_beat_miss_streak",
        "valuation.pe_ratio_trailing", "valuation.ps_ratio", "valuation.pb_ratio", "valuation.fcf_yield",
        "valuation.price_target.upside_pct",
    },
    "speculative": {
        "historical_trend.volatility_daily",  # D-12: Speculative cuma dapat volatilitas dari bagian 4
        "ownership.institutional_pct",
    },
}


def _knowledge_gaps(profile: KnowledgeProfile, module: str) -> list[str]:
    allowed = MODULE_KNOWLEDGE_GAP_FIELDS[module]
    return [f for f in (profile.metadata.missing_fields or []) if f in allowed]


def _module_confidence(
    confidence_report: ConfidenceReport | None,
    knowledge_gaps: list[str],
) -> ModuleConfidence:
    """confidence.score modul ini <= ConfidenceReport.overall.score (V6) —
    dijamin karena selalu MULAI dari angka itu lalu cuma dikurangi."""
    if confidence_report is None:
        base = 50.0
        limiters = ["ConfidenceReport unavailable"]
    else:
        base = confidence_report.overall.score
        limiters = list(confidence_report.overall.limiters)

    score = max(0.0, base - 5.0 * len(knowledge_gaps))
    if knowledge_gaps:
        limiters.append(f"{len(knowledge_gaps)} field missing dalam scope modul ini")

    if score >= 70:
        band = "high"
    elif score >= 40:
        band = "medium"
    else:
        band = "low"

    if band == "high":
        limiters = []  # V3 cuma wajib kalau band != high

    return ModuleConfidence(score=round(score, 1), band=band, limiters=limiters)


def _build_flag_responses(risk: RiskAssessment | None) -> list[FlagResponse]:
    """V1: tepat 1 entri per flag severity=tinggi (semua modul, terlepas
    dari section akses D-12 masing-masing — Risk spec eksplisit bilang flag
    'terlihat oleh ketiga modul reasoning', override normal akses karena ini
    soal keselamatan (Prinsip #4), bukan analisis section biasa).
    V2: rationale menyertakan flag_id + evidence_note supaya otomatis beda
    antar flag, gak pernah kalimat generik yang sama diulang."""
    if risk is None:
        return []
    responses = []
    for flag in risk.flags:
        if flag.severity != "tinggi":
            continue
        if flag.status == "undetermined":
            impact = "lowers_confidence"
            rationale = f"{flag.flag_id}: belum bisa dipastikan ({flag.evidence_note}) — confidence diturunkan, bukan diabaikan"
        else:
            impact = "changes_stance" if flag.category == "dilution" else "lowers_confidence"
            rationale = f"{flag.flag_id} terkonfirmasi: {flag.evidence_note}"
        responses.append(FlagResponse(flag_id=flag.flag_id, impact=impact, rationale=rationale))
    return responses


def run_quality_lens(
    profile: KnowledgeProfile,
    confidence: ConfidenceReport | None = None,
    risk: RiskAssessment | None = None,
    peer: PeerComparisonResult | None = None,
    layer1: "MarketContextPackage | None" = None,
) -> ModuleOutput:
    """Quality/Compound Lens — "Ini mesin compounding?"

    Access: sections 1,2,3a,4,6,7 (Identity, Financial Health, Competitive
    Structure, Historical, Valuation, Governance).
    """
    score = 0.0
    positive = []
    negative = []
    metrics = {}

    fh = profile.financial_health
    if fh.net_margin_trend.q4 is not None and fh.net_margin_trend.q4 > 5:
        score += 15
        positive.append("Strong net margins (>5%)")
        metrics["net_margin_q4"] = fh.net_margin_trend.q4
    elif fh.net_margin_trend.q4 is not None and fh.net_margin_trend.q4 < 0:
        score -= 15
        negative.append("Negative net margin")
        metrics["net_margin_q4"] = fh.net_margin_trend.q4

    if fh.balance_sheet.current_ratio is not None and fh.balance_sheet.current_ratio > 1.5:
        score += 10
        positive.append("Strong current ratio (>1.5)")
        metrics["current_ratio"] = fh.balance_sheet.current_ratio
    elif fh.balance_sheet.current_ratio is not None and fh.balance_sheet.current_ratio < 1.0:
        score -= 10
        negative.append("Weak liquidity (current ratio <1)")
        metrics["current_ratio"] = fh.balance_sheet.current_ratio

    if fh.balance_sheet.debt_to_equity is not None and fh.balance_sheet.debt_to_equity < 1.0:
        score += 10
        positive.append("Conservative leverage (D/E <1)")
        metrics["debt_to_equity"] = fh.balance_sheet.debt_to_equity
    elif fh.balance_sheet.debt_to_equity is not None and fh.balance_sheet.debt_to_equity > 2.0:
        score -= 12
        negative.append("High leverage (D/E >2)")
        metrics["debt_to_equity"] = fh.balance_sheet.debt_to_equity

    ht = profile.historical_trend
    if ht.return_1y is not None and ht.return_1y > 10:
        score += 8
        positive.append("Positive 1Y return")
        metrics["return_1y"] = ht.return_1y
    elif ht.return_1y is not None and ht.return_1y < -20:
        score -= 8
        negative.append("Severe drawdown")
        metrics["return_1y"] = ht.return_1y

    val = profile.valuation
    if val.pe_ratio_trailing is not None:
        if val.pe_ratio_trailing < 20:
            score += 12
            positive.append("Attractive P/E (<20x)")
            metrics["pe_ratio"] = val.pe_ratio_trailing
        elif val.pe_ratio_trailing > 50:
            score -= 8
            negative.append("Expensive valuation (P/E >50x)")
            metrics["pe_ratio"] = val.pe_ratio_trailing

    if val.pb_ratio is not None and val.pb_ratio < 2.0:
        score += 8
        positive.append("Fair P/B ratio (<2x)")
        metrics["pb_ratio"] = val.pb_ratio

    # Peer: valuasi relatif — sebelumnya modul ini gak pernah baca Peer sama
    # sekali padahal 08_MODULE_QUALITY_COMPOUND.md butuh percentile peer
    # untuk menilai "wajar/mahal dibanding sejenis". Bobot kecil (+/-6),
    # bukan pengganti kriteria valuasi absolut di atas.
    if peer and peer.pe_ratio_comparison and peer.pe_ratio_comparison.percentile is not None:
        pct = peer.pe_ratio_comparison.percentile
        if pct <= 30:
            score += 6
            positive.append(f"P/E cheaper than {100-pct:.0f}% of peers")
            metrics["pe_peer_percentile"] = pct
        elif pct >= 85:
            score -= 6
            negative.append(f"P/E more expensive than {pct:.0f}% of peers")
            metrics["pe_peer_percentile"] = pct

    # Analyst consensus price target (bagian 6, upgrade 2026-07-25) — sinyal
    # eksternal (banyak analis) di luar valuasi absolut/peer di atas. Bobot
    # sedang (+/-8), sejajar peer percentile: pelengkap, bukan pengganti.
    pt = val.price_target
    if pt and pt.upside_pct is not None:
        if pt.upside_pct > 15:
            score += 8
            positive.append(f"Analyst consensus implies {pt.upside_pct:.0f}% upside")
            metrics["analyst_upside_pct"] = pt.upside_pct
        elif pt.upside_pct < -10:
            score -= 8
            negative.append(f"Analyst consensus implies {abs(pt.upside_pct):.0f}% downside")
            metrics["analyst_upside_pct"] = pt.upside_pct

    # recommendation_key & price_target_trend_3m (bagian 6) — dua sinyal
    # analyst-consensus TAMBAHAN di luar upside_pct di atas: recommendation_key
    # itu rating kategorikal langsung dari analis (bisa beda arah dari
    # upside_pct kalau target harganya basi/belum di-update), trend_3m itu
    # ARAH perubahan konsensus 3 bulan terakhir (momentum sentimen, bukan
    # level absolut). Bobot lebih kecil (+/-5) dari upside_pct karena
    # ketiganya sama-sama berasal dari analyst_estimates -- supaya satu
    # sumber data eksternal tidak mendominasi skor lensa cuma karena
    # direpresentasikan di 3 field berbeda.
    if pt and pt.recommendation_key:
        if pt.recommendation_key in ("strong_buy", "buy"):
            score += 5
            positive.append(f"Analyst rating: {pt.recommendation_key.replace('_', ' ')}")
            metrics["analyst_recommendation"] = pt.recommendation_key
        elif pt.recommendation_key in ("sell", "strong_sell"):
            score -= 5
            negative.append(f"Analyst rating: {pt.recommendation_key.replace('_', ' ')}")
            metrics["analyst_recommendation"] = pt.recommendation_key

    if pt and pt.price_target_trend_3m is not None:
        if pt.price_target_trend_3m > 10:
            score += 5
            positive.append(f"Analyst targets rising ({pt.price_target_trend_3m:.0f}% over 3M)")
            metrics["price_target_trend_3m"] = pt.price_target_trend_3m
        elif pt.price_target_trend_3m < -10:
            score -= 5
            negative.append(f"Analyst targets falling ({pt.price_target_trend_3m:.0f}% over 3M)")
            metrics["price_target_trend_3m"] = pt.price_target_trend_3m

    # EPS beat/miss streak (bagian 4) — "N/M beat" string diparse defensif
    # (try/except) karena formatnya dihasilkan Knowledge (_compute_earnings_
    # beat_streak), bukan data eksternal mentah, tapi tetap tidak boleh
    # crash pipeline kalau formatnya berubah di masa depan.
    if ht.earnings_beat_miss_streak:
        try:
            beats_str, total_str = ht.earnings_beat_miss_streak.split("/")
            beats, total = int(beats_str), int(total_str.split()[0])
            if total > 0:
                beat_rate = beats / total
                if beat_rate >= 0.75:
                    score += 6
                    positive.append(f"Strong EPS beat record ({ht.earnings_beat_miss_streak})")
                    metrics["eps_beat_streak"] = ht.earnings_beat_miss_streak
                elif beat_rate <= 0.25:
                    score -= 6
                    negative.append(f"Weak EPS beat record ({ht.earnings_beat_miss_streak})")
                    metrics["eps_beat_streak"] = ht.earnings_beat_miss_streak
        except (ValueError, IndexError):
            pass

    gov = profile.governance
    if len(gov.restatements or []) > 0:
        score -= 15
        negative.append(f"Financial restatements ({len(gov.restatements)})")
    elif len(gov.auditor_changes or []) > 0:
        score -= 8
        negative.append(f"Auditor changes ({len(gov.auditor_changes)})")

    if confidence and confidence.overall.score < 40:
        score -= 10
        negative.append("Low data confidence")

    if risk and risk.high_severity_count > 0:
        score -= 15
        negative.append(f"High-risk flags ({risk.high_severity_count})")

    # Insider Form 4 filing activity DELIBERATELY NOT scored here (removed post-audit,
    # 2026-07-24). Reasons: (1) D-12 scope — this module's own access-list (1,2,3a,4,6,7)
    # never included section 5 Ownership; (2) sec_form4.py's MVP fetch counts ANY Form 4
    # filing (grants/RSU-vesting/option-exercise included), it cannot isolate genuine
    # open-market buys (the parser that could, _parse_form4_xml, exists but is dead code),
    # so treating filing volume as "conviction" was unverified; (3) the same raw count was
    # being scored in Quality, Multibagger AND Speculative simultaneously — a single weak,
    # ambiguous signal voting three times violates the independent-lens principle (D-09).
    # See Speculative lens below for the (deliberately reduced) surviving use of this signal.

    score = max(0, min(100, score + 50))

    gaps = _knowledge_gaps(profile, "quality_compound")
    if score >= 70:
        stance = "compounding_kuat"
    elif score >= 45:
        stance = "compounding_rapuh"
    else:
        stance = "bukan_compounder"
    if len(gaps) >= 4:
        stance = "mesin_tak_terbaca"
    assert stance in QUALITY_STANCES

    return ModuleOutput(
        module="quality_compound",
        ticker=profile.ticker,
        exchange=profile.exchange,
        method_version=METHOD_VERSION,
        stance=stance,
        stance_rationale=f"Quality score {score:.0f}: " + (positive[0] if positive else (negative[0] if negative else "Mixed signals")),
        confidence=_module_confidence(confidence, gaps),
        flag_responses=_build_flag_responses(risk),
        context_used=_build_context_used(layer1, "quality_compound"),
        knowledge_gaps=gaps,
        generated_at=datetime.now(timezone.utc).isoformat(),
        positive_factors=positive,
        negative_factors=negative,
        key_metrics=metrics,
        score_breakdown={"fundamentals": (score - 50) / 50},
        fields_accessed=["identity", "financial_health", "historical_trend", "valuation", "governance"],
    )


def run_speculative_lens(
    profile: KnowledgeProfile,
    confidence: ConfidenceReport | None = None,
    risk: RiskAssessment | None = None,
    catalyst: CatalystSet | None = None,
    layer1: "MarketContextPackage | None" = None,
) -> ModuleOutput:
    """Speculative Lens — "Ada asimetri berkatalis?"

    Access: sections 1,3a,4-volatilitas,5 (Identity, Competitive Structure,
    Volatility, Ownership) + CatalystSet (Fase A, 10_CATALYST_TRACKING.md).

    Kosakata stance membedakan asimetri BERKATALIS vs TANPA katalis: asimetri
    (skor tinggi) + ada katalis mendatang terjadwal/diperkirakan =
    "asimetri_berkatalis"; asimetri tapi tidak ada katalis =
    "asimetri_tanpa_katalis". Katalis `rumored` sengaja TIDAK dihitung sebagai
    katalis di sini (spec: rumor menurunkan confidence, bukan menaikkan
    stance) — has_upcoming hanya True untuk scheduled/expected.
    """
    score = 0.0
    positive = []
    negative = []
    metrics = {}

    ht = profile.historical_trend
    if ht.volatility_daily is not None:
        if ht.volatility_daily > 4.0:
            score += 10
            positive.append("High volatility - trading opportunity")
            metrics["volatility_daily"] = ht.volatility_daily
        elif ht.volatility_daily < 1.0:
            score -= 5
            negative.append("Low volatility - boring")
            metrics["volatility_daily"] = ht.volatility_daily

    if ht.return_1y is not None and ht.return_1y > 30:
        score += 15
        positive.append("Strong momentum (>30% 1Y)")
        metrics["return_1y"] = ht.return_1y
    elif ht.return_1y is not None and ht.return_1y < -30:
        score -= 10
        negative.append("Negative momentum")
        metrics["return_1y"] = ht.return_1y

    own = profile.ownership
    if own.institutional_pct is not None and own.institutional_pct > 0.70:
        score += 8
        positive.append("High institutional ownership")
        metrics["institutional_pct"] = own.institutional_pct
    elif own.institutional_pct is not None and own.institutional_pct < 0.10:
        score -= 5
        negative.append("Low institutional ownership")
        metrics["institutional_pct"] = own.institutional_pct

    if len(own.insider_transactions or []) > 0:
        score += 10
        positive.append("Recent insider activity")
        metrics["insider_transactions"] = len(own.insider_transactions)

    if confidence and confidence.by_section["historical_trend"].score < 40:
        score -= 15
        negative.append("Insufficient price data for technical analysis")

    # Catalysts: explicit (earnings, events) + implicit (insider conviction via Form 4)
    has_catalyst = catalyst is not None and catalyst.has_upcoming
    if has_catalyst:
        upcoming = [c for c in catalyst.catalysts if c.certainty in ("scheduled", "expected")]
        nearest = min(upcoming, key=lambda c: c.expected_at)
        positive.append(f"Upcoming catalyst: {nearest.kind} {nearest.expected_at} ({nearest.certainty})")
        metrics["next_catalyst"] = f"{nearest.kind}@{nearest.expected_at}"

    # Insider Form 4 filing activity — reduced post-audit (2026-07-24). Was previously also
    # scored in Quality/Multibagger (removed there, D-09 lens-independence violation — a
    # single ambiguous signal shouldn't vote 3 times) and was flipping this lens's STANCE to
    # "asimetri_berkatalis" by treating filing count as a CatalystSet-equivalent event, which
    # is a category error (Catalyst = dated future event; this is a trailing 30-day count with
    # no forward date, never becomes a real Catalyst object). It also cannot distinguish a
    # genuine open-market buy from a routine grant/RSU-vest/option-exercise Form 4 (the parser
    # that could, sec_form4.py's _parse_form4_xml, is written but never called) — so it stays
    # here ONLY as a small factual point contributor (this lens already legitimately reads
    # Ownership per its own access-list), and — unlike before — can NOT by itself flip the
    # stance to "asimetri_berkatalis". A real forward-looking Catalyst is still required for
    # that stance.
    if own.insider_filing_activity_30d and own.insider_filing_activity_30d >= 2:
        score += 5
        positive.append(f"Recent Form 4 filing activity ({own.insider_filing_activity_30d} in 30d)")
        metrics["insider_filing_activity_30d"] = own.insider_filing_activity_30d

    score = max(0, min(100, score + 50))

    gaps = _knowledge_gaps(profile, "speculative")
    if score >= 60:
        # Asimetri terbaca — berkatalis HANYA kalau ada katalis kalender nyata (CatalystSet),
        # tanpa katalis kalau tidak. Insider filing activity TIDAK bisa memicu ini sendirian
        # (lihat catatan di atas).
        stance = "asimetri_berkatalis" if has_catalyst else "asimetri_tanpa_katalis"
    else:
        stance = "tanpa_asimetri"
    if len(gaps) >= 2:
        stance = "asimetri_tak_terbaca"
    assert stance in SPECULATIVE_STANCES

    return ModuleOutput(
        module="speculative",
        ticker=profile.ticker,
        exchange=profile.exchange,
        method_version=METHOD_VERSION,
        stance=stance,
        stance_rationale=f"Speculative score {score:.0f}: " + (positive[0] if positive else (negative[0] if negative else "Neutral momentum")),
        confidence=_module_confidence(confidence, gaps),
        flag_responses=_build_flag_responses(risk),
        context_used=_build_context_used(layer1, "speculative"),
        knowledge_gaps=gaps,
        generated_at=datetime.now(timezone.utc).isoformat(),
        positive_factors=positive,
        negative_factors=negative,
        key_metrics=metrics,
        score_breakdown={"momentum": (score - 50) / 50},
        fields_accessed=["identity", "historical_trend", "ownership"],
    )


def run_multibagger_lens(
    profile: KnowledgeProfile,
    confidence: ConfidenceReport | None = None,
    risk: RiskAssessment | None = None,
    peer: PeerComparisonResult | None = None,
    layer1: "MarketContextPackage | None" = None,
) -> ModuleOutput:
    """Multibagger Lens — "Ada ruang untuk kelipatan besar?"

    Access: sections 1,3a,3b,4,6 (Identity, Competitive Structure, Momentum,
    Historical, Valuation).
    """
    score = 0.0
    positive = []
    negative = []
    metrics = {}

    cs = profile.competitive_structure
    if cs.total_revenue_ttm is not None and cs.total_revenue_ttm > 100e6:
        if cs.tam_estimate and "large" in str(cs.tam_estimate).lower():
            score += 12
            positive.append("Large TAM - multibagger potential")
            metrics["tam_estimate"] = cs.tam_estimate

    cm = profile.competitive_momentum
    if cm.segment_growth and "high" in str(cm.segment_growth).lower():
        score += 15
        positive.append("High segment growth")
        metrics["segment_growth"] = cm.segment_growth
    elif cm.acceleration_signal and "positive" in str(cm.acceleration_signal).lower():
        score += 10
        positive.append("Positive acceleration signal")
        metrics["acceleration_signal"] = cm.acceleration_signal

    ht = profile.historical_trend
    if ht.return_1y is not None and ht.return_1y > 50:
        score += 12
        positive.append("Explosive 1Y return (>50%)")
        metrics["return_1y"] = ht.return_1y
    elif ht.return_1y is not None and ht.return_1y > 20:
        score += 8
        positive.append("Strong 1Y return (>20%)")
        metrics["return_1y"] = ht.return_1y

    val = profile.valuation
    if val.pe_ratio_trailing is not None:
        if val.pe_ratio_trailing > 50 and ht.return_1y and ht.return_1y > 30:
            score += 8
            positive.append("High growth justifies premium valuation")
            metrics["pe_ratio"] = val.pe_ratio_trailing
        elif val.pe_ratio_trailing > 100:
            score -= 10
            negative.append("Extreme valuation - limited upside")
            metrics["pe_ratio"] = val.pe_ratio_trailing

    # Peer: pertumbuhan revenue relatif — 07_MODULE_MULTIBAGGER.md butuh
    # posisi relatif buat menilai "pertumbuhan cepat dibanding sejenis, atau
    # cuma sektornya lagi naik semua". Bobot kecil, sama seperti Quality.
    if peer and peer.revenue_growth_comparison and peer.revenue_growth_comparison.percentile is not None:
        pct = peer.revenue_growth_comparison.percentile
        if pct >= 80:
            score += 6
            positive.append(f"Revenue growth faster than {pct:.0f}% of peers")
            metrics["revenue_growth_peer_percentile"] = pct

    # Analyst consensus price target (bagian 6, upgrade 2026-07-25) — dibaca
    # sebagai indikator "masih ada ruang naik" versi eksternal, sejalan
    # thesis modul ini. Bobot & threshold sama dengan Quality lens supaya
    # sinyal yang sama tidak diberi bobot berbeda-beda antar modul tanpa alasan.
    pt = val.price_target
    if pt and pt.upside_pct is not None:
        if pt.upside_pct > 15:
            score += 8
            positive.append(f"Analyst consensus sees {pt.upside_pct:.0f}% more room to run")
            metrics["analyst_upside_pct"] = pt.upside_pct
        elif pt.upside_pct < -10:
            score -= 8
            negative.append(f"Analyst consensus sees limited room ({pt.upside_pct:.0f}%)")
            metrics["analyst_upside_pct"] = pt.upside_pct

    # recommendation_key & price_target_trend_3m -- sama seperti Quality lens
    # di atas, bobot lebih kecil (+/-5) karena satu sumber data (analyst_
    # estimates) dengan upside_pct.
    if pt and pt.recommendation_key:
        if pt.recommendation_key in ("strong_buy", "buy"):
            score += 5
            positive.append(f"Analyst rating: {pt.recommendation_key.replace('_', ' ')}")
            metrics["analyst_recommendation"] = pt.recommendation_key
        elif pt.recommendation_key in ("sell", "strong_sell"):
            score -= 5
            negative.append(f"Analyst rating: {pt.recommendation_key.replace('_', ' ')}")
            metrics["analyst_recommendation"] = pt.recommendation_key

    if pt and pt.price_target_trend_3m is not None:
        if pt.price_target_trend_3m > 10:
            score += 5
            positive.append(f"Analyst targets rising ({pt.price_target_trend_3m:.0f}% over 3M)")
            metrics["price_target_trend_3m"] = pt.price_target_trend_3m
        elif pt.price_target_trend_3m < -10:
            score -= 5
            negative.append(f"Analyst targets falling ({pt.price_target_trend_3m:.0f}% over 3M)")
            metrics["price_target_trend_3m"] = pt.price_target_trend_3m

    if ht.earnings_beat_miss_streak:
        try:
            beats_str, total_str = ht.earnings_beat_miss_streak.split("/")
            beats, total = int(beats_str), int(total_str.split()[0])
            if total > 0:
                beat_rate = beats / total
                if beat_rate >= 0.75:
                    score += 6
                    positive.append(f"Consistent execution vs. estimates ({ht.earnings_beat_miss_streak})")
                    metrics["eps_beat_streak"] = ht.earnings_beat_miss_streak
                elif beat_rate <= 0.25:
                    score -= 6
                    negative.append(f"Inconsistent execution vs. estimates ({ht.earnings_beat_miss_streak})")
                    metrics["eps_beat_streak"] = ht.earnings_beat_miss_streak
        except (ValueError, IndexError):
            pass

    if confidence and confidence.overall.score < 40:
        score -= 12
        negative.append("Insufficient data for growth thesis")

    # Insider Form 4 filing activity DELIBERATELY NOT scored here (removed post-audit,
    # 2026-07-24) — same reasons as Quality lens above: D-12 scope (this module's own
    # access-list 1,3a,3b,4,6 never included section 5 Ownership), filing-count can't
    # distinguish real buys from routine grants/exercises, and it was voting in 3
    # independent lenses simultaneously (D-09 violation). See Speculative lens for the
    # surviving, reduced use of this signal.

    score = max(0, min(100, score + 50))

    gaps = _knowledge_gaps(profile, "multibagger")
    if score >= 70:
        stance = "ruang_terbuka"
    elif score >= 45:
        stance = "ruang_sempit"
    else:
        stance = "ruang_tertutup"
    if len(gaps) >= 3:
        stance = "ruang_tak_terbaca"
    assert stance in MULTIBAGGER_STANCES

    return ModuleOutput(
        module="multibagger",
        ticker=profile.ticker,
        exchange=profile.exchange,
        method_version=METHOD_VERSION,
        stance=stance,
        stance_rationale=f"Multibagger score {score:.0f}: " + (positive[0] if positive else (negative[0] if negative else "Modest growth profile")),
        confidence=_module_confidence(confidence, gaps),
        flag_responses=_build_flag_responses(risk),
        context_used=_build_context_used(layer1, "multibagger"),
        knowledge_gaps=gaps,
        generated_at=datetime.now(timezone.utc).isoformat(),
        positive_factors=positive,
        negative_factors=negative,
        key_metrics=metrics,
        score_breakdown={"growth": (score - 50) / 50},
        fields_accessed=["identity", "competitive_structure", "competitive_momentum", "historical_trend", "valuation"],
    )


def run_reasoning_pipeline(
    profile: KnowledgeProfile,
    confidence: ConfidenceReport | None = None,
    risk: RiskAssessment | None = None,
    peer: PeerComparisonResult | None = None,
    catalyst: CatalystSet | None = None,
    layer1: "MarketContextPackage | None" = None,
) -> ReasoningBundle:
    """Jalankan 3 reasoning lens independen — TIDAK diagregasi jadi satu
    skor/stance di sini (itu yang dilarang D-04). Sintesis non-memampatkan
    (agreements/divergences) dibangun terpisah di Fase 6 (aggregator.py)."""
    quality = run_quality_lens(profile, confidence, risk, peer, layer1)
    speculative = run_speculative_lens(profile, confidence, risk, catalyst, layer1)
    multibagger = run_multibagger_lens(profile, confidence, risk, peer, layer1)

    confidence_report_score = confidence.overall.score if confidence else None
    tinggi_flag_ids = [f.flag_id for f in risk.flags if f.severity == "tinggi"] if risk else []
    for output in (quality, speculative, multibagger):
        violations = validate_module_output(output, tinggi_flag_ids, confidence_report_score)
        if violations:
            # Tidak menghentikan pipeline (satu ticker gagal validasi bukan
            # alasan gagalkan seluruh run) — tapi harus terlihat, bukan
            # ditelan diam-diam (bertentangan dengan Prinsip #4/#5 itu
            # sendiri kalau pelanggaran definisi jujur ini malah disembunyikan).
            import sys
            print(f"  WARNING {profile.ticker}/{output.module}: {violations}", file=sys.stderr)

    return ReasoningBundle(
        ticker=profile.ticker,
        exchange=profile.exchange,
        multibagger=multibagger,
        quality_compound=quality,
        speculative=speculative,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
