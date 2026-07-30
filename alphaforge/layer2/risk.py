"""Risk & Red-Flag detection module — Layer 2 Fase B, stage 3

Mendeteksi governance anomalies, financial extremes, momentum reversals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .risk_contracts import RiskAssessment, RedFlag, Flag

if TYPE_CHECKING:
    from .knowledge_contracts import KnowledgeProfile


def assess_risk(knowledge_profile: KnowledgeProfile) -> RiskAssessment:
    """Assess risk level untuk satu Knowledge profile.

    Args:
        knowledge_profile: KnowledgeProfile dari Fase A

    Returns:
        RiskAssessment dengan flags dan risk scores
    """
    ticker = knowledge_profile.ticker
    exchange = knowledge_profile.exchange

    red_flags = []

    # Deteksi governance anomalies
    gov_flags, gov_score = _detect_governance_issues(knowledge_profile, red_flags)
    red_flags.extend(gov_flags)

    # Deteksi financial extremes
    fin_flags, fin_score = _detect_financial_extremes(knowledge_profile, red_flags)
    red_flags.extend(fin_flags)

    # Deteksi momentum reversals
    mom_flags, mom_score = _detect_momentum_issues(knowledge_profile, red_flags)
    red_flags.extend(mom_flags)

    # Deteksi valuation extremes
    val_flags, val_score = _detect_valuation_extremes(knowledge_profile, red_flags)
    red_flags.extend(val_flags)

    # 04_RISK_REDFLAG_CHECK.md v2.0.0 — 6 pemeriksaan spec, terpisah dari
    # red_flags di atas (lihat docstring Flag di risk_contracts.py)
    flags = _check_spec_flags(knowledge_profile)
    triggered_ekstrem = [f for f in flags if f.severity == "ekstrem" and f.status == "triggered"]
    halted = len(triggered_ekstrem) > 0
    halt_reason = triggered_ekstrem[0].evidence_note if halted else None

    # Calculate overall risk (weighted average)
    weights = {
        "governance": 0.25,
        "financial": 0.35,
        "momentum": 0.20,
        "valuation": 0.20,
    }

    overall_risk = (
        gov_score * weights["governance"]
        + fin_score * weights["financial"]
        + mom_score * weights["momentum"]
        + val_score * weights["valuation"]
    )

    # Rating
    if overall_risk >= 75:
        rating = "critical"
    elif overall_risk >= 50:
        rating = "high"
    elif overall_risk >= 25:
        rating = "medium"
    else:
        rating = "low"

    # Count flags by severity
    high_count = sum(1 for f in red_flags if f.severity == "high")
    medium_count = sum(1 for f in red_flags if f.severity == "medium")
    low_count = sum(1 for f in red_flags if f.severity == "low")

    # Risk adjustment for confidence (negative = reduce confidence)
    risk_adjustment = 0.0
    if high_count > 0:
        risk_adjustment = -0.2
    elif medium_count >= 2:
        risk_adjustment = -0.1

    # Risk notes
    notes_parts = []
    if high_count > 0:
        notes_parts.append(f"{high_count} high-risk flag(s)")
    if medium_count > 0:
        notes_parts.append(f"{medium_count} medium-risk flag(s)")
    if len(red_flags) == 0:
        notes_parts.append("No significant red flags detected")

    risk_notes = " | ".join(notes_parts)

    return RiskAssessment(
        ticker=ticker,
        exchange=exchange,
        risk_score=round(overall_risk, 1),
        risk_rating=rating,
        governance_risk_score=round(gov_score, 1),
        financial_risk_score=round(fin_score, 1),
        momentum_risk_score=round(mom_score, 1),
        valuation_risk_score=round(val_score, 1),
        red_flags=red_flags,
        high_severity_count=high_count,
        medium_severity_count=medium_count,
        low_severity_count=low_count,
        risk_notes=risk_notes,
        recommended_risk_adjustment=risk_adjustment,
        assessed_at=datetime.now(timezone.utc).isoformat(),
        flags=flags,
        halted=halted,
        halt_reason=halt_reason,
    )


def _detect_governance_issues(profile: KnowledgeProfile, existing_flags: list[RedFlag]) -> tuple[list[RedFlag], float]:
    """Deteksi governance anomalies."""
    gov = profile.governance
    flags = []
    risk_score = 0.0

    # Auditor changes
    if gov.auditor_changes and len(gov.auditor_changes) > 0:
        flags.append(RedFlag(
            flag_type="auditor_change",
            severity="high" if len(gov.auditor_changes) > 1 else "medium",
            description=f"Auditor change(s) detected ({len(gov.auditor_changes)})",
            affected_metrics=["governance", "audit_quality"]
        ))
        risk_score += 25

    # Restatements
    if gov.restatements and len(gov.restatements) > 0:
        flags.append(RedFlag(
            flag_type="restatement",
            severity="high",
            description=f"Financial restatement(s) ({len(gov.restatements)})",
            affected_metrics=["financial_reporting", "earnings_quality"]
        ))
        risk_score += 30

    # Material litigation
    if gov.material_litigation and len(gov.material_litigation) > 0:
        flags.append(RedFlag(
            flag_type="litigation",
            severity="high" if len(gov.material_litigation) > 2 else "medium",
            description=f"Material litigation ({len(gov.material_litigation)} cases)",
            affected_metrics=["legal_risk", "cash_flow"]
        ))
        risk_score += 20

    # Unusual filings
    if gov.unusual_filings and len(gov.unusual_filings) > 0:
        flags.append(RedFlag(
            flag_type="unusual_filing",
            severity="medium",
            description=f"Unusual SEC filings ({len(gov.unusual_filings)})",
            affected_metrics=["disclosure_quality"]
        ))
        risk_score += 15

    return flags, min(risk_score, 100.0)


def _detect_financial_extremes(profile: KnowledgeProfile, existing_flags: list[RedFlag]) -> tuple[list[RedFlag], float]:
    """Deteksi financial red flags (high debt, declining margins)."""
    fh = profile.financial_health
    flags = []
    risk_score = 0.0

    # High debt
    if fh.balance_sheet.debt_to_equity is not None and fh.balance_sheet.debt_to_equity > 2.0:
        flags.append(RedFlag(
            flag_type="high_debt",
            severity="high" if fh.balance_sheet.debt_to_equity > 3.0 else "medium",
            description=f"High debt-to-equity ratio ({fh.balance_sheet.debt_to_equity:.2f})",
            affected_metrics=["leverage", "financial_stability"]
        ))
        risk_score += 25

    # Poor current ratio
    if fh.balance_sheet.current_ratio is not None and fh.balance_sheet.current_ratio < 1.0:
        flags.append(RedFlag(
            flag_type="low_liquidity",
            severity="high" if fh.balance_sheet.current_ratio < 0.5 else "medium",
            description=f"Low current ratio ({fh.balance_sheet.current_ratio:.2f}) - liquidity concern",
            affected_metrics=["liquidity", "working_capital"]
        ))
        risk_score += 20

    # Declining margins.
    # net_margin_trend.q4 berskala PERSEN-POIN (-12.3 berarti -12.3%), lihat
    # knowledge_helpers.compute_financial_trends yang menghitung (ni/rev)*100 —
    # konvensi yang sama dipakai reasoning.py (ambang `> 5` / `< 0`).
    # Dulu di sini ambangnya `-0.05` (efektif "margin negatif apa pun") dan
    # formatnya `:.2%` yang MENGALIKAN 100 sekali lagi, jadi margin riil -12.3%
    # tercetak sebagai "-1230.00%" di deskripsi red flag.
    if fh.net_margin_trend.q4 is not None and fh.net_margin_trend.q4 < 0:
        flags.append(RedFlag(
            flag_type="declining_margin",
            severity="medium",
            description=f"Negative net margin Q4 ({fh.net_margin_trend.q4:.2f}%)",
            affected_metrics=["profitability", "operational_efficiency"]
        ))
        risk_score += 15

    # Negative FCF
    if fh.cash_flow_trend.fcf_q4 is not None and fh.cash_flow_trend.fcf_q4 < 0:
        flags.append(RedFlag(
            flag_type="negative_fcf",
            severity="high",
            description="Negative free cash flow - burn rate concern",
            affected_metrics=["cash_generation", "sustainability"]
        ))
        risk_score += 25

    return flags, min(risk_score, 100.0)


def _detect_momentum_issues(profile: KnowledgeProfile, existing_flags: list[RedFlag]) -> tuple[list[RedFlag], float]:
    """Deteksi momentum reversals (earnings streak breaks, guidance misses)."""
    ht = profile.historical_trend
    cm = profile.competitive_momentum
    flags = []
    risk_score = 0.0

    # Earnings streak breaks
    if ht.earnings_beat_miss_streak and "miss" in str(ht.earnings_beat_miss_streak).lower():
        flags.append(RedFlag(
            flag_type="earnings_streak_break",
            severity="medium",
            description="Recent earnings miss(es) detected",
            affected_metrics=["earnings_quality", "guidance_accuracy"]
        ))
        risk_score += 15

    # Guidance misses
    if cm.guidance_trend and "down" in str(cm.guidance_trend).lower():
        flags.append(RedFlag(
            flag_type="guidance_miss",
            severity="medium",
            description="Downward guidance trend",
            affected_metrics=["momentum", "management_credibility"]
        ))
        risk_score += 15

    return flags, min(risk_score, 100.0)


def _detect_valuation_extremes(profile: KnowledgeProfile, existing_flags: list[RedFlag]) -> tuple[list[RedFlag], float]:
    """Deteksi valuation extremes (very high multiples, distressed valuations)."""
    val = profile.valuation
    ht = profile.historical_trend
    flags = []
    risk_score = 0.0

    # Very high P/E
    if val.pe_ratio_trailing is not None:
        if val.pe_ratio_trailing > 100:
            flags.append(RedFlag(
                flag_type="valuation_extreme",
                severity="medium",
                description=f"Very high P/E ratio ({val.pe_ratio_trailing:.1f}x)",
                affected_metrics=["valuation", "growth_assumptions"]
            ))
            risk_score += 10

    # Very high volatility
    if ht.volatility_daily is not None and ht.volatility_daily > 5.0:
        flags.append(RedFlag(
            flag_type="high_volatility",
            severity="medium",
            description=f"High daily volatility ({ht.volatility_daily:.2f}%) - illiquid or risky",
            affected_metrics=["market_risk", "trading_risk"]
        ))
        risk_score += 10

    # Extreme negative returns
    if ht.return_1y is not None and ht.return_1y < -50:
        flags.append(RedFlag(
            flag_type="severe_drawdown",
            severity="high",
            description=f"Severe drawdown ({ht.return_1y:.1f}%) - distressed situation",
            affected_metrics=["valuation", "distress_signal"]
        ))
        risk_score += 20

    return flags, min(risk_score, 100.0)


# Ambang kuantitatif di bawah ini kalibrasi awal — spec sendiri bilang
# "ambang kuantitatif spesifik ... belum final" (04_RISK_REDFLAG_CHECK.md).
DILUTION_THRESHOLD_PCT = 10.0  # % kenaikan shares outstanding dalam 12 bulan
INSIDER_SELLING_THRESHOLD_USD = 1_000_000.0  # total nilai jual insider dalam 90 hari
AUDITOR_CHANGE_WINDOW_YEARS = 3
AUDITOR_CHANGE_MIN_COUNT = 1  # > 1 kali dalam window = flag
RESTATEMENT_WINDOW_YEARS = 2


def _parse_event_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(date_str)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _events_within(events: list, years: float | None = None, days: float | None = None) -> list:
    """Filter GovernanceEvent/insider-transaction dict yang tanggalnya dalam window."""
    now = datetime.now(timezone.utc)
    delta = timedelta(days=days) if days else timedelta(days=365 * years)
    cutoff = now - delta
    result = []
    for e in events:
        raw_date = e.date if hasattr(e, "date") else e.get("date")
        parsed = _parse_event_date(raw_date)
        if parsed and parsed >= cutoff:
            result.append(e)
    return result


def _check_dilution(profile: KnowledgeProfile) -> Flag | None:
    change = profile.governance.shares_outstanding_change_12m
    if change is None:
        return Flag(
            flag_id="dilution_12m", category="dilution", severity="tinggi", status="undetermined",
            knowledge_refs=["governance.shares_outstanding_change_12m"],
            evidence_note="Shares outstanding 12-month baseline not available from current Evidence sources",
            source="Yahoo Finance fundamentals",
        )
    if change > DILUTION_THRESHOLD_PCT:
        return Flag(
            flag_id="dilution_12m", category="dilution", severity="tinggi", status="triggered",
            knowledge_refs=["governance.shares_outstanding_change_12m"],
            evidence_note=f"Shares outstanding increased {change:.1f}% in the last 12 months",
            source="Yahoo Finance fundamentals",
        )
    return None


def _check_auditor_change(profile: KnowledgeProfile) -> Flag | None:
    changes = profile.governance.auditor_changes
    if not changes:
        return Flag(
            flag_id="auditor_change_3y", category="governance", severity="tinggi", status="undetermined",
            knowledge_refs=["governance.auditor_changes"],
            evidence_note="Auditor change history not available — Evidence tracks filing form_type/date only, not item-level content",
            source="SEC EDGAR filings",
        )
    recent = _events_within(changes, years=AUDITOR_CHANGE_WINDOW_YEARS)
    if len(recent) > AUDITOR_CHANGE_MIN_COUNT:
        return Flag(
            flag_id="auditor_change_3y", category="governance", severity="tinggi", status="triggered",
            knowledge_refs=["governance.auditor_changes"],
            evidence_note=f"{len(recent)} auditor change(s) in the last {AUDITOR_CHANGE_WINDOW_YEARS} years",
            source="SEC EDGAR filings",
        )
    return None


def _check_restatement(profile: KnowledgeProfile) -> Flag | None:
    restatements = profile.governance.restatements
    if not restatements:
        return Flag(
            flag_id="restatement_2y", category="accounting", severity="tinggi", status="undetermined",
            knowledge_refs=["governance.restatements"],
            evidence_note="Restatement history not available — Evidence tracks filing form_type/date only, not item-level content",
            source="SEC EDGAR filings",
        )
    recent = _events_within(restatements, years=RESTATEMENT_WINDOW_YEARS)
    if recent:
        return Flag(
            flag_id="restatement_2y", category="accounting", severity="tinggi", status="triggered",
            knowledge_refs=["governance.restatements"],
            evidence_note=f"{len(recent)} restatement(s) in the last {RESTATEMENT_WINDOW_YEARS} years",
            source="SEC EDGAR filings",
        )
    return None


def _check_litigation(profile: KnowledgeProfile) -> Flag | None:
    litigation = profile.governance.material_litigation
    if not litigation:
        return Flag(
            flag_id="litigation_material", category="litigation", severity="tinggi", status="undetermined",
            knowledge_refs=["governance.material_litigation"],
            evidence_note="Material litigation status not available — Evidence tracks filing form_type/date only, not item-level content",
            source="SEC EDGAR filings",
        )
    return Flag(
        flag_id="litigation_material", category="litigation", severity="tinggi", status="triggered",
        knowledge_refs=["governance.material_litigation"],
        evidence_note=f"{len(litigation)} material litigation record(s) on file",
        source="SEC EDGAR filings",
    )


def _check_insider_selling(profile: KnowledgeProfile) -> Flag | None:
    transactions = profile.ownership.insider_transactions
    if not transactions:
        return Flag(
            flag_id="insider_selling_90d", category="insider", severity="tinggi", status="undetermined",
            knowledge_refs=["ownership.insider_transactions"],
            evidence_note="Insider transaction data not available — SEC EDGAR fetcher excludes Form 3/4/144",
            source="SEC EDGAR filings",
        )
    recent_sells = [
        t for t in _events_within(transactions, days=90)
        if (t.get("type") if isinstance(t, dict) else None) == "sell"
    ]
    total_usd = sum((t.get("amount_usd") or 0) for t in recent_sells)
    if total_usd > INSIDER_SELLING_THRESHOLD_USD:
        return Flag(
            flag_id="insider_selling_90d", category="insider", severity="tinggi", status="triggered",
            knowledge_refs=["ownership.insider_transactions"],
            evidence_note=f"${total_usd:,.0f} in insider selling over the last 90 days ({len(recent_sells)} transaction(s))",
            source="SEC EDGAR filings",
        )
    return None


def _check_fraud_or_delisting(profile: KnowledgeProfile) -> Flag | None:
    """Severity ekstrem — hard-gate. Knowledge saat ini tidak punya field
    konfirmasi fraud atau status delisting/bankruptcy (SEC EDGAR fetcher
    cuma menyimpan form_type + tanggal, bukan item number 8-K seperti 1.03
    "Bankruptcy or Receivership", dan tidak menarik Form 15 deregistrasi) —
    jadi status ini selalu undetermined sampai Evidence diperluas. Mekanisme
    hard-gate (assess_risk di atas) tetap benar & siap dipakai begitu field-
    nya ada."""
    return Flag(
        flag_id="confirmed_fraud_or_delisting", category="listing_status", severity="ekstrem", status="undetermined",
        knowledge_refs=["governance.unusual_filings", "identity.instrument_status"],
        evidence_note="Confirmed fraud / delisting / bankruptcy status not available — Evidence does not fetch 8-K item numbers or Form 15",
        source="SEC EDGAR filings",
    )


def _check_spec_flags(profile: KnowledgeProfile) -> list[Flag]:
    """6 pemeriksaan 04_RISK_REDFLAG_CHECK.md v2.0.0. Mengembalikan flag
    kalau triggered ATAU undetermined — hanya diam (None) kalau field-nya
    benar-benar ada isinya dan tidak melewati ambang (checked & clean)."""
    checks = [
        _check_dilution(profile),
        _check_auditor_change(profile),
        _check_restatement(profile),
        _check_litigation(profile),
        _check_insider_selling(profile),
        _check_fraud_or_delisting(profile),
    ]
    return [f for f in checks if f is not None]


def run_risk_assessment(profiles: list[KnowledgeProfile]) -> list[RiskAssessment]:
    """Run risk assessment untuk semua profiles.

    Args:
        profiles: List of KnowledgeProfile dari Fase A

    Returns:
        List of RiskAssessment
    """
    assessments = []
    for profile in profiles:
        assessment = assess_risk(profile)
        assessments.append(assessment)

    return assessments
