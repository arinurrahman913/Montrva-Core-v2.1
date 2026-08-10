"""02_LAYER1_SPECS/06_MARKET_BREADTH.md — kind=derived, komponen leaf.

Universe = hasil Screening sendiri (D-05), bukan S&P 500 — lihat spec.
Screening (03_LAYER2_SPECS/01_SCREENING.md) sudah diimplementasikan dan
dipanggil oleh CLI lewat flag `layer1 --with-screening`, yang membangun
`price_cache` dan meneruskannya ke `compute(price_cache=...)` di bawah
(lihat montrva/cli.py + pipeline.py). Kalau `compute()` dipanggil tanpa
price_cache (mis. `layer1` tanpa flag itu), komponen ini sengaja
`status=missing` daripada memicu ribuan panggilan Yahoo sendiri (larangan
eksplisit di spec: "bukan memicu ribuan call sendiri") — itu bukan berarti
Screening belum ada, cuma belum diminta untuk run ini.

Nama tampilan sengaja "Internal Universe Breadth", bukan "Market Breadth"
polos — biar tidak disalahartikan sebagai breadth S&P 500 (identifier
internal `market_breadth` di pipeline.py/dict key tetap sama, cuma label
yang berubah, supaya tidak perlu ubah wiring di tempat lain).
"""
from __future__ import annotations

from ..contracts import ComponentReading
from ._util import ev, missing, th

NAME = "market_breadth"
DISPLAY_NAME = "Internal Universe Breadth"
METHOD_VERSION = "1.0.0"


def compute(price_cache: dict | None = None) -> ComponentReading:
    if price_cache is None:
        reading = missing(
            DISPLAY_NAME,
            "derived",
            "Cache harga universe Screening belum terisi untuk run ini (jalankan CLI dengan --with-screening).",
            method_version=METHOD_VERSION,
        )
        return reading

    advances = 0
    declines = 0
    above_ma200 = 0
    total = 0
    as_of = None
    for ticker, df in price_cache.items():
        if df is None or len(df) < 200:
            continue
        close = df["Close"]
        total += 1
        as_of = df.index[-1].strftime("%Y-%m-%d")
        if float(close.iloc[-1]) > float(close.iloc[-2]):
            advances += 1
        else:
            declines += 1
        if float(close.iloc[-1]) > float(close.rolling(200).mean().iloc[-1]):
            above_ma200 += 1

    if total == 0:
        return missing(DISPLAY_NAME, "derived", "Cache harga tidak punya histori cukup (>=200 hari).",
                        method_version=METHOD_VERSION)

    pct_above_ma200 = above_ma200 / total * 100.0

    # Band interpretasi absolut supaya angka % langsung punya makna.
    if pct_above_ma200 < 40:
        interpretation = "Weak"
    elif pct_above_ma200 < 60:
        interpretation = "Neutral"
    elif pct_above_ma200 < 80:
        interpretation = "Healthy"
    else:
        interpretation = "Very Strong"

    narrative = (
        f"[Internal Universe Breadth, BUKAN S&P 500] {advances}/{total} saham naik sesi terakhir. "
        f"{pct_above_ma200:.1f}% di atas MA200 — {interpretation}. Universe: hasil Screening sendiri ({total} ticker)."
    )
    rule = "score = % ticker universe internal di atas MA200 (dipakai langsung sebagai raw_score, sudah 0-100)"

    return ComponentReading(
        name=DISPLAY_NAME,
        value={
            "advances": advances,
            "declines": declines,
            "pct_above_ma200": pct_above_ma200,
            "universe_size": total,
            "interpretation": interpretation,
        },
        status="ok",
        kind="derived",
        method_version=METHOD_VERSION,
        note="Universe = hasil Screening sendiri (D-05), bukan konstituen S&P 500 — tidak sebanding "
             "dengan breadth publik manapun.",
        narrative=narrative,
        narrative_version="1.0.0",
        evidence=[
            ev("universe_size", total, as_of, "Internal Screening universe (bukan S&P 500)"),
            ev("pct_above_ma200", pct_above_ma200, as_of, "Internal Screening universe price cache"),
        ],
        rule=rule,
        thresholds=[th("ticker diikutkan jika histori harga minimal ini (hari)", ">=", 200)],
        raw_score=pct_above_ma200,
    )
