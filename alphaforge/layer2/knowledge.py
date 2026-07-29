"""Layer 2 tahap 3: Knowledge — structured understanding dari Evidence per ticker.

Bukan data mentah lagi, tapi profil terstruktur: tren, kategori, fakta terukur.
Tanpa penilaian kualitatif (itu tugas Confidence/Reasoning/Risk).

Input: list EvidencePackage dari Evidence
Output: list KnowledgeProfile

Lihat 03_LAYER2_SPECS/03_KNOWLEDGE.md.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from .contracts import EvidencePackage, ScreeningCandidate
from .knowledge_contracts import (
    KnowledgeProfile, KnowledgeMetadata, FinancialHealth, Ownership,
    RevenueTrend, MarginTrend, BalanceSheet, CashFlowTrend, CapExInfo,
    CompetitiveStructure, CompetitiveMomentum, HistoricalTrend, Valuation, Governance,
    GovernanceEvent, PriceTargetMetrics
)
from .knowledge_helpers import (
    calculate_returns, calculate_volatility,
    calculate_financial_metrics, infer_size_category, compute_financial_trends,
    calculate_price_target_metrics, infer_business_model
)


def build_knowledge_for_ticker(evidence: EvidencePackage, candidate: ScreeningCandidate | None = None) -> KnowledgeProfile:
    """Bangun Knowledge Profile dari satu Evidence package.

    #1 Tren Calculation — parse price_history untuk returns, volatility
    #2 Financial Trends — compute dari fundamental fields
    #3 Field Extraction — better metrics calculation
    #4 Screening Flags — map ke Knowledge structure
    """

    # 1. Identitas & Klasifikasi
    screening_flags = list(candidate.soft_flags) if candidate else []
    # no_institutional_data baru bisa diketahui di sini (Knowledge), bukan
    # Screening — kepemilikan institusional cuma ada setelah Evidence fetch
    # Yahoo .info, panggilan yang sengaja tidak dilakukan di Screening biar
    # tetap murah di skala full-market (lihat sources/yahoo_evidence.py).
    if not evidence.institutional_ownership.percentage:
        screening_flags.append("no_institutional_data")
    size_category = infer_size_category(evidence.price_market.market_cap, screening_flags)
    sector = evidence.fundamental.sector

    # #1: TREN CALCULATION — Parse price_history
    # (high_52w/low_52w tidak dihitung ulang di sini — sudah tersedia langsung
    # dari Evidence via evidence.price_market.high_52w/low_52w dari Yahoo)
    price_history = evidence.price_market.price_history or []
    returns = calculate_returns(price_history)
    volatility = calculate_volatility(price_history)

    # 2. Kesehatan Finansial
    # #2: Compute trends dari quarterly data jika ada
    trends = compute_financial_trends(evidence.fundamental.quarterly_data)

    # #3: FIELD EXTRACTION — dihitung di sini (bukan di bawah) supaya
    # metrics['net_margin_pct'] bisa dipakai sebagai fallback net_margin_q4
    # yang benar kalau data kuartalan kosong (lihat net_margin_trend di bawah)
    metrics = calculate_financial_metrics(
        revenue=evidence.fundamental.revenue,
        net_income=evidence.fundamental.net_income,
        free_cash_flow=evidence.fundamental.free_cash_flow,
        market_cap=evidence.price_market.market_cap,
        shares_outstanding=evidence.price_market.shares_outstanding,
        last_price=evidence.price_market.last_price,
        eps=evidence.fundamental.eps,
        book_value_per_share=evidence.fundamental.book_value_per_share
    )

    # Nominal CapEx per kuartal dari quarterly_data — quarterly_data[0] =
    # kuartal terbaru (q4), [1]=q3, [2]=q2, [3]=q1, sama seperti pemetaan
    # di compute_financial_trends().
    qdata = evidence.fundamental.quarterly_data or []
    capex_nominal = {}
    for i, period in enumerate(qdata[:4]):
        q_key = f"capex_nominal_q{4 - i}"
        capex_nominal[q_key] = getattr(period, "capital_expenditures", None)

    financial_health = FinancialHealth(
        revenue_trend=RevenueTrend(
            yoy_q1=trends.get('revenue_yoy_q1'),
            yoy_q2=trends.get('revenue_yoy_q2'),
            yoy_q3=trends.get('revenue_yoy_q3'),
            yoy_q4=trends.get('revenue_yoy_q4')
        ),
        gross_margin_trend=MarginTrend(
            q1=trends.get('gross_margin_q1'),
            q2=trends.get('gross_margin_q2'),
            q3=trends.get('gross_margin_q3'),
            q4=trends.get('gross_margin_q4')
        ),
        operating_margin_trend=MarginTrend(
            q1=trends.get('operating_margin_q1'),
            q2=trends.get('operating_margin_q2'),
            q3=trends.get('operating_margin_q3'),
            q4=trends.get('operating_margin_q4')
        ),
        net_margin_trend=MarginTrend(
            q1=trends.get('net_margin_q1'),
            q2=trends.get('net_margin_q2'),
            q3=trends.get('net_margin_q3'),
            # Fallback kalau data kuartalan kosong: net_income/revenue TTM
            # dari fundamental (metrics['net_margin_pct']) — BUKAN
            # operating_margin seperti sebelumnya, yang mensubstitusi metrik
            # yang beda sama sekali sebagai "net margin".
            q4=trends.get('net_margin_q4') if trends.get('net_margin_q4') is not None else metrics['net_margin_pct']
        ),
        balance_sheet=BalanceSheet(
            debt_to_equity=evidence.fundamental.debt_to_equity,
            current_ratio=evidence.fundamental.current_ratio,
            quick_ratio=evidence.fundamental.quick_ratio
        ),
        cash_flow_trend=CashFlowTrend(
            fcf_q4=evidence.fundamental.free_cash_flow,
            fcf_margin_q4=_fcf_margin_pct(evidence.fundamental.free_cash_flow, evidence.fundamental.revenue)
        ),
        capex_info=CapExInfo(
            capex_nominal_q1=capex_nominal.get('capex_nominal_q1'),
            capex_nominal_q2=capex_nominal.get('capex_nominal_q2'),
            capex_nominal_q3=capex_nominal.get('capex_nominal_q3'),
            capex_nominal_q4=capex_nominal.get('capex_nominal_q4'),
            capex_pct_revenue_q4=trends.get('capex_pct_revenue_q4')
        ),
        # ROE/ROA dari Yahoo (evidence.fundamental) sudah difetch sejak awal
        # tapi sebelumnya tidak pernah dibawa ke Knowledge — Peer's
        # roe_comparison/roa_comparison jadi selalu None/pakai data yang salah
        # (institutional_pct sebagai pengganti). Lihat audit 2026-07-24.
        roe=evidence.fundamental.roe,
        roa=evidence.fundamental.roa,
    )

    # 3a. Struktur Kompetitif
    competitive_structure = CompetitiveStructure(
        business_model=infer_business_model(evidence.fundamental.industry),
        total_revenue_ttm=evidence.fundamental.revenue,
        employees_count=evidence.company_profile.employees if evidence.company_profile else None
    )

    # 3b. Momentum — spec 03_KNOWLEDGE.md §3b.
    # Hanya acceleration_signal yang derivable dari Evidence saat ini: dibangun
    # dari revenue_yoy_q3/q4 (trends, dihitung di atas via compute_financial_trends)
    # — deskripsi faktual murni ("growth accelerating/decelerating"), sama
    # seperti contoh di spec, tanpa kata sifat evaluatif.
    # segment_growth & guidance_trend tetap None: Evidence tidak mengumpulkan
    # revenue per segmen (butuh parsing 10-K segment reporting) atau data
    # guidance vs konsensus analis — dua-duanya sumber data yang belum ada di
    # Evidence stage, bukan bug di Knowledge.
    competitive_momentum = CompetitiveMomentum(
        segment_growth=None,
        guidance_trend=None,
        acceleration_signal=_compute_acceleration_signal(trends)
    )

    # 4. Tren Historis
    # #1: use calculated returns & volatility
    historical_trend = HistoricalTrend(
        return_1y=returns.get('return_1y'),
        return_3y=returns.get('return_3y'),
        return_5y=returns.get('return_5y'),
        volatility_daily=volatility,
        beta=evidence.price_market.beta,
        earnings_beat_miss_streak=_compute_earnings_beat_streak(
            evidence.analyst_estimates.eps_surprise_history if evidence.analyst_estimates else []
        )
    )

    # 5. Kepemilikan
    ownership = Ownership(
        institutional_pct=evidence.institutional_ownership.percentage,
        # heldPercentInsiders sudah difetch di Evidence (yahoo_evidence.py,
        # objek .info yang sama dengan institutional_pct) tapi sebelumnya
        # tidak pernah dibawa ke sini -- 0% terisi di seluruh universe
        # (audit 2026-07-29), bukan karena datanya memang jarang ada.
        insider_pct=evidence.institutional_ownership.insider_percentage,
        insider_transactions=[],  # TODO: dari news/SEC filings
        insider_filing_activity_30d=evidence.institutional_activity.buy_count_30d  # Form 4 filings count
    )

    # 6. Valuasi
    # #3: FIELD EXTRACTION — metrics sudah dihitung di atas (dipakai juga
    # untuk fallback net_margin_q4)
    # Price Target Metrics dari analyst estimates
    price_target_metrics = None
    if evidence.analyst_estimates:
        from dataclasses import asdict
        analyst_data = {
            'target_mean': evidence.analyst_estimates.target_mean,
            'target_median': evidence.analyst_estimates.target_median,
            'target_low': evidence.analyst_estimates.target_low,
            'target_high': evidence.analyst_estimates.target_high,
            'num_analyst_opinions': evidence.analyst_estimates.num_analyst_opinions,
            'recommendation_key': evidence.analyst_estimates.recommendation_key
        }
        pt_metrics = calculate_price_target_metrics(
            current_price=evidence.price_market.last_price,
            analyst_estimates=analyst_data,
            price_target_history=evidence.analyst_estimates.price_target_history
        )
        price_target_metrics = PriceTargetMetrics(
            current_price=pt_metrics.get('current_price'),
            target_mean=pt_metrics.get('target_mean'),
            target_median=pt_metrics.get('target_median'),
            target_low=pt_metrics.get('target_low'),
            target_high=pt_metrics.get('target_high'),
            num_analysts=pt_metrics.get('num_analysts'),
            upside_pct=pt_metrics.get('upside_pct'),
            recommendation_key=pt_metrics.get('recommendation_key'),
            price_target_trend_3m=pt_metrics.get('price_target_trend_3m')
        )

    valuation = Valuation(
        pe_ratio_trailing=evidence.fundamental.pe_ratio,
        ps_ratio=metrics['ps_ratio'],
        ev_ebitda=None,  # TODO: compute jika EBITDA ada
        pb_ratio=metrics['pb_ratio'],
        fcf_yield=metrics['fcf_yield_pct'],
        price_target=price_target_metrics
    )

    # 7. Governance — spec 03_KNOWLEDGE.md §7.
    governance = _build_governance(evidence)

    # Count completed fields untuk Confidence downstream
    completed_fields, expected_fields, missing_fields = _count_completed_fields(
        returns, volatility, financial_health, ownership, valuation,
        competitive_structure, historical_trend
    )

    # Metadata
    metadata = KnowledgeMetadata(
        evidence_date=evidence.generated_at,
        method_version="1.1",
        fields_completed=completed_fields,
        fields_expected=expected_fields,
        missing_fields=missing_fields,
        sources_used=_extract_sources(evidence),
        data_quality_notes=_generate_quality_notes(evidence, returns, volatility)
    )

    return KnowledgeProfile(
        ticker=evidence.ticker,
        exchange=evidence.exchange,
        sector=sector,
        size_category=size_category,
        screening_flags=screening_flags,
        financial_health=financial_health,
        competitive_structure=competitive_structure,
        competitive_momentum=competitive_momentum,
        historical_trend=historical_trend,
        ownership=ownership,
        valuation=valuation,
        governance=governance,
        metadata=metadata
    )


def _fcf_margin_pct(fcf: float | None, revenue: float | None) -> float | None:
    """Calculate FCF margin % (FCF / Revenue)."""
    if not fcf or not revenue or revenue <= 0:
        return None
    return (fcf / revenue) * 100


def _count_completed_fields(
    returns: dict, volatility: float | None, financial_health, ownership, valuation,
    competitive_structure, historical_trend
) -> tuple[int, int, list[str]]:
    """#5: Count non-null fields untuk data quality tracking.

    Returns (fields_completed, fields_expected, missing_fields) — semuanya
    dihitung dari list checks yang sama, supaya fields_expected otomatis
    selalu sama dengan jumlah field yang benar-benar dicek di sini
    (sebelumnya fields_expected di-hardcode ke 50 di caller padahal cuma
    ~13 field yang pernah dicek). missing_fields membawa NAMA field yang
    kosong, bukan cuma hitungannya — dibutuhkan Data Contracts §4/§6 (V4:
    field Knowledge yang missing dan relevan untuk suatu modul reasoning
    wajib muncul di knowledge_gaps modul itu; tidak bisa dipenuhi dari
    angka completeness saja).
    """
    checks = [
        ("historical_trend.return_1y", returns.get('return_1y')),
        ("historical_trend.return_3y", returns.get('return_3y')),
        ("historical_trend.return_5y", returns.get('return_5y')),
        ("historical_trend.volatility_daily", volatility),
        ("financial_health.balance_sheet.debt_to_equity", financial_health.balance_sheet.debt_to_equity),
        ("financial_health.balance_sheet.current_ratio", financial_health.balance_sheet.current_ratio),
        ("financial_health.balance_sheet.quick_ratio", financial_health.balance_sheet.quick_ratio),
        ("financial_health.cash_flow_trend.fcf_q4", financial_health.cash_flow_trend.fcf_q4),
        ("financial_health.roe", financial_health.roe),
        ("financial_health.roa", financial_health.roa),
        ("ownership.institutional_pct", ownership.institutional_pct),
        ("valuation.pe_ratio_trailing", valuation.pe_ratio_trailing),
        ("valuation.ps_ratio", valuation.ps_ratio),
        ("valuation.pb_ratio", valuation.pb_ratio),
        ("valuation.fcf_yield", valuation.fcf_yield),
        ("valuation.price_target.upside_pct", valuation.price_target.upside_pct if valuation.price_target else None),
        ("competitive_structure.employees_count", competitive_structure.employees_count),
        ("historical_trend.earnings_beat_miss_streak", historical_trend.earnings_beat_miss_streak),
    ]
    # "is not None", BUKAN truthy check -- return_1y=0.0 (flat), debt_to_equity=0.0
    # (bebas utang), roe/roa=0.0 (breakeven) semuanya nilai VALID, bukan data
    # hilang. Truthy check menghitungnya sebagai "missing" secara diam-diam
    # (bug class yang sama yang ditemukan di peer.py, audit 2026-07-24).
    fields_completed = sum(1 for _, v in checks if v is not None)
    fields_expected = len(checks)
    missing_fields = [name for name, v in checks if v is None]
    return fields_completed, fields_expected, missing_fields


def _extract_sources(evidence: EvidencePackage) -> list[str]:
    """Extract source list dari Evidence metadata."""
    sources = set()

    if evidence.price_market.metadata.status != "missing":
        sources.add(evidence.price_market.metadata.source)
    if evidence.fundamental.metadata.status != "missing":
        sources.add(evidence.fundamental.metadata.source)
    if evidence.institutional_ownership.metadata.status != "missing":
        sources.add(evidence.institutional_ownership.metadata.source)
    if evidence.institutional_activity.metadata.status != "missing":
        sources.add(evidence.institutional_activity.metadata.source)
    if evidence.news.metadata and evidence.news.metadata.status != "missing":
        sources.add(evidence.news.metadata.source)
    if evidence.sec_filings.metadata and evidence.sec_filings.metadata.status != "missing":
        sources.add(evidence.sec_filings.metadata.source)

    return sorted(list(sources))


def _generate_quality_notes(evidence: EvidencePackage, returns: dict, volatility: float | None) -> str | None:
    """#5: Generate data quality notes untuk flag issues."""
    notes = []

    # Price history
    price_hist_count = len(evidence.price_market.price_history) if evidence.price_market.price_history else 0
    if price_hist_count < 100:
        notes.append(f"Limited price history ({price_hist_count} bars)")

    # Fundamental gaps
    if not evidence.fundamental.revenue:
        notes.append("Revenue missing")
    if evidence.fundamental.net_income is None:
        notes.append("Net income missing")
    if not evidence.fundamental.free_cash_flow:
        notes.append("FCF missing")

    # Ownership gaps
    if not evidence.institutional_ownership.percentage:
        notes.append("Institutional ownership missing")

    # News gap
    news_count = len(evidence.news.news) if evidence.news and evidence.news.news else 0
    if news_count == 0:
        notes.append("No news data")

    return " | ".join(notes) if notes else None


