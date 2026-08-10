"""02_LAYER1_SPECS/12_MARKET_SENTIMENT.md — kind=derived, satu-satunya
composite Layer 1 (butuh minimal VIX + Market Breadth, dihitung terakhir).

Memakai s.d. 6 input sentimen, SEMUA dari sumber resmi:
  1. VIX          — dari komponen volatility_index (Yahoo Finance), OTOMATIS
  2. Market Breadth — dari komponen market_breadth (universe internal), OTOMATIS
  3. CFTC COT     — positioning spekulan E-mini S&P 500 dari laporan CFTC
                     Commitments of Traders (resmi, mingguan, gratis via Socrata).
                     OTOMATIS — lihat sources/sentiment.fetch_cftc_spx().
  4. FINRA short  — rasio short-volume pasar dari file harian FINRA Reg SHO
                     (resmi, gratis). OTOMATIS — fetch_finra_short_volume().
                     Proksi kasar (short volume termasuk hedging market-maker).
  5. Put/Call     — input manual opsional (dashboard/data/sentiment_manual.json).
                     CBOE tidak punya API resmi gratis (403 saat dicoba).
                     Sumber otomatis via endpoint tidak resmi CNN Fear & Greed
                     SENGAJA DIMATIKAN (lihat sources/sentiment.py).
  6. AAII survey  — input manual opsional (dashboard/data/sentiment_manual.json),
                     karena AAII tidak punya API resmi gratis.

Dengan CFTC + FINRA, 4 input OTOMATIS (vix, breadth, cftc, finra) sudah cukup
untuk status `ok` TANPA input manual apa pun — put/call & AAII jadi pelengkap
opsional (menuju 6/6), bukan syarat.

status:
  - `ok`       bila ≥3 dari 6 input tersedia
  - `degraded` bila hanya 1–2 input
  - `missing`  bila tidak ada input sama sekali

`status` menilai KELENGKAPAN DATA, bukan arah sinyal. Arah ada di `value.label`
(fear/neutral/greed), field terpisah. Skor tinggi = sentimen mendukung risk
assets (bukan warning contrarian "kelewat greedy").
"""
from __future__ import annotations

from ..contracts import ComponentReading, now_iso
from ..sources import sentiment as sentiment_src
from ._util import ev, source, th

NAME = "market_sentiment"
METHOD_VERSION = "3.0.0"

VIX_SCORE_MAP = {"low": 75, "normal": 50, "high": 20}
TOTAL_INPUTS = 6   # vix, market_breadth, cftc, finra, put_call, aaii
OK_MIN_INPUTS = 3  # ≥3 dari 6 input → status ok


