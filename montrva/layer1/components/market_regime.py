"""02_LAYER1_SPECS/08_MARKET_REGIME.md — kind=direct, komponen leaf.

Klasifikasi inti tetap MA50/MA200 (raw_score 90/50/10) — comparability histori terjaga.
Agregasi konteks (breadth/vix/liquidity/credit/dxy/business_cycle) ditambah sebagai
reasoning layer post-pass di pipeline, bukan pengganti formula.
"""
from __future__ import annotations

from ..contracts import ComponentReading
from ..sources import yahoo
from ._util import ev, missing, source, th

NAME = "market_regime"
METHOD_VERSION = "1.1.0"  # Reasoning layer tambahan; formula MA tetap v1.0

RAW_SCORE = {"bull": 90.0, "sideways": 50.0, "bear": 10.0}


def _direction_of(reading: ComponentReading | None) -> str | None:
    """Arah sinyal suatu komponen (bullish/bearish/neutral) berdasarkan value yang ada."""
    if not reading or reading.status != "ok":
        return None
    v = reading.value or {}

    if reading.name == "volatility_index":
        level = v.get("level")
        if level == "low":
            return "bullish"
        if level == "high":
            return "bearish"
        return "neutral"

    if reading.name == "market_breadth" or "breadth" in reading.name:
        pct = v.get("pct_above_ma200")
        if pct is not None:
            if pct >= 60:
                return "bullish"
            if pct <= 40:
                return "bearish"
            return "neutral"

    if reading.name == "liquidity_conditions":
        if v.get("tightening"):
            return "bearish"
        return "bullish"

    if reading.name == "credit_spread":
        level = v.get("level")
        if level == "tight":
            return "bullish"
        if level == "wide":
            return "bearish"
        return "neutral"

    if reading.name == "currency_dxy":
        chg = v.get("change_30d_pct")
        if chg is not None:
            if chg > 1.0:
                return "bearish"  # dolar kuat = headwind
            if chg < -1.0:
                return "bullish"
        return "neutral"

    if reading.name == "business_cycle_stage":
        stage = v.get("stage")
        if stage in ("early-expansion", "mid-cycle"):
            return "bullish"
        if stage in ("late-cycle", "recession"):
            return "bearish"
        return "neutral"

    return None


def enrich_regime_aggregation(regime_reading: ComponentReading, components: dict) -> None:
    """Post-pass: enrich market_regime value dengan konteks agregasi dari komponen lain.
    Jangan ubah raw_score (tetap MA50/MA200), cuma tambah fields explanatory."""
    if not regime_reading or regime_reading.status != "ok":
        return

    signals = {}
    for name in ["volatility_index", "market_breadth", "liquidity_conditions", "credit_spread", "currency_dxy", "business_cycle_stage"]:
        c = components.get(name)
        if c:
            d = _direction_of(c)
            if d:
                signals[name] = d

    bullish = sum(1 for v in signals.values() if v == "bullish")
    bearish = sum(1 for v in signals.values() if v == "bearish")
    neutral = sum(1 for v in signals.values() if v == "neutral")

    regime = regime_reading.value.get("regime")
    # Triggers = kondisi apa yang bisa change current regime
    if regime == "bull":
        triggers = [
            "Bearish: price turun di bawah MA50",
            "Bearish: breadth melemah (<40% di atas MA200)",
            "Bearish: credit spread melebar cepat",
        ]
    elif regime == "bear":
        triggers = [
            "Bullish: price naik di atas MA50",
            "Bullish: breadth menguat (>60% di atas MA200)",
            "Bullish: credit spread menyempit",
        ]
    else:  # sideways
        triggers = [
            "Bullish: price tembus MA50 & MA200 sejajar",
            "Bearish: price menembus di bawah MA200",
            "Bullish/Bearish: breadth ekstrem (>70% atau <30%)",
        ]

    regime_reading.value["aggregate_signals"] = signals
    regime_reading.value["aggregate_summary"] = {
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
    }
    regime_reading.value["triggers"] = triggers
    regime_reading.narrative_version = "1.1.0"


def compute() -> ComponentReading:
    try:
        df = yahoo.history("^GSPC", period="2y")
        close = df["Close"]
        current = float(close.iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        as_of = df.index[-1].strftime("%Y-%m-%d")
    except Exception as exc:
        return missing(NAME, "direct", f"Yahoo ^GSPC gagal ditarik: {exc}")

    if current > ma50 > ma200:
        regime = "bull"
    elif current < ma50 < ma200:
        regime = "bear"
    else:
        regime = "sideways"

    dist_ma200_pct = (current - ma200) / ma200 * 100.0
    narrative = f"S&P 500 {current:,.0f}, {regime}. Jarak ke MA200: {dist_ma200_pct:+.1f}%. Konteks agregasi breadth/vix/likuiditas/credit/dxy/siklus bisnis akan ditambahkan post-pass."
    rule = "price > MA50 > MA200 → bull; price < MA50 < MA200 → bear; selain itu → sideways (klasifikasi inti, tetap v1.0)"

    return ComponentReading(
        name=NAME,
        value={"regime": regime, "price": current, "ma50": ma50, "ma200": ma200,
               "distance_to_ma200_pct": dist_ma200_pct},
        status="ok",
        kind="direct",
        method_version=METHOD_VERSION,
        sources=[source("Yahoo Finance")],
        narrative=narrative,
        narrative_version="1.1.0",
        evidence=[
            ev("price", current, as_of, "Yahoo Finance ^GSPC"),
            ev("ma50", ma50, as_of, "Yahoo Finance ^GSPC (rolling 50d)"),
            ev("ma200", ma200, as_of, "Yahoo Finance ^GSPC (rolling 200d)"),
        ],
        rule=rule,
        thresholds=[],  # klasifikasi ini urutan MA, bukan angka ambang tunggal
        raw_score=RAW_SCORE[regime],
    )
