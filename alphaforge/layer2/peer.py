"""Layer 2 Fase B: Peer Comparison — bandingkan ticker terhadap peer group.

Input: Knowledge profiles (entire population dari Fase A)
Output: PeerComparisonResult per ticker

Lihat 06_PEER_RELATIVE_COMPARISON.md.
"""
from __future__ import annotations

import sys
import statistics
from datetime import datetime, timezone
from .contracts import ScreeningCandidate
from .knowledge_contracts import KnowledgeProfile
from .peer_contracts import (
    PeerComparisonResult, PeerGroupInfo, PeerMetricComparison
)


def group_by_sector(profiles: list[KnowledgeProfile]) -> dict[str, list[KnowledgeProfile]]:
    """Group Knowledge profiles by sector.

    Returns: {
        'Technology': [profile1, profile2, ...],
        'Healthcare': [profile3, profile4, ...],
        ...
    }

    Sectors tanpa nama dikelompokkan jadi 'Unknown'.
    """
    groups = {}
    for profile in profiles:
        sector = profile.sector or "Unknown"
        if sector not in groups:
            groups[sector] = []
        groups[sector].append(profile)
    return groups


def calculate_percentile(ticker_value: float | None, peer_values: list[float]) -> float | None:
    """Calculate percentile position dari ticker dalam peer group.

    Percentile: 0-100, dimana 50 = median, >50 = better than median.

    Formula: (count of values <= ticker_value) / total * 100
    """
    if ticker_value is None or not peer_values:
        return None

    sorted_values = sorted(peer_values)
    count_lte = sum(1 for v in sorted_values if v <= ticker_value)
    percentile = (count_lte / len(sorted_values)) * 100
    return percentile


# Metric interpretation: mana yang "tinggi baik" vs "rendah baik"
METRIC_DIRECTION = {
    "pe_ratio": "lower_is_better",
    "ps_ratio": "lower_is_better",
    "pb_ratio": "lower_is_better",
    "debt_to_equity": "lower_is_better",
    "fcf_yield": "higher_is_better",
    "gross_margin": "higher_is_better",
    "operating_margin": "higher_is_better",
    "net_margin": "higher_is_better",
    "revenue_growth": "higher_is_better",
    "roe": "higher_is_better",
    "roa": "higher_is_better",
}


def calculate_metric_comparison(
    metric_name: str,
    ticker_value: float | None,
    peer_values: list[float],
    peer_tickers: list[str],
    peer_failures: list[str],
    min_group_size: int = 3
) -> PeerMetricComparison | None:
    """Calculate comparison untuk satu metrik.

    Returns: PeerMetricComparison atau None jika group terlalu kecil.
    """
    # Ambang minimum dihitung dari jumlah peer yang PUNYA nilai valid untuk
    # metrik ini (len(peer_values)), bukan dari total peer group (peer_tickers).
    # peer_tickers ukurannya sama untuk semua metrik seorang ticker, sementara
    # tiap metrik bisa punya null berbeda-beda di antar peer -- pakai
    # len(peer_tickers) di sini membuat median/percentile bisa dihitung dari
    # cuma 1-2 nilai valid sambil tetap dilabeli "ok"/"low_sample_size" seolah
    # didukung oleh seluruh peer group (lihat bug: pe_ratio_comparison AAL di
    # peer_results.json -- peer_group_count=4 tapi median==min==max, cuma 1
    # nilai valid yang masuk kalkulasi).
    group_size = len(peer_values)

    # Check minimum group size
    if group_size < min_group_size:
        return None  # Don't calculate untuk grup terlalu kecil

    status = "ok"
    if group_size < 5:
        status = "low_sample_size"

    # Calculate median + min/max
    median = statistics.median(peer_values) if peer_values else None
    min_val = min(peer_values) if peer_values else None
    max_val = max(peer_values) if peer_values else None

    # Calculate percentile
    percentile = calculate_percentile(ticker_value, peer_values)

    # Get metric direction (default: higher_is_better if unknown)
    direction = METRIC_DIRECTION.get(metric_name, "higher_is_better")

    return PeerMetricComparison(
        metric_name=metric_name,
        ticker_value=ticker_value,
        peer_group_median=median,
        peer_group_min=min_val,
        peer_group_max=max_val,
        peer_group_count=group_size,
        percentile=percentile,
        status=status,
        direction=direction
    )