def _compute_earnings_beat_streak(eps_surprise_history: list) -> str | None:
    """#4 Tren Historis: hitung berapa dari kuartal EPS terakhir yang "beat"
    estimate analis (surprise_pct > 0) -- format "N/M beat" sesuai contoh di
    HistoricalTrend.earnings_beat_miss_streak (knowledge_contracts.py).
    eps_surprise_history dari Evidence hanya 4 kuartal terakhir (yfinance
    earnings_history), bukan 8 -- lihat AnalystEstimatesBlock di frontend.
    Kuartal tanpa surprise_pct (None) tidak dihitung sebagai penyebut.
    """
    valid = [e for e in eps_surprise_history if e.surprise_pct is not None]
    if not valid:
        return None

    beats = sum(1 for e in valid if e.surprise_pct > 0)
    return f"{beats}/{len(valid)} beat"


def _compute_acceleration_signal(trends: dict) -> str | None:
    """#3b Momentum: bandingkan revenue YoY growth kuartal terakhir (q4) vs
    kuartal sebelumnya (q3) — sinyal deskriptif murni angka, tanpa kata sifat
    evaluatif (spec 03_KNOWLEDGE.md §3b, contoh: "QoQ growth accelerating").

    trends di sini adalah dict hasil compute_financial_trends() yang sudah
    dihitung di build_knowledge_for_ticker (bukan panggilan baru ke Evidence —
    invariant Knowledge murni per-ticker tetap utuh).
    """
    yoy_q3 = trends.get('revenue_yoy_q3')
    yoy_q4 = trends.get('revenue_yoy_q4')
    if yoy_q3 is None or yoy_q4 is None:
        return None

    delta = yoy_q4 - yoy_q3
    if delta > 0:
        return f"Revenue YoY growth accelerating: {yoy_q3:.1f}% (Q-1) -> {yoy_q4:.1f}% (Q terkini)"
    elif delta < 0:
        return f"Revenue YoY growth decelerating: {yoy_q3:.1f}% (Q-1) -> {yoy_q4:.1f}% (Q terkini)"
    else:
        return f"Revenue YoY growth flat: {yoy_q4:.1f}% (Q terkini, tidak berubah dari Q-1)"


