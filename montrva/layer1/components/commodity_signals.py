"""02_LAYER1_SPECS/11_COMMODITY_SIGNALS.md — kind=direct, komponen leaf."""
from __future__ import annotations

from ..contracts import ComponentReading
from ..sources import yahoo
from ._util import ev, missing, percentile_rank, source, th

NAME = "commodity_signals"
METHOD_VERSION = "2.0.0"  # formula raw_score TIDAK berubah (emas+WTI percentile) — comparability histori terjaga
CHANGE_WINDOW_DAYS = 21  # ~1 bulan kalender (hari bursa)
LOOKBACK_PERIOD = "3y"

# Ambang arah perubahan ~1 bulan untuk klasifikasi pattern (bukan rata-rata).
DIR_FLAT = 1.5   # |Δ| <= ini → mendatar (→)
DIR_STRONG = 6.0  # |Δ| >= ini → kuat (↑↑ / ↓↓)


def _direction(chg: float) -> str:
    if chg >= DIR_STRONG:
        return "up_strong"
    if chg >= DIR_FLAT:
        return "up"
    if chg <= -DIR_STRONG:
        return "down_strong"
    if chg <= -DIR_FLAT:
        return "down"
    return "flat"


_ARROW = {"up_strong": "↑↑", "up": "↑", "flat": "→", "down": "↓", "down_strong": "↓↓"}


def _classify_pattern(oil: str, copper: str, gold: str) -> tuple[str, str, str]:
    """Kembalikan (key, label, interpretation) dari arah Oil/Copper/Gold.
    Berbasis POLA lintas-komoditas, bukan rata-rata — sinyal yang sama besarnya
    bisa berarti reflation (bullish siklikal) atau inflation shock (bearish
    margin) tergantung komposisinya."""
    up = {"up", "up_strong"}
    down = {"down", "down_strong"}
    oil_up, copper_up, gold_up = oil in up, copper in up, gold in up
    oil_dn, copper_dn, gold_dn = oil in down, copper in down, gold in down

    # Energi melonjak + emas naik + tembaga (proxy permintaan industri) melemah
    if oil == "up_strong" and gold_up and copper_dn:
        return ("inflation_shock", "Inflation Shock",
                "Energi melonjak & emas naik tapi tembaga melemah — inflasi dorongan-biaya, "
                "waspada tekanan margin & konsumsi (headwind saham).")
    # Permintaan industri + energi menguat bersamaan
    if oil_up and copper_up and not gold_dn:
        return ("reflation", "Reflation",
                "Minyak & tembaga menguat bersamaan — permintaan siklikal/industri pulih, "
                "umumnya suportif untuk saham siklikal & value.")
    # Emas menguat sementara siklikal (minyak+tembaga) melemah
    if gold_up and oil_dn and copper_dn:
        return ("risk_off", "Risk-Off / Flight to Safety",
                "Emas naik sementara minyak & tembaga turun — permintaan risiko melemah, "
                "aliran ke aset lindung nilai.")
    # Semua melemah
    if oil_dn and copper_dn and (gold_dn or gold == "flat"):
        return ("deflation", "Deflation / Demand Weakness",
                "Minyak & tembaga melemah bersama — permintaan agregat melunak, "
                "tekanan disinflasi/deflasi.")
    return ("mixed", "Mixed / No Clear Pattern",
            "Sinyal komoditas belum membentuk pola koheren — perlakukan sebagai netral.")

# raw_score: sinyal paling lemah/ambigu di antara 12 komponen (naik bisa
# berarti inflasi/late-cycle yang bearish buat saham, atau reflation yang
# bullish — spec sendiri memposisikan ini sebagai konteks sektor, bukan
# sinyal arah yang kuat), jadi score cuma bergerak tipis di sekitar netral
# 55, tidak pernah lebih ekstrem dari ±10 poin.
#
# Pasca-audit (2026-07): swing tetap dibatasi ±10 (desain sengaja, lihat
# catatan di atas) TAPI sekarang diskalakan dari PERCENTILE gabungan emas+WTI
# terhadap distribusi perubahan ~1 bulan 3 tahun terakhir — bukan dari %
# perubahan mentah yang clamp-nya berarti beda antar rezim volatilitas
# komoditas (mis. ±10% wajar di era volatile, ekstrem di era tenang).
BASELINE = 55.0
MAX_SWING = 10.0