def build_peer_comparison(
    target_profile: KnowledgeProfile,
    peer_profiles: list[KnowledgeProfile],
    basis: str = "screening_universe",
    peer_failures: list[str] | None = None,
) -> PeerComparisonResult:
    """Bangun Peer Comparison untuk satu ticker terhadap peer group-nya.

    target_profile: ticker yang akan dianalisis
    peer_profiles: seluruh peers dalam grup (including target)
    peer_failures: ticker yang lolos Screening di scope yang sama (sektor/
        universe) tapi tidak pernah sampai ke Knowledge — gagal di Evidence
        atau Knowledge stage. Dihitung run_peer_comparison (butuh daftar
        kandidat Screening asli), bukan di sini — fungsi ini cuma
        menyematkannya ke output.
    """
    ticker = target_profile.ticker
    exchange = target_profile.exchange

    # Exclude target dari peer group
    peers = [p for p in peer_profiles if p.ticker != ticker]
    peer_tickers = [p.ticker for p in peers]
    peer_failures = list(peer_failures) if peer_failures else []

    # Build peer group info
    peer_group = PeerGroupInfo(
        ticker=ticker,
        sector=target_profile.sector,
        industry=None,  # TODO: industry classification
        peer_tickers=peer_tickers,
        group_size=len(peers),
        peer_failures=peer_failures
    )

    # Extract metric values dari peers.
    # Filter pakai "is not None", BUKAN truthy check (`if p.x...`) -- metrik
    # seperti debt_to_equity=0.0 (perusahaan bebas utang), institutional_pct=0.0
    # (belum ada kepemilikan institusional), atau margin=0.0% (breakeven) adalah
    # nilai VALID, bukan data hilang. Truthy check dulu membuang nilai 0.0 yang
    # sah sebagai kalau null, mengecilkan peer sample diam-diam.
    pe_values = [p.valuation.pe_ratio_trailing for p in peers if p.valuation.pe_ratio_trailing is not None]
    ps_values = [p.valuation.ps_ratio for p in peers if p.valuation.ps_ratio is not None]
    pb_values = [p.valuation.pb_ratio for p in peers if p.valuation.pb_ratio is not None and p.valuation.pb_ratio > 0]
    fcf_yield_values = [p.valuation.fcf_yield for p in peers if p.valuation.fcf_yield is not None]

    gm_values = [p.financial_health.gross_margin_trend.q4 for p in peers
                  if p.financial_health.gross_margin_trend.q4 is not None]
    om_values = [p.financial_health.operating_margin_trend.q4 for p in peers
                  if p.financial_health.operating_margin_trend.q4 is not None]
    nm_values = [p.financial_health.net_margin_trend.q4 for p in peers
                  if p.financial_health.net_margin_trend.q4 is not None]

    roe_values = [p.financial_health.roe for p in peers if p.financial_health.roe is not None]
    roa_values = [p.financial_health.roa for p in peers if p.financial_health.roa is not None]
    dte_values = [p.financial_health.balance_sheet.debt_to_equity for p in peers
                   if p.financial_health.balance_sheet.debt_to_equity is not None]

    # Calculate comparisons
    pe_comp = calculate_metric_comparison("pe_ratio", target_profile.valuation.pe_ratio_trailing, pe_values, peer_tickers, peer_failures)
    ps_comp = calculate_metric_comparison("ps_ratio", target_profile.valuation.ps_ratio, ps_values, peer_tickers, peer_failures)
    pb_comp = calculate_metric_comparison("pb_ratio", target_profile.valuation.pb_ratio, pb_values, peer_tickers, peer_failures) if target_profile.valuation.pb_ratio and target_profile.valuation.pb_ratio > 0 else None
    fcf_comp = calculate_metric_comparison("fcf_yield", target_profile.valuation.fcf_yield, fcf_yield_values, peer_tickers, peer_failures)

    gm_comp = calculate_metric_comparison("gross_margin", target_profile.financial_health.gross_margin_trend.q4, gm_values, peer_tickers, peer_failures)
    om_comp = calculate_metric_comparison("operating_margin", target_profile.financial_health.operating_margin_trend.q4, om_values, peer_tickers, peer_failures)
    nm_comp = calculate_metric_comparison("net_margin", target_profile.financial_health.net_margin_trend.q4, nm_values, peer_tickers, peer_failures)

    roe_comp = calculate_metric_comparison("roe", target_profile.financial_health.roe, roe_values, peer_tickers, peer_failures)
    roa_comp = calculate_metric_comparison("roa", target_profile.financial_health.roa, roa_values, peer_tickers, peer_failures)
    dte_comp = calculate_metric_comparison("debt_to_equity", target_profile.financial_health.balance_sheet.debt_to_equity, dte_values, peer_tickers, peer_failures)

    return PeerComparisonResult(
        ticker=ticker,
        exchange=exchange,
        peer_group=peer_group,
        pe_ratio_comparison=pe_comp,
        ps_ratio_comparison=ps_comp,
        pb_ratio_comparison=pb_comp,
        fcf_yield_comparison=fcf_comp,
        gross_margin_comparison=gm_comp,
        operating_margin_comparison=om_comp,
        net_margin_comparison=nm_comp,
        roe_comparison=roe_comp,
        roa_comparison=roa_comp,
        debt_to_equity_comparison=dte_comp,
        generated_at=datetime.now(timezone.utc).isoformat(),
        peer_group_basis=basis,
        low_sample_size=len(peers) < 3
    )


