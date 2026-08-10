"""02_LAYER1_SPECS/02_SECTOR_ROTATION.md — kind=direct, komponen leaf."""
from __future__ import annotations

from ..contracts import ComponentReading
from ..sources import yahoo
from ._util import ev, missing, source, th

NAME = "sector_rotation"

SECTOR_ETFS = ["XLK", "XLE", "XLF", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
BENCHMARK = "SPY"

# Klasifikasi kasar cyclical vs defensive buat raw_score — XLE/XLB/XLC
# sengaja netral (energi & materials lebih dipengaruhi commodity cycle,
# communication services campuran growth+defensive).
CYCLICAL = {"XLK", "XLY", "XLF", "XLI"}
DEFENSIVE = {"XLP", "XLU", "XLV", "XLRE"}
RAW_SCORE_CYCLICAL_LEADS = 70.0
RAW_SCORE_DEFENSIVE_LEADS = 40.0
RAW_SCORE_NEUTRAL = 55.0


def compute() -> ComponentReading:
    try:
        # Semua fetch pakai period="1y" yang sama supaya berbagi satu entry
        # cache per ticker (dulu 6mo & 1y = dua key = dua unduhan per ETF;
        # money_flow juga kini pakai 1y → ikut reuse cache yang sama).
        bench_df = yahoo.history(BENCHMARK, period="1y")
        as_of = bench_df.index[-1].strftime("%Y-%m-%d")
        bench_1m = yahoo.pct_change(BENCHMARK, days=21, period="1y")
        bench_3m = yahoo.pct_change(BENCHMARK, days=63, period="1y")
        relative = {}
        for etf in SECTOR_ETFS:
            r1m = yahoo.pct_change(etf, days=21, period="1y")
            r3m = yahoo.pct_change(etf, days=63, period="1y")
            relative[etf] = {
                "return_1m_pct": r1m,
                "return_3m_pct": r3m,
                "relative_1m_pct": r1m - bench_1m,
                "relative_3m_pct": r3m - bench_3m,
            }
    except Exception as exc:
        return missing(NAME, "direct", f"Yahoo sector ETF gagal ditarik: {exc}")

    ranked = sorted(relative, key=lambda k: relative[k]["relative_1m_pct"], reverse=True)
    leader = ranked[0]
    laggard = ranked[-1]
    leaders = [{"etf": e, "relative_1m_pct": relative[e]["relative_1m_pct"]} for e in ranked[:3]]
    laggards = [{"etf": e, "relative_1m_pct": relative[e]["relative_1m_pct"]} for e in ranked[-3:][::-1]]

    # Implikasi screening: bila 3 pemimpin didominasi sektor defensif → rotasi
    # bertahan, screening sebaiknya lebih selektif; bila didominasi siklikal →
    # nafsu risiko sehat, screening bisa lebih agresif.
    top3 = {l["etf"] for l in leaders}
    n_def = len(top3 & DEFENSIVE)
    n_cyc = len(top3 & CYCLICAL)
    if n_def > n_cyc:
        screening_implication = ("Dominasi sektor defensif di pemimpin — rotasi bertahan; "
                                 "screening sebaiknya lebih selektif (utamakan kualitas & neraca kuat).")
    elif n_cyc > n_def:
        screening_implication = ("Dominasi sektor siklikal di pemimpin — nafsu risiko sehat; "
                                 "screening bisa lebih terbuka ke growth/beta lebih tinggi.")
    else:
        screening_implication = ("Kepemimpinan sektor campuran — tak ada bias rotasi jelas; "
                                 "pertahankan kriteria screening standar.")

    lead_txt = ", ".join(f"{l['etf']} ({l['relative_1m_pct']:+.1f}pp)" for l in leaders)
    lag_txt = ", ".join(f"{l['etf']} ({l['relative_1m_pct']:+.1f}pp)" for l in laggards)
    narrative = (
        f"Leader vs {BENCHMARK}: {lead_txt}. Laggard: {lag_txt} (relatif 1 bulan). {screening_implication}"
    )
    rule = "ranking = relative_1m_pct vs SPY; leaders = 3 teratas, laggards = 3 terbawah"

    if leader in CYCLICAL:
        raw_score = RAW_SCORE_CYCLICAL_LEADS
    elif leader in DEFENSIVE:
        raw_score = RAW_SCORE_DEFENSIVE_LEADS
    else:
        raw_score = RAW_SCORE_NEUTRAL

    return ComponentReading(
        name=NAME,
        value={"benchmark": BENCHMARK, "sectors": relative, "leader": leader, "laggard": laggard,
               "leaders": leaders, "laggards": laggards, "screening_implication": screening_implication},
        status="ok",
        kind="direct",
        sources=[source("Yahoo Finance")],
        narrative=narrative,
        narrative_version="1.1.0",
        evidence=[
            ev("leaders", ", ".join(l["etf"] for l in leaders), as_of, "Yahoo Finance sector ETF (top 3 relative 1m vs SPY)"),
            ev("leader_relative_1m_pct", relative[leader]["relative_1m_pct"], as_of, "Yahoo Finance"),
            ev("laggards", ", ".join(l["etf"] for l in laggards), as_of, "Yahoo Finance sector ETF (bottom 3 relative 1m vs SPY)"),
        ],
        rule=rule,
        thresholds=[
            th(f"cyclical leads → score {RAW_SCORE_CYCLICAL_LEADS:.0f} (XLK/XLY/XLF/XLI)", "in", 0),
            th(f"defensive leads → score {RAW_SCORE_DEFENSIVE_LEADS:.0f} (XLP/XLU/XLV/XLRE)", "in", 0),
        ],
        raw_score=raw_score,
    )