def compute() -> ComponentReading:
    try:
        gold_df = yahoo.history("GC=F", period=LOOKBACK_PERIOD)
        gold_close = gold_df["Close"]
        gold = float(gold_close.iloc[-1])
        gold_as_of = gold_df.index[-1].strftime("%Y-%m-%d")
        gold_chg_series = gold_close.pct_change(CHANGE_WINDOW_DAYS) * 100.0
        gold_chg = float(gold_chg_series.iloc[-1])
        gold_history = gold_chg_series.dropna().tolist()

        wti_df = yahoo.history("CL=F", period=LOOKBACK_PERIOD)
        wti_close = wti_df["Close"]
        wti = float(wti_close.iloc[-1])
        wti_as_of = wti_df.index[-1].strftime("%Y-%m-%d")
        wti_chg_series = wti_close.pct_change(CHANGE_WINDOW_DAYS) * 100.0
        wti_chg = float(wti_chg_series.iloc[-1])
        wti_history = wti_chg_series.dropna().tolist()

        # Copper (HG=F) — proxy permintaan industri global ("Dr. Copper").
        # Ditambah untuk analisis POLA (reflation vs inflation shock); TIDAK
        # masuk raw_score (formula emas+WTI dipertahankan, comparability histori).
        copper_df = yahoo.history("HG=F", period=LOOKBACK_PERIOD)
        copper_close = copper_df["Close"]
        copper = float(copper_close.iloc[-1])
        copper_as_of = copper_df.index[-1].strftime("%Y-%m-%d")
        copper_chg = float((copper_close.pct_change(CHANGE_WINDOW_DAYS) * 100.0).iloc[-1])
    except Exception as exc:
        return missing(NAME, "direct", f"Yahoo futures gagal ditarik: {exc}", method_version=METHOD_VERSION)

    if not gold_history or not wti_history:
        return missing(NAME, "direct", "Histori GC=F/CL=F tidak cukup untuk hitung percentile.", method_version=METHOD_VERSION)

    gold_pct = percentile_rank(gold_history, gold_chg)
    wti_pct = percentile_rank(wti_history, wti_chg)
    avg_percentile = (gold_pct + wti_pct) / 2.0  # 0=paling lemah historis, 1=paling kuat

    oil_dir, copper_dir, gold_dir = _direction(wti_chg), _direction(copper_chg), _direction(gold_chg)
    pattern_key, pattern_label, pattern_interp = _classify_pattern(oil_dir, copper_dir, gold_dir)

    narrative = (
        f"Pola: {pattern_label}. Oil {_ARROW[oil_dir]} ({wti_chg:+.1f}%), "
        f"Copper {_ARROW[copper_dir]} ({copper_chg:+.1f}%), Gold {_ARROW[gold_dir]} ({gold_chg:+.1f}%) "
        f"~1 bulan. {pattern_interp}"
    )
    rule = (
        f"score = {BASELINE:.0f} - (percentile_gabungan-0.5)×2×{MAX_SWING:.0f} — percentile dari ranking "
        f"perubahan ~1bln emas & WTI vs distribusi {LOOKBACK_PERIOD} masing-masing, dirata-rata. "
        f"Swing dibatasi ±{MAX_SWING:.0f} poin (sengaja sempit — sinyal ambigu: naik bisa inflasi/late-cycle "
        f"bearish ATAU reflation bullish)."
    )

    swing = (avg_percentile - 0.5) * 2.0 * MAX_SWING
    raw_score = BASELINE - swing

    return ComponentReading(
        name=NAME,
        value={
            "gold": gold,
            "gold_change_30d_pct": gold_chg,
            "wti": wti,
            "wti_change_30d_pct": wti_chg,
            "copper": copper,
            "copper_change_30d_pct": copper_chg,
            "percentile_3y": avg_percentile,
            "pattern": pattern_key,
            "pattern_label": pattern_label,
            "pattern_interpretation": pattern_interp,
            "directions": {"oil": oil_dir, "copper": copper_dir, "gold": gold_dir},
        },
        status="ok",
        kind="direct",
        method_version=METHOD_VERSION,
        sources=[source("Yahoo Finance")],
        narrative=narrative,
        narrative_version="2.1.0",
        evidence=[
            ev("gold", gold, gold_as_of, "Yahoo Finance GC=F"),
            ev("gold_change_30d_pct", gold_chg, gold_as_of, "Yahoo Finance GC=F (~1 bulan, 21 hari bursa)"),
            ev("wti", wti, wti_as_of, "Yahoo Finance CL=F"),
            ev("wti_change_30d_pct", wti_chg, wti_as_of, "Yahoo Finance CL=F (~1 bulan, 21 hari bursa)"),
            ev("copper", copper, copper_as_of, "Yahoo Finance HG=F"),
            ev("copper_change_30d_pct", copper_chg, copper_as_of, "Yahoo Finance HG=F (~1 bulan, 21 hari bursa)"),
            ev("pattern", pattern_label, gold_as_of, "Derived — arah Oil/Copper/Gold ~1 bulan"),
            ev("percentile_3y", round(avg_percentile, 3), gold_as_of, f"Yahoo Finance GC=F+CL=F (persentil vs {LOOKBACK_PERIOD})"),
        ],
        rule=rule,
        thresholds=[th("swing dibatasi ± poin dari baseline 55", "clamp", MAX_SWING)],
        raw_score=raw_score,
    )
