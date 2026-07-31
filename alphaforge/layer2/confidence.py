"""Confidence module — Layer 2 Fase B, stage 2: Data Quality Scoring.

Menghasilkan ConfidenceReport (Data Contracts §5c) per Knowledge profile:
seberapa kuat DATA yang menopang saham ini, dipecah per section Knowledge
(bukan per-provider seperti versi sebelumnya) plus penalti eksplisit dari
kualitas peer group dan kondisi Layer 1 saat profil ini disusun.

Bobot & ambang di bawah adalah kalibrasi awal (05_CONFIDENCE_DATA_QUALITY.md
sendiri menandainya "perlu dikalibrasi saat implementasi", bukan angka
final) — dipertahankan dari versi sebelumnya sejauh strukturnya masih pas
dengan 7 section Knowledge yang baru.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .confidence_contracts import (
    ConfidenceReport, OverallConfidence, SectionScore, PeerPenalty, ContextPenalty,
    RecencyPenalty,
)

if TYPE_CHECKING:
    from .knowledge_contracts import KnowledgeProfile
    from .peer_contracts import PeerComparisonResult

METHOD_VERSION = "2.0"

# Bobot tiap section Knowledge dalam overall.score — 03_KNOWLEDGE.md bagian
# 1 (Identitas) sengaja tidak diberi bobot: isinya kategorikal (sector,
# size_category, screening_flags), bukan field numerik yang "lengkap/tidak
# lengkap" secara bermakna.
SECTION_WEIGHTS = {
    "financial_health": 0.20,
    "valuation": 0.15,
    "historical_trend": 0.15,
    "competitive_structure": 0.15,
    "competitive_momentum": 0.05,
    "ownership": 0.15,
    "governance": 0.15,
}

PEER_PENALTY_POINTS = 10.0
CONTEXT_PENALTY_PER_COMPONENT = 5.0
CONTEXT_PENALTY_CAP = 15.0
# 05_CONFIDENCE_DATA_QUALITY.md §2: "data fundamental yang berumur lebih dari
# 1 kuartal penuh menurunkan skor" — 1 kuartal kalender ~91 hari. Poin penalti
# belum dikalibrasi spec ("didiskusikan lebih lanjut mendekati fase
# implementasi") — dipakai magnitude yang sama dengan PEER_PENALTY_POINTS
# sebagai starting point yang konsisten, bukan angka final.
RECENCY_PENALTY_THRESHOLD_DAYS = 91
RECENCY_PENALTY_POINTS = 10.0

# Audit 2026-07-30 item A7: menghapus 7 cek yang tak pernah terisi (lihat
# docstring _score_valuation/_score_competitive_structure/
# _score_competitive_momentum/_score_ownership) menaikkan DENOMINATOR 4
# section berbobot gabungan 0.50 (valuation 0.15 + competitive_structure 0.15
# + competitive_momentum 0.05 + ownership 0.15). Skor maksimum yang bisa
# dicapai naik dari 81.4 ke 100 tanpa ambang band (70/40, dipertahankan dari
# versi sebelum audit) ikut disesuaikan -- ticker yang dulu mentok di plafon
# lama naik ~18.6 poin walau data mentahnya tidak berubah sama sekali.
#
# 70/40 di sini adalah 70/81.381 dan 40/81.381 dikali balik ke skala baru
# (100/81.381 ≈ 1.2288): OLD_MAX = 100%*0.20 (financial_health, tak
# terpengaruh) + 5/7*100%*0.15 (valuation, 5 cek asli/7 lama) + 100%*0.15
# (historical_trend, tak terpengaruh) + 3/5*100%*0.15 (competitive_structure)
# + 1/3*100%*0.05 (competitive_momentum) + 2/3*100%*0.15 (ownership) +
# 100%*0.15 (governance, sudah diperbaiki di audit sebelumnya, tak
# terpengaruh di sini) = 81.381.
#
# CATATAN KETEPATAN: rescale linear ini EKSAK untuk ticker yang tingkat
# kelengkapan datanya proporsional sama di semua section (skenario paling
# umum -- ticker dengan cakupan data buruk cenderung buruk di semua section,
# bukan selektif). Untuk ticker dengan pola kelengkapan yang timpang antar
# section (mis. sempurna di 4 section yang berubah tapi lemah di 3 section
# yang tidak), pemetaan skor lama->baru TIDAK bijektif murni dari skor
# gabungan saja (nilai per-section yang menentukan) -- lihat docstring
# assess_confidence untuk perinciannya. Kalibrasi ini belum divalidasi
# terhadap distribusi skor produksi nyata (tidak ada snapshot knowledge.json
# di lingkungan pengembangan saat perbaikan ini dibuat); validasi ulang
# begitu ada run produksi baru tersedia, sama seperti bobot/ambang lain di
# modul ini yang sudah ditandai "kalibrasi awal, bukan angka final" (lihat
# docstring modul).
BAND_HIGH_THRESHOLD = 86.0
BAND_MEDIUM_THRESHOLD = 49.0


def assess_confidence(
    knowledge_profile: KnowledgeProfile,
    peer_comparison: PeerComparisonResult | None = None,
    components_degraded: list[str] | None = None,
) -> ConfidenceReport:
    """Assess confidence untuk satu Knowledge profile.

    Args:
        knowledge_profile: KnowledgeProfile dari Fase A
        peer_comparison: PeerComparisonResult dari Fase B stage 1 (optional)
        components_degraded: nama komponen Layer 1 yang status != "ok" saat
            profil ini disusun (optional — kalau tidak diisi, context_penalty
            tidak diterapkan, bukan dianggap "semua ok").

    Returns:
        ConfidenceReport (Data Contracts §5c)
    """
    ticker = knowledge_profile.ticker
    exchange = knowledge_profile.exchange

    by_section = {
        "financial_health": _score_financial_health(knowledge_profile),
        "valuation": _score_valuation(knowledge_profile),
        "historical_trend": _score_historical_trend(knowledge_profile),
        "competitive_structure": _score_competitive_structure(knowledge_profile),
        "competitive_momentum": _score_competitive_momentum(knowledge_profile),
        "ownership": _score_ownership(knowledge_profile),
        "governance": _score_governance(knowledge_profile),
    }
    _apply_screening_flag_penalties(by_section, knowledge_profile.screening_flags)

    base_score = sum(
        by_section[name].score * weight for name, weight in SECTION_WEIGHTS.items()
    )

    peer_penalty = _assess_peer_penalty(peer_comparison)
    context_penalty = _assess_context_penalty(components_degraded)
    evidence_age = _get_data_age_days(knowledge_profile.metadata.evidence_date)
    recency_penalty = _assess_recency_penalty(evidence_age)

    score = base_score
    if peer_penalty.applied:
        score -= PEER_PENALTY_POINTS
    if context_penalty.applied:
        score -= min(
            len(context_penalty.components_degraded) * CONTEXT_PENALTY_PER_COMPONENT,
            CONTEXT_PENALTY_CAP,
        )
    if recency_penalty.applied:
        score -= RECENCY_PENALTY_POINTS
    score = max(0.0, min(100.0, score))

    if score >= BAND_HIGH_THRESHOLD:
        band = "high"
    elif score >= BAND_MEDIUM_THRESHOLD:
        band = "medium"
    else:
        band = "low"

    limiters: list[str] = []
    if band != "high":
        # Section-section paling lemah (< 50% lengkap) — bukan semua yang
        # kurang dari 100%, biar limiters tidak berisik untuk profil yang
        # sebenarnya wajar (data itu memang tidak semuanya pernah 100%).
        for name, sec in by_section.items():
            if sec.score < 50:
                if name == "competitive_momentum":
                    # Audit item A8: tersisa 1 cek (acceleration_signal),
                    # yang butuh revenue YoY q3 DAN q4 dari SEC EDGAR --
                    # proksi cakupan histori EDGAR, bukan "data hilang"
                    # generik. Pesan default "data incomplete (0/1)"
                    # menyesatkan (kedengaran seperti gagal fetch); yang
                    # sebenarnya terjadi: kurang dari 2 kuartal revenue
                    # berurutan untuk dibandingkan.
                    limiters.append(
                        "competitive_momentum: revenue YoY quarter-over-quarter "
                        "tidak tersedia (perlu histori SEC EDGAR untuk 2 "
                        "kuartal berurutan)"
                    )
                else:
                    limiters.append(f"{name} data incomplete ({sec.filled}/{sec.expected})")
        if peer_penalty.applied:
            limiters.append(peer_penalty.reason)
        if context_penalty.applied:
            limiters.append(
                f"Layer 1 context degraded: {', '.join(context_penalty.components_degraded)}"
            )
        if recency_penalty.applied:
            limiters.append(recency_penalty.reason)
        # V3 (Data Contracts): limiters WAJIB terisi kalau band != "high". Kasus
        # nyata yang bisa lolos semua cek di atas tanpa masuk sini: band="medium"
        # dari rata-rata tertimbang section yang moderat merata (tidak ada satu
        # pun <50%) dan tidak ada peer/context/recency penalty — sebelumnya
        # menghasilkan limiters=[] padahal band != "high" (V3 violation nyata,
        # ditemukan audit 2026-07-24). Fallback generik, bukan alasan spesifik
        # palsu, supaya tidak menyembunyikan band non-high tanpa penjelasan sama
        # sekali.
        if not limiters:
            limiters.append("Overall data quality moderate (no single factor critical, but combined score below high threshold)")

    return ConfidenceReport(
        ticker=ticker,
        exchange=exchange,
        method_version=METHOD_VERSION,
        overall=OverallConfidence(score=round(score, 1), band=band, limiters=limiters),
        by_section=by_section,
        peer_penalty=peer_penalty,
        context_penalty=context_penalty,
        recency_penalty=recency_penalty,
        evidence_age_days=evidence_age,
        assessed_at=datetime.now(timezone.utc).isoformat(),
    )


def _section_score(checks: list[bool]) -> SectionScore:
    filled = sum(1 for c in checks if c)
    expected = len(checks)
    pct = (filled / expected * 100) if expected > 0 else 0.0
    return SectionScore(filled=filled, expected=expected, score=round(pct, 1))


def _score_financial_health(profile: KnowledgeProfile) -> SectionScore:
    """03_KNOWLEDGE.md bagian 2."""
    fh = profile.financial_health
    return _section_score([
        fh.revenue_trend.yoy_q4 is not None,
        fh.gross_margin_trend.q4 is not None,
        fh.operating_margin_trend.q4 is not None,
        fh.net_margin_trend.q4 is not None,
        fh.balance_sheet.debt_to_equity is not None,
        fh.balance_sheet.current_ratio is not None,
        fh.balance_sheet.quick_ratio is not None,
        fh.cash_flow_trend.fcf_q4 is not None,
        fh.capex_info.capex_nominal_q4 is not None,
    ])


def _score_valuation(profile: KnowledgeProfile) -> SectionScore:
    """03_KNOWLEDGE.md bagian 6. Rasio yang null karena secara matematis
    tidak bermakna (mis. P/E rugi) tetap dihitung sebagai "missing" di sini
    — Confidence tidak tahu bedanya dari sini, itu keputusan modul reasoning
    yang membacanya (lihat catatan spec bagian 6).

    Audit 2026-07-30: `pe_ratio_forward` & `ev_ebitda` dihapus dari daftar cek,
    pola yang sama dengan _score_governance. Keduanya TIDAK PERNAH diisi
    knowledge.py (`ev_ebitda=None  # TODO`, dan forward-PE tidak ada sama sekali
    di FundamentalData), jadi menghitungnya sebagai "data hilang" adalah penalti
    konstan ke SEMUA ticker — plafon section ini macet 5/7 (71%) walau data
    ticker sempurna. Sekarang 5/5 achievable."""
    val = profile.valuation
    return _section_score([
        val.pe_ratio_trailing is not None,
        val.ps_ratio is not None,
        val.pb_ratio is not None,
        val.fcf_yield is not None,
        val.price_target is not None and val.price_target.upside_pct is not None,
    ])


def _score_historical_trend(profile: KnowledgeProfile) -> SectionScore:
    """03_KNOWLEDGE.md bagian 4."""
    ht = profile.historical_trend
    return _section_score([
        ht.return_1y is not None,
        ht.return_3y is not None,
        ht.return_5y is not None,
        ht.volatility_daily is not None,
        ht.beta is not None,
        ht.earnings_beat_miss_streak is not None,
    ])


def _score_competitive_structure(profile: KnowledgeProfile) -> SectionScore:
    """03_KNOWLEDGE.md bagian 3a.

    Audit 2026-07-30: `revenue_by_segment` & `tam_estimate` dihapus dari cek —
    tidak pernah di-set knowledge.py (CompetitiveStructure dibangun hanya dengan
    business_model/total_revenue_ttm/employees_count), jadi plafon macet 3/5
    (60%) untuk semua ticker. Sekarang 3/3 achievable."""
    cs = profile.competitive_structure
    return _section_score([
        cs.business_model is not None,
        cs.total_revenue_ttm is not None,
        cs.employees_count is not None,
    ])


def _score_competitive_momentum(profile: KnowledgeProfile) -> SectionScore:
    """03_KNOWLEDGE.md bagian 3b.

    Audit 2026-07-30: `segment_growth` & `guidance_trend` dihapus dari cek —
    knowledge.py men-set keduanya `None` secara eksplisit dan permanen (Evidence
    tidak mengumpulkan datanya; gap yang sudah didokumentasikan di sana), jadi
    plafon macet 1/3 (33%) untuk semua ticker. Tersisa satu cek yang benar-benar
    bervariasi antar ticker (acceleration_signal butuh revenue YoY q3+q4)."""
    cm = profile.competitive_momentum
    return _section_score([
        cm.acceleration_signal is not None,
    ])


def _score_ownership(profile: KnowledgeProfile) -> SectionScore:
    """03_KNOWLEDGE.md bagian 5.

    Audit 2026-07-30: cek `insider_transactions` dihapus — knowledge.py
    men-hardcode `[]` (TODO yang secara struktural tak terjangkau: fetcher SEC
    EDGAR menyaring keluar Form 3/4/144), jadi plafon macet 2/3 (67%) untuk
    semua ticker. Sekarang 2/2 achievable."""
    own = profile.ownership
    return _section_score([
        own.institutional_pct is not None,
        own.insider_pct is not None,
    ])


def _score_governance(profile: KnowledgeProfile) -> SectionScore:
    """03_KNOWLEDGE.md bagian 7.

    Audit 2026-07-29: 3 cek dihapus (auditor_changes/restatements/
    material_litigation, semua len(list)>0). Ketiganya BUKAN kejadian
    langka yang kebetulan kosong -- knowledge.py men-hardcode ketiganya []
    SELALU (Evidence tidak pernah mengumpulkan isi filing yang dibutuhkan,
    lihat _build_governance), jadi menghitungnya sebagai "data hilang"
    adalah PENALTI KONSTAN ke setiap ticker tanpa kecuali, bukan sinyal
    kualitas data yang membedakan satu ticker dari ticker lain. unusual_
    filings juga diganti checknya dari "ada amandemen" (event langka, sama
    masalahnya dengan 3 field di atas -- ticker sehat wajar nol amandemen)
    ke `filing_data_available` (fetch SEC EDGAR berhasil atau tidak) --
    inilah yang benar-benar membedakan "berhasil dicek, nihil" vs "gagal
    dicek". Plafon section ini naik dari 40% (2/5) ke 100% (2/2 achievable)."""
    gov = profile.governance
    return _section_score([
        gov.shares_outstanding_change_12m is not None,
        gov.filing_data_available,
    ])


