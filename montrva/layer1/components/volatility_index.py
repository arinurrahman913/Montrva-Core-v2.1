"""02_LAYER1_SPECS/07_VOLATILITY_INDEX.md — kind=direct, komponen leaf."""
from __future__ import annotations

from ..contracts import ComponentReading
from ..sources import yahoo
from ._util import ev, missing, source, th

NAME = "volatility_index"

# raw_score: low=80 (calm, favorable for risk assets, tapi tidak 100 karena
# VIX yang terlalu rendah kadang mendahului lonjakan volatilitas), normal=55,
# high=15 (fear, headwind kuat buat risk assets).
RAW_SCORE = {"low": 80.0, "normal": 55.0, "high": 15.0}


def compute() -> ComponentReading:
    try:
        df = yahoo.history("^VIX", period="5y")
        current = float(df["Close"].iloc[-1])
        # Median, bukan mean: VIX berekor gemuk — mean 5y ketarik naik oleh
        # spike krisis (2020/2022) sehingga band "normal" bias tinggi dan VIX
        # lebih sering terbaca "low". Median jauh lebih tahan outlier.
        median_5y = float(df["Close"].median())
        as_of = df.index[-1].strftime("%Y-%m-%d")
    except Exception as exc:
        return missing(NAME, "direct", f"Yahoo ^VIX gagal ditarik: {exc}")

    if current < median_5y * 0.85:
        level = "low"
    elif current > median_5y * 1.25:
        level = "high"
    else:
        level = "normal"

    narrative = f"VIX {current:.1f}, {level} relatif terhadap median 5 tahun ({median_5y:.1f})."
    rule = "VIX < 85% median 5th → low; VIX > 125% median 5th → high; selain itu → normal (median tahan spike, beda dari mean)"

    return ComponentReading(
        name=NAME,
        value={"vix": current, "median_5y": median_5y, "level": level},
        status="ok",
        kind="direct",
        sources=[source("Yahoo Finance")],
        narrative=narrative,
        narrative_version="1.1.0",
        evidence=[
            ev("vix", current, as_of, "Yahoo Finance ^VIX"),
            ev("median_5y", median_5y, as_of, "Yahoo Finance ^VIX (median 5 tahun)"),
        ],
        rule=rule,
        thresholds=[
            th("low jika VIX di bawah median 5y ×", "<", 0.85),
            th("high jika VIX di atas median 5y ×", ">", 1.25),
        ],
        raw_score=RAW_SCORE[level],
    )
