"""02_LAYER1_SPECS/03_MONEY_FLOW.md — kind=derived, komponen leaf.

Proksi: volume abnormal + arah harga di sector ETF (bukan data flow
institusional presisi seperti EPFR/Lipper, yang berbayar).
"""
from __future__ import annotations

from ..contracts import ComponentReading
from ..sources import yahoo
from ._util import ev, missing, source, th

NAME = "money_flow"
METHOD_VERSION = "1.1.0"

SECTOR_ETFS = ["XLK", "XLE", "XLF", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]
VOL_RATIO_THRESHOLD = 1.3
# Jendela agregasi. Dulu 1 hari (volume + arah harga hari terakhir) — sangat
# berisik, arahnya bisa berbalik tiap hari. 3 hari menyaring noise satu sesi
# jadi hanya lonjakan volume yang bertahan yang terhitung sebagai flow.
WINDOW = 3


def compute() -> ComponentReading:
    try:
        flows = {}
        as_of = None
        for etf in SECTOR_ETFS:
            df = yahoo.history(etf, period="1y")  # period sama dgn sector_rotation → reuse cache
            as_of = df.index[-1].strftime("%Y-%m-%d")
            vol = df["Volume"]
            close = df["Close"]
            if len(df) < 20 + WINDOW + 1:
                # Histori tidak cukup untuk jendela 3-hari + baseline 20-hari.
                flows[etf] = {"volume_ratio_vs_20d": 0.0, "price_change_pct": 0.0, "direction": "neutral"}
                continue
            recent_vol = float(vol.iloc[-WINDOW:].mean())
            avg_vol_20d = float(vol.iloc[-(20 + WINDOW):-WINDOW].mean())  # 20 hari SEBELUM jendela
            price_chg = (float(close.iloc[-1]) - float(close.iloc[-1 - WINDOW])) / float(close.iloc[-1 - WINDOW]) * 100.0
            vol_ratio = recent_vol / avg_vol_20d if avg_vol_20d else 0.0
            if vol_ratio > VOL_RATIO_THRESHOLD and price_chg > 0:
                direction = "inflow"
            elif vol_ratio > VOL_RATIO_THRESHOLD and price_chg < 0:
                direction = "outflow"
            else:
                direction = "neutral"
            flows[etf] = {"volume_ratio_vs_20d": vol_ratio, "price_change_pct": price_chg, "direction": direction}
    except Exception as exc:
        return missing(NAME, "derived", f"Yahoo sector ETF gagal ditarik: {exc}", method_version=METHOD_VERSION)

    inflows = [k for k, v in flows.items() if v["direction"] == "inflow"]
    outflows = [k for k, v in flows.items() if v["direction"] == "outflow"]

    # Money Flow adalah KONFIRMASI, bukan penentu arah — proxy volume+harga
    # tidak setara data flow institusional. Kalimat eksplisit soal keseimbangan
    # inflow/outflow supaya label "flat" tidak disalahartikan sebagai sinyal.
    diff = len(inflows) - len(outflows)
    if diff == 0:
        flow_state = "seimbang"
        confirmation_note = ("Inflow dan outflow relatif seimbang sehingga belum ada arah dominan. "
                             "Money Flow di sini berperan sebagai konfirmasi, bukan penentu arah pasar.")
    elif diff > 0:
        flow_state = "net_inflow"
        confirmation_note = (f"Net inflow tipis ({len(inflows)} vs {len(outflows)} sektor) — mengonfirmasi "
                             "minat beli, tapi bukan penentu arah (proxy volume+harga, bukan flow institusional).")
    else:
        flow_state = "net_outflow"
        confirmation_note = (f"Net outflow tipis ({len(outflows)} vs {len(inflows)} sektor) — mengonfirmasi "
                             "tekanan jual, tapi bukan penentu arah (proxy volume+harga, bukan flow institusional).")

    narrative = (
        f"Proksi volume+harga (bukan data flow institusional). Inflow terdeteksi di: "
        f"{', '.join(inflows) if inflows else 'tidak ada'}. "
        f"Outflow di: {', '.join(outflows) if outflows else 'tidak ada'} "
        f"(volume rata-rata {WINDOW} hari >30% di atas rata-rata 20 hari + arah harga {WINDOW} hari, universe {len(SECTOR_ETFS)} sector ETF). "
        f"{confirmation_note}"
    )
    rule = f"volume {WINDOW}h/rata2 20h > {VOL_RATIO_THRESHOLD} & harga {WINDOW}h naik → inflow; > {VOL_RATIO_THRESHOLD} & harga {WINDOW}h turun → outflow; selain itu → neutral"
    raw_score = max(0.0, min(100.0, 50.0 + (len(inflows) - len(outflows)) / len(SECTOR_ETFS) * 50.0))

    return ComponentReading(
        name=NAME,
        value={"sectors": flows, "inflows": inflows, "outflows": outflows,
               "flow_state": flow_state, "confirmation_note": confirmation_note, "role": "confirmation"},
        status="ok",
        kind="derived",
        method_version=METHOD_VERSION,
        sources=[source("Yahoo Finance")],
        narrative=narrative,
        narrative_version="1.0.0",
        evidence=[
            ev("universe_size", len(SECTOR_ETFS), as_of, "Yahoo Finance sector ETF"),
            ev("inflows", inflows, as_of, "Yahoo Finance sector ETF volume+price"),
            ev("outflows", outflows, as_of, "Yahoo Finance sector ETF volume+price"),
        ],
        rule=rule,
        thresholds=[th("volume ratio vs rata-rata 20d di atas ini dianggap abnormal", ">", VOL_RATIO_THRESHOLD)],
        raw_score=raw_score,
    )
