"""02_LAYER1_SPECS/10_CURRENCY_DXY.md — kind=direct, komponen leaf.

Skor berbasis PERCENTILE historis (pasca-audit 2026-07) — dulu pakai band
% perubahan tetap (>+2%→25, dst) yang maknanya bisa melenceng antar rezim
volatilitas dolar (mis. +2% dalam 1 bulan itu besar di era DXY tenang,
biasa saja di era dolar bergejolak). Sekarang perubahan ~1 bulan (21 hari
bursa) hari ini diranking terhadap distribusi perubahan ~1 bulan yang sama
sepanjang 3 tahun terakhir — otomatis menyesuaikan diri ke rezim volatilitas
yang berlaku, bukan angka absolut tetap.
"""
from __future__ import annotations

from ..contracts import ComponentReading
from ..sources import yahoo
from ._util import ev, missing, percentile_rank, source, th

NAME = "currency_dxy"
METHOD_VERSION = "2.0.0"
CHANGE_WINDOW_DAYS = 21  # ~1 bulan kalender (hari bursa)
CHANGE_WINDOW_3M_DAYS = 63  # ~3 bulan kalender (hari bursa)
LOOKBACK_PERIOD = "3y"

# raw_score: dolar menguat = headwind buat emerging market, komoditas, dan
# growth stock (kanal likuiditas dolar) — jadi score turun saat DXY menguat
# relatif terhadap distribusi historisnya sendiri (percentile tinggi).
SCORE_MAX = 80.0
SCORE_MIN = 20.0


def compute() -> ComponentReading:
    try:
        df = yahoo.history("DX-Y.NYB", period=LOOKBACK_PERIOD)
        close = df["Close"]
        current = float(close.iloc[-1])
        as_of = df.index[-1].strftime("%Y-%m-%d")
        chg_series = close.pct_change(CHANGE_WINDOW_DAYS) * 100.0
        chg_30d = float(chg_series.iloc[-1])
        history_changes = chg_series.dropna().tolist()
        chg_90d = float((close.pct_change(CHANGE_WINDOW_3M_DAYS) * 100.0).iloc[-1]) if len(close) > CHANGE_WINDOW_3M_DAYS else None
    except Exception as exc:
        return missing(NAME, "direct", f"Yahoo DX-Y.NYB gagal ditarik: {exc}", method_version=METHOD_VERSION)

    if not history_changes:
        return missing(NAME, "direct", "Histori DX-Y.NYB tidak cukup untuk hitung percentile.", method_version=METHOD_VERSION)

    # 0 = dolar paling melemah dalam 3 tahun terakhir, 1 = paling menguat
    percentile = percentile_rank(history_changes, chg_30d)
    if percentile >= 0.75:
        level = "kuat"
    elif percentile <= 0.25:
        level = "lemah"
    else:
        level = "netral"

    raw_score = SCORE_MAX - percentile * (SCORE_MAX - SCORE_MIN)

    direction = "menguat" if chg_30d > 0 else "melemah"
    chg_90d_txt = f", {chg_90d:+.1f}% dalam ~3 bulan" if chg_90d is not None else ""
    narrative = (
        f"DXY {current:.2f}, {direction} {abs(chg_30d):.1f}% dalam ~1 bulan{chg_90d_txt} "
        f"(persentil-{round(percentile * 100)} vs {LOOKBACK_PERIOD} terakhir — {level})."
    )
    rule = (
        f"percentile = ranking perubahan ~1bln hari ini vs distribusi perubahan ~1bln {LOOKBACK_PERIOD} terakhir; "
        f"score = {SCORE_MAX:.0f} - percentile×{SCORE_MAX - SCORE_MIN:.0f} — dolar menguat relatif historis → score turun (headwind EM/komoditas/growth)"
    )

    return ComponentReading(
        name=NAME,
        value={"dxy": current, "change_30d_pct": chg_30d, "change_90d_pct": chg_90d,
               "percentile_3y": percentile, "level": level},
        status="ok",
        kind="direct",
        method_version=METHOD_VERSION,
        sources=[source("Yahoo Finance")],
        narrative=narrative,
        narrative_version="2.1.0",
        evidence=[
            ev("dxy", current, as_of, "Yahoo Finance DX-Y.NYB"),
            ev("change_30d_pct", chg_30d, as_of, "Yahoo Finance DX-Y.NYB (~1 bulan, 21 hari bursa)"),
            ev("change_90d_pct", chg_90d, as_of, "Yahoo Finance DX-Y.NYB (~3 bulan, 63 hari bursa)"),
            ev("percentile_3y", round(percentile, 3), as_of, f"Yahoo Finance DX-Y.NYB (persentil vs {len(history_changes)} observasi {LOOKBACK_PERIOD})"),
        ],
        rule=rule,
        thresholds=[
            th("lemah jika percentile di bawah ini", "<=", 0.25),
            th("kuat jika percentile di atas ini", ">=", 0.75),
        ],
        raw_score=raw_score,
    )