def compute(vix_reading: ComponentReading, breadth_reading: ComponentReading) -> ComponentReading:
    available: list[tuple[str, float]] = []  # (nama_input, skor_0_100)
    missing_inputs: list[str] = []
    evidence = []
    sources_used = []

    # 1. VIX
    if vix_reading.status == "ok":
        vlevel = vix_reading.value["level"]
        vas = vix_reading.evidence[0].as_of if vix_reading.evidence else now_iso()[:10]
        available.append(("vix", float(VIX_SCORE_MAP[vlevel])))
        evidence.append(ev("vix_level", vlevel, vas, "volatility_index (komponen ini)"))
    else:
        missing_inputs.append("vix")

    # 2. Market breadth
    if breadth_reading.status == "ok":
        bpct = breadth_reading.value["pct_above_ma200"]
        bas = breadth_reading.evidence[0].as_of if breadth_reading.evidence else now_iso()[:10]
        available.append(("market_breadth", float(min(100, max(0, bpct)))))
        evidence.append(ev("breadth_pct_above_ma200", bpct, bas, "market_breadth (komponen ini)"))
    else:
        missing_inputs.append("market_breadth")

    # 3. CFTC COT — positioning spekulan E-mini S&P 500 (OTOMATIS, resmi)
    cftc = sentiment_src.fetch_cftc_spx()
    if cftc:
        available.append(("cftc", float(cftc["score_0_100"])))
        evidence.append(ev("cftc_spx_net_long_pct", round(cftc["score_0_100"], 1), cftc.get("as_of") or now_iso()[:10],
                           "CFTC Commitments of Traders (E-mini S&P 500)"))
        sources_used.append(source("CFTC COT (Socrata)"))
    else:
        missing_inputs.append("cftc_cot")

    # 4. FINRA short-volume ratio (OTOMATIS, resmi) — proksi, dibalik ke greed
    finra = sentiment_src.fetch_finra_short_volume()
    if finra:
        available.append(("finra", float(finra["score_0_100"])))
        evidence.append(ev("finra_short_volume_pct", round(finra.get("short_ratio_pct", 0.0), 1),
                           finra.get("as_of") or now_iso()[:10], "FINRA Reg SHO daily short volume"))
        sources_used.append(source("FINRA Reg SHO"))
    else:
        missing_inputs.append("finra_short_volume")

    # 5. Put/Call — HANYA input manual. Sumber otomatis CNN Fear & Greed
    # (endpoint tidak resmi) sengaja dimatikan — lihat sources/sentiment.py
    # dan docstring modul ini.
    manual = sentiment_src.read_manual()
    pc = manual.get("put_call")
    if pc:
        available.append(("put_call", float(pc["score_0_100"])))
        evidence.append(ev("put_call_score", round(pc["score_0_100"], 1), pc.get("as_of") or now_iso()[:10],
                           "manual (CBOE)"))
        sources_used.append(source("manual (CBOE)"))
    else:
        missing_inputs.append("put_call_ratio")

    # 6. AAII survey — hanya dari input manual
    aaii = manual.get("aaii")
    if aaii:
        available.append(("aaii", float(aaii["score_0_100"])))
        evidence.append(ev("aaii_bull_bear", f"bull {aaii['bullish_pct']}% / bear {aaii['bearish_pct']}%",
                           aaii.get("as_of") or now_iso()[:10], "AAII (input manual)"))
        sources_used.append(source("AAII (manual)"))
    else:
        missing_inputs.append("aaii_survey")

    if not available:
        return ComponentReading(
            name=NAME, value=None, status="missing", kind="derived",
            method_version=METHOD_VERSION, inputs=["volatility_index", "market_breadth"],
            note=f"Tidak ada input tersedia — hilang: {', '.join(missing_inputs)}.",
            rule="score = rata-rata skor input yang tersedia; tidak ada input → missing",
        )

    score = sum(s for _, s in available) / len(available)
    label = "greed" if score >= 65 else "fear" if score <= 35 else "neutral"
    n = len(available)
    status = "ok" if n >= OK_MIN_INPUTS else "degraded"

    # Komposisi arah tiap input (ambang sama dgn label akhir) — supaya user tahu
    # KENAPA hasil akhir greed/neutral/fear, bukan cuma angka rata-ratanya.
    def _bucket(s: float) -> str:
        return "bullish" if s >= 65 else "bearish" if s <= 35 else "neutral"

    per_input = [{"input": nm, "score": round(s, 0), "stance": _bucket(s)} for nm, s in available]
    composition = {
        "bullish": sum(1 for x in per_input if x["stance"] == "bullish"),
        "neutral": sum(1 for x in per_input if x["stance"] == "neutral"),
        "bearish": sum(1 for x in per_input if x["stance"] == "bearish"),
    }

    used_names = [n for n, _ in available]
    comp_txt = f"Bullish {composition['bullish']} / Neutral {composition['neutral']} / Bearish {composition['bearish']}"
    narrative = (
        f"Signal: {label}. Skor sentimen {score:.0f}/100, dari {n} dari {TOTAL_INPUTS} input "
        f"({', '.join(used_names)}), dibobot rata. Komposisi: {comp_txt}."
    )
    if missing_inputs:
        narrative += f" Belum ada: {', '.join(missing_inputs)}."

    note = None
    if missing_inputs:
        note = (f"Hilang: {', '.join(missing_inputs)}. put/call & AAII hanya lewat input manual "
                "dashboard/data/sentiment_manual.json (tidak ada sumber otomatis resmi & gratis untuk keduanya); "
                "cftc & finra otomatis tapi best-effort (bisa hilang kalau jaringan/rilis belum tersedia).")

    rule = (
        "score = rata-rata tak berbobot dari input tersedia (skala 0-100, tinggi=greed) — "
        "vix: low→75/normal→50/high→20; breadth: %ticker di atas MA200; "
        "cftc: long/(long+short)×100 spekulan E-mini S&P 500; "
        "finra: short-volume dibalik (netral ~48%, proksi); "
        "put/call: skor manual (tinggi=greed); aaii: bull/(bull+bear)×100. "
        f"score≥65→greed, ≤35→fear, selain itu→neutral. status=ok bila ≥{OK_MIN_INPUTS}/{TOTAL_INPUTS} input tersedia."
    )

    return ComponentReading(
        name=NAME,
        value={"score_0_100": score, "label": label, "inputs_used": used_names,
               "inputs_total": TOTAL_INPUTS, "composition": composition, "per_input": per_input},
        status=status,
        kind="derived",
        method_version=METHOD_VERSION,
        inputs=["volatility_index", "market_breadth"],
        sources=sources_used,
        note=note,
        narrative=narrative,
        narrative_version="3.0.0",
        evidence=evidence,
        rule=rule,
        thresholds=[
            th("greed jika score di atas ini", ">=", 65),
            th("fear jika score di bawah ini", "<=", 35),
        ],
        raw_score=score,
    )