def _build_governance(evidence: EvidencePackage) -> Governance:
    """#7 Governance & Filing Events (spec 03_KNOWLEDGE.md §7).

    Derivable dari Evidence saat ini:
      - unusual_filings: filing amandemen (form_type diakhiri "/A" — 10-K/A,
        10-Q/A, 8-K/A) dari sec_edgar.py fetch_sec_filings(). Amandemen filing
        itu sendiri adalah fakta ("filing tidak biasa"), bukan interpretasi —
        cocok dengan contoh spec ("filing tidak biasa lainnya").
      - filing_data_available: BENAR/SALAH-nya fetch SEC EDGAR itu sendiri
        (evidence.sec_filings.metadata.status=="ok"), dipakai Confidence
        sebagai sinyal kelengkapan bagian ini (bukan len(unusual_filings)>0
        — lihat docstring field-nya di knowledge_contracts.py buat alasan).

    shares_outstanding_change_12m sekarang derivable (2026-07-24) — dari
    SEC XBRL CommonStockSharesOutstanding (instant fact per tanggal neraca),
    bukan lagi Yahoo fast_info snapshot-saat-ini yang tidak punya baseline
    12-bulan. Lihat sources/sec_parser.py fetch_shares_outstanding_change_12m.
    Masih bisa None kalau ticker tidak punya cukup snapshot historis (mis.
    baru IPO) — itu genuinely "tidak ada data", bukan bug.

    TIDAK derivable dari Evidence saat ini (sengaja dibiarkan kosong,
    bukan bug — butuh data yang belum dikumpulkan di Evidence stage, DAN
    sejak audit 2026-07-29 sengaja TIDAK ikut dihitung sebagai "data hilang"
    oleh Confidence lagi, persis karena inilah alasannya):
      - auditor_changes / restatements / material_litigation: butuh parsing isi
        filing (mis. item number 8-K 4.01 untuk auditor change, 4.02 untuk
        restatement, atau teks litigation disclosure). sec_edgar.py cuma
        menyimpan form_type + tanggal + URL filing, bukan isi/item filing.
    """
    unusual_filings: list[GovernanceEvent] = []
    for f in evidence.sec_filings.filings or []:
        if f.form_type and f.form_type.endswith("/A"):
            unusual_filings.append(GovernanceEvent(
                event_type="filing_amendment",
                date=f.filing_date,
                description=f"{f.form_type} filed (amended filing)"
            ))
    filing_data_available = bool(
        evidence.sec_filings.metadata and evidence.sec_filings.metadata.status == "ok"
    )

    return Governance(
        shares_outstanding_change_12m=evidence.fundamental.shares_outstanding_change_12m,
        auditor_changes=[],
        restatements=[],
        material_litigation=[],
        unusual_filings=unusual_filings,
        filing_data_available=filing_data_available
    )


def run_knowledge(evidence_packages: list[EvidencePackage], screening_candidates: list[ScreeningCandidate] | None = None) -> list[KnowledgeProfile]:
    """Jalankan Knowledge generation untuk semua Evidence packages."""
    candidates_map = {}
    if screening_candidates:
        candidates_map = {c.ticker: c for c in screening_candidates}

    profiles = []
    total = len(evidence_packages)

    for i, evidence in enumerate(evidence_packages, 1):
        if i % 10 == 0 or i == 1:
            print(f"Knowledge {i}/{total}: {evidence.ticker}", file=sys.stderr)

        try:
            candidate = candidates_map.get(evidence.ticker)
            profile = build_knowledge_for_ticker(evidence, candidate)
            profiles.append(profile)
        except Exception as e:
            print(f"Error building knowledge for {evidence.ticker}: {e}", file=sys.stderr)

    print(f"Knowledge complete: {len(profiles)} profiles", file=sys.stderr)
    return profiles