# 05_CONFIDENCE_DATA_QUALITY.md komponen #4: flag Screening menurunkan
# confidence pada BAGIAN data yang terkait, bukan skor keseluruhan — dua
# contoh eksplisit di spec dipetakan di sini. Flag lain (low_liquidity, adr,
# market_cap_unavailable) sengaja tidak dipetakan: tidak ada section target
# yang jelas tanpa membuat keputusan pemetaan sendiri di luar yang dicontohkan
# spec.
SCREENING_FLAG_SECTION_PENALTY = {
    "no_institutional_data": ("ownership", 30.0),
    "recent_ipo": ("historical_trend", 20.0),
}


def _apply_screening_flag_penalties(by_section: dict[str, SectionScore], screening_flags: list[str]) -> None:
    for flag_name, (section, penalty) in SCREENING_FLAG_SECTION_PENALTY.items():
        if flag_name in (screening_flags or []) and section in by_section:
            sec = by_section[section]
            sec.score = max(0.0, round(sec.score - penalty, 1))


def _assess_peer_penalty(peer_comparison: PeerComparisonResult | None) -> PeerPenalty:
    if peer_comparison is None:
        return PeerPenalty(applied=True, reason="No peer comparison available")
    if peer_comparison.low_sample_size:
        return PeerPenalty(
            applied=True,
            reason=f"Peer group too small ({peer_comparison.peer_group.group_size} peers)",
        )
    if peer_comparison.peer_group.peer_failures:
        return PeerPenalty(
            applied=True,
            reason=f"{len(peer_comparison.peer_group.peer_failures)} peer(s) failed to be retrieved",
        )
    return PeerPenalty(applied=False)


