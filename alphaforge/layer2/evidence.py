"""Layer 2 tahap 2: Evidence — pengumpulan fakta terverifikasi per ticker.

Input: list ScreeningCandidate dari Screening
Output: list EvidencePackage, satu per ticker

Alur: ambil price/fundamental dari Yahoo, berita dari Finnhub, filing dari EDGAR.
Tiap field ditandai dengan source + timestamp + status.

Lihat 03_LAYER2_SPECS/02_EVIDENCE.md.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from .contracts import (
    ScreeningCandidate, ScreeningResult, EvidencePackage,
    NewsCollection, SecFilings, SourceMetadata
)
from .sources.yahoo_evidence import (
    fetch_price_market_data, fetch_fundamental_data, fetch_institutional_ownership,
    fetch_company_profile, fetch_analyst_estimates,
    reset_batch_tracking as reset_yahoo_batch_tracking
)
from .sources.finnhub import fetch_company_news, reset_batch_tracking as reset_finnhub_batch_tracking
from .sources.sec_edgar import fetch_sec_filings
from .sources.sec_parser import (
    fetch_quarterly_financials, fetch_shares_outstanding_change_12m, reset_sec_rate_limit
)
from .sources.sec_form4 import fetch_institutional_activity


CROSS_REFERENCE_REVENUE_THRESHOLD_PCT = 15.0


def _fmt_money(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:.2f}"


def _cross_reference_fundamental(fundamental) -> list[str]:
    """Bandingkan revenue TTM (Yahoo `.info`, top-level `revenue`) vs jumlah
    4 kuartal terakhir dari SEC EDGAR (`quarterly_data`) — dua sumber
    independen untuk metrik yang sama, satu-satunya pasangan begitu di
    Evidence saat ini. Selisih besar = sinyal nyata (unit salah, cache
    stale, fiscal year misalign), bukan sekadar noise TTM-window."""
    notes: list[str] = []
    if fundamental.revenue is None or len(fundamental.quarterly_data) < 4:
        return notes
    sorted_quarters = sorted(
        fundamental.quarterly_data,
        key=lambda q: q.fiscal_date or q.period,
        reverse=True,
    )
    last_4 = sorted_quarters[:4]
    if any(q.revenue is None for q in last_4):
        return notes
    quarterly_sum = sum(q.revenue for q in last_4)
    if quarterly_sum == 0:
        return notes
    diff_pct = abs(fundamental.revenue - quarterly_sum) / quarterly_sum * 100
    if diff_pct > CROSS_REFERENCE_REVENUE_THRESHOLD_PCT:
        notes.append(
            f"Revenue TTM (Yahoo) {_fmt_money(fundamental.revenue)} vs jumlah 4Q SEC "
            f"{_fmt_money(quarterly_sum)} — selisih {diff_pct:.1f}%"
        )
    return notes


def build_evidence_for_ticker(candidate: ScreeningCandidate) -> EvidencePackage | None:
    """Bangun Evidence package untuk satu ticker yang lolos Screening."""
    ticker = candidate.ticker
    exchange = candidate.exchange

    # Parallel fetch (dalam praktik bisa di-batch/async, tapi untuk MVP sequential fine)
    price_market = fetch_price_market_data(ticker)
    fundamental = fetch_fundamental_data(ticker)
    institutional = fetch_institutional_ownership(ticker)
    institutional_activity = fetch_institutional_activity(ticker)
    news = fetch_company_news(ticker)
    sec_filings = fetch_sec_filings(ticker)
    company_profile = fetch_company_profile(ticker)
    analyst_estimates = fetch_analyst_estimates(ticker)

    # #2 Financial Trends: Fetch quarterly data dari SEC EDGAR
    quarterly_data = fetch_quarterly_financials(ticker)
    if quarterly_data and quarterly_data.get("periods"):
        from .contracts import QuarterlyFundamental
        fundamental.quarterly_data = [
            QuarterlyFundamental(**q) for q in quarterly_data["periods"]
        ]

    fundamental.cross_reference_notes = _cross_reference_fundamental(fundamental)

    # Baseline dilution 12-bulan (lihat sources/sec_parser.py) — dipanggil
    # SETELAH fetch_quarterly_financials di atas supaya company facts XBRL
    # (cache 24h) sudah ter-cache, bukan network call kedua.
    fundamental.shares_outstanding_change_12m = fetch_shares_outstanding_change_12m(ticker)

    # analyst_estimates.price_target_history is populated later, in bulk,
    # by price_target.sync_price_target_history() after run_evidence()
    # returns — that's where the on-disk historical store lives (see that
    # module's docstring for why: Yahoo has no free historical time series
    # for this, so it has to be accumulated across daily pipeline runs).

    package = EvidencePackage(
        ticker=ticker,
        exchange=exchange,
        price_market=price_market,
        fundamental=fundamental,
        institutional_ownership=institutional,
        institutional_activity=institutional_activity,
        news=news,
        sec_filings=sec_filings,
        generated_at=datetime.now(timezone.utc).isoformat(),
        company_profile=company_profile,
        analyst_estimates=analyst_estimates,
    )

    return package


def run_evidence(screening_result: ScreeningResult) -> list[EvidencePackage]:
    """Jalankan Evidence collection untuk semua kandidat dari Screening."""
    packages = []
    reset_finnhub_batch_tracking()  # Reset Finnhub rate limit tracking
    reset_yahoo_batch_tracking()  # Reset Yahoo Finance rate limit tracking
    reset_sec_rate_limit()  # Reset SEC EDGAR/XBRL rate limit tracking

    passed_count = len(screening_result.passed)
    for i, candidate in enumerate(screening_result.passed, 1):
        if i % 10 == 0 or i == 1:
            print(f"Evidence {i}/{passed_count}: {candidate.ticker}", file=sys.stderr)

        try:
            pkg = build_evidence_for_ticker(candidate)
            if pkg:
                packages.append(pkg)
        except Exception as e:
            print(f"Error building evidence for {candidate.ticker}: {e}", file=sys.stderr)

    print(f"Evidence complete: {len(packages)} packages", file=sys.stderr)
    return packages