def run_peer_comparison(
    profiles: list[KnowledgeProfile],
    screening_candidates: list[ScreeningCandidate] | None = None,
) -> list[PeerComparisonResult]:
    """Jalankan Peer Comparison untuk seluruh Knowledge population.

    Fase B: butuh complete Knowledge dari seluruh screening candidates.

    screening_candidates: daftar kandidat ASLI dari Screening (screening_result.passed),
        optional — kalau diisi, dipakai untuk hitung peer_failures nyata (ticker
        yang lolos Screening tapi gagal di Evidence/Knowledge, sehingga tidak
        pernah sampai ke `profiles`). Tanpa ini, peer_failures selalu [] (tidak
        ada visibilitas ke kandidat yang drop out sebelum Peer) — sama seperti
        behavior lama, bukan regresi.
    """
    # Group by sector
    sectors = group_by_sector(profiles)
    knowledge_tickers = {p.ticker for p in profiles}

    # Kandidat Screening per sektor (kalau disediakan) — dipakai buat deteksi
    # peer_failures: kandidat yang ADA di sini tapi TIDAK ADA di knowledge_tickers
    # berarti gagal di Evidence atau Knowledge stage, bukan cuma "tidak
    # dibandingkan" (beda dari low_sample_size, yang soal peer group terlalu
    # kecil meski semuanya berhasil).
    screening_by_sector: dict[str, list[str]] = {}
    all_screening_tickers: list[str] = []
    if screening_candidates:
        for c in screening_candidates:
            sector = c.sector or "Unknown"
            screening_by_sector.setdefault(sector, []).append(c.ticker)
            all_screening_tickers.append(c.ticker)

    results = []
    total = len(profiles)

    for i, profile in enumerate(profiles, 1):
        if i % 50 == 0 or i == 1:
            print(f"Peer {i}/{total}: {profile.ticker}", file=sys.stderr)

        try:
            sector = profile.sector or "Unknown"
            sector_group = sectors.get(sector, [])

            if len(sector_group) >= 2:
                # peer_group_basis kontraknya cuma "screening_universe" | "manual"
                # (lihat peer_contracts.py) -- narrowing per-sektor tetap masih
                # bersumber dari populasi screening yang sama, jadi basis-nya
                # tetap "screening_universe", bukan label "sector" yang bukan
                # bagian dari kontrak.
                peer_group, basis = sector_group, "screening_universe"
                scope_tickers = screening_by_sector.get(sector, [])
            else:
                # Sektor tidak diketahui atau kepesertaannya terlalu kecil di
                # universe ini untuk perbandingan bermakna (mis. cuma 1
                # ticker di sektor itu) — dulu ticker ini di-skip total dari
                # Peer. Sekarang fallback ke seluruh screening universe
                # supaya tetap dapat hasil, dengan basis yang jujur di label.
                print(f"  Info: {profile.ticker} sector '{sector}' punya <2 peers, fallback ke screening_universe", file=sys.stderr)
                peer_group, basis = profiles, "screening_universe"
                scope_tickers = all_screening_tickers

            peer_failures = sorted(set(scope_tickers) - knowledge_tickers) if screening_candidates else []

            comparison = build_peer_comparison(profile, peer_group, basis=basis, peer_failures=peer_failures)
            results.append(comparison)
        except Exception as e:
            print(f"Error for {profile.ticker}: {e}", file=sys.stderr)

    print(f"Peer Comparison complete: {len(results)} results", file=sys.stderr)
    return results