def _assess_context_penalty(components_degraded: list[str] | None) -> ContextPenalty:
    if not components_degraded:
        return ContextPenalty(applied=False)
    return ContextPenalty(applied=True, components_degraded=list(components_degraded))


def _assess_recency_penalty(evidence_age_days: int | None) -> RecencyPenalty:
    if evidence_age_days is None or evidence_age_days <= RECENCY_PENALTY_THRESHOLD_DAYS:
        return RecencyPenalty(applied=False, age_days=evidence_age_days)
    return RecencyPenalty(
        applied=True,
        age_days=evidence_age_days,
        reason=f"Evidence {evidence_age_days} days old (>1 full quarter, {RECENCY_PENALTY_THRESHOLD_DAYS}d threshold)",
    )


def _get_data_age_days(iso_datetime: str | None) -> int | None:
    """Get age of data in days."""
    if not iso_datetime:
        return None

    try:
        data_time = datetime.fromisoformat(iso_datetime)
        if data_time.tzinfo is None:
            data_time = data_time.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - data_time).days
    except (ValueError, AttributeError):
        return None


def run_confidence(
    profiles: list[KnowledgeProfile],
    comparisons: list[PeerComparisonResult] | None = None,
    components_degraded: list[str] | None = None,
) -> list[ConfidenceReport]:
    """Run confidence scoring untuk semua profiles.

    Args:
        profiles: List of KnowledgeProfile dari Fase A
        comparisons: List of PeerComparisonResult dari Fase B stage 1 (optional)
        components_degraded: nama komponen Layer 1 yang degraded/missing di
            sesi ini (optional, sama untuk semua ticker dalam satu sesi)

    Returns:
        List of ConfidenceReport
    """
    peer_map = {}
    if comparisons:
        for comp in comparisons:
            peer_map[comp.ticker] = comp

    reports = []
    for profile in profiles:
        peer_comp = peer_map.get(profile.ticker)
        report = assess_confidence(profile, peer_comp, components_degraded)
        reports.append(report)

    return reports
