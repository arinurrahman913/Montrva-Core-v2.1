"""02_LAYER1_SPECS/05_YIELD_CURVE.md — kind=direct, komponen leaf.

Yield curve shape (inverted/flat/normal) adalah leading recession indicator.
Tambahan: tracking 2Y & 10Y yields sendiri + delta 1M/3M (momentum perubahan).
Slope perubahan sering lead shape inversion (mis. steepening bisa berubah jadi
flattening berbulan lalu sebelum akhirnya invert).
"""
from __future__ import annotations
from datetime import date, timedelta

from ..contracts import ComponentReading
from ..sources import fred
from ._util import ev, missing, source, th

NAME = "yield_curve"
METHOD_VERSION = "1.1.0"

# raw_score: normal=100 (no recession-risk signal), flat=50, inverted=0
RAW_SCORE = {"normal": 100.0, "flat": 50.0, "inverted": 0.0}

SERIES = {
    "T2Y": "DGS2",      # 2-year Treasury yield
    "T10Y": "DGS10",    # 10-year Treasury yield
    "T10Y2Y": "T10Y2Y", # 10Y-2Y spread
}


def compute() -> ComponentReading:
    try:
        obs_spread = fred.series_observations(SERIES["T10Y2Y"], limit=50)
        obs_2y = fred.series_observations(SERIES["T2Y"], limit=50)
        obs_10y = fred.series_observations(SERIES["T10Y"], limit=50)
    except Exception as exc:
        return missing(NAME, "direct", f"FRED yield curves gagal ditarik: {exc}")

    if not obs_spread or not obs_2y or not obs_10y:
        return missing(NAME, "direct", "Histori yield tidak cukup")

    date_spread, spread = obs_spread[0]
    date_2y, yield_2y = obs_2y[0]
    date_10y, yield_10y = obs_10y[0]

    # Delta 1M & 3M untuk spread & individual yields
    target_1m = (date.fromisoformat(date_spread[:10]) - timedelta(days=30)).isoformat()
    target_3m = (date.fromisoformat(date_spread[:10]) - timedelta(days=90)).isoformat()

    _, spread_1m_ago = fred.observation_near(obs_spread, target_1m)
    _, spread_3m_ago = fred.observation_near(obs_spread, target_3m)
    _, yield_2y_1m_ago = fred.observation_near(obs_2y, target_1m)
    _, yield_10y_1m_ago = fred.observation_near(obs_10y, target_1m)

    spread_delta_1m = spread - spread_1m_ago
    spread_delta_3m = spread - spread_3m_ago
    yield_2y_delta_1m = yield_2y - yield_2y_1m_ago
    yield_10y_delta_1m = yield_10y - yield_10y_1m_ago

    if spread < 0:
        shape = "inverted"
    elif spread < 0.25:
        shape = "flat"
    else:
        shape = "normal"

    # Summary: kurva shape + momentum
    momentum = "steepening" if spread_delta_1m > 0.1 else "flattening" if spread_delta_1m < -0.1 else "stabil"
    narrative = (
        f"10Y yield {yield_10y:.2f}% · 2Y yield {yield_2y:.2f}% (spread {spread:+.2f}pp, {shape}). "
        f"Momentum 30h: {momentum} ({spread_delta_1m:+.2f}pp). "
        f"Implikasi: kurva {'sudah invert — recession risk tinggi' if spread < 0 else 'normal — pertumbuhan terjaga' if spread >= 0.25 else 'flattening — waspadai transisi'}."
    )
    rule = "spread < 0 → inverted (recession signal); 0 ≤ spread < 0.25pp → flat; spread ≥ 0.25pp → normal"

    return ComponentReading(
        name=NAME,
        value={
            "spread_10y_2y": spread,
            "yield_2y": yield_2y,
            "yield_10y": yield_10y,
            "shape": shape,
            "spread_delta_1m": spread_delta_1m,
            "spread_delta_3m": spread_delta_3m,
            "yield_2y_delta_1m": yield_2y_delta_1m,
            "yield_10y_delta_1m": yield_10y_delta_1m,
            "momentum": momentum,
            "as_of": date_spread,
        },
        status="ok",
        kind="direct",
        method_version=METHOD_VERSION,
        sources=[source("FRED")],
        narrative=narrative,
        narrative_version="1.1.0",
        evidence=[
            ev("yield_2y", yield_2y, date_2y, f"FRED {SERIES['T2Y']}"),
            ev("yield_10y", yield_10y, date_10y, f"FRED {SERIES['T10Y']}"),
            ev("spread_10y_2y", spread, date_spread, f"FRED {SERIES['T10Y2Y']}"),
            ev("spread_delta_1m", spread_delta_1m, date_spread, f"Δ vs 30h"),
            ev("spread_delta_3m", spread_delta_3m, date_spread, f"Δ vs 90h"),
        ],
        rule=rule,
        thresholds=[
            th("inverted (recession signal) jika spread di bawah ini", "<", 0.0),
            th("flat jika spread di bawah ini", "<", 0.25),
        ],
        raw_score=RAW_SCORE[shape],
    )
