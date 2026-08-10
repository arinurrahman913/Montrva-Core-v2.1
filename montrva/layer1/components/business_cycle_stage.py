"""02_LAYER1_SPECS/01_BUSINESS_CYCLE_STAGE.md — kind=derived, komponen leaf.

Catatan jujur soal sumber: spec menyebut PMI sebagai salah satu indikator,
tapi ISM Manufacturing PMI tidak tersedia gratis di FRED (berlisensi, seri
lama `NAPM` sudah ditarik). Sebagai gantinya dipakai Industrial Production
YoY (`INDPRO`) sebagai proksi arah aktivitas manufaktur — didokumentasikan
di sini, bukan disamarkan seolah PMI resmi.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..contracts import ComponentReading
from ..sources import fred
from ._util import ev, missing, source, th

NAME = "business_cycle_stage"
METHOD_VERSION = "1.0.0"

RAW_SCORE = {"early-cycle": 90.0, "mid-cycle": 65.0, "late-cycle": 35.0, "recession": 10.0}


def compute() -> ComponentReading:
    try:
        gdp_date, gdp_qoq = fred.latest_observation("A191RL1Q225SBEA")  # real GDP QoQ SAAR %
        # Ambil buffer ekstra: window 6-bulan/YoY dicari via TANGGAL (bukan
        # posisi indeks), jadi kebal terhadap gap nilai '.' & panjang variabel.
        unrate_obs = fred.series_observations("UNRATE", limit=10)
        indpro_obs = fred.series_observations("INDPRO", limit=16)
    except Exception as exc:
        return missing(NAME, "derived", f"FRED gagal ditarik: {exc}", method_version=METHOD_VERSION)

    if not unrate_obs or not indpro_obs:
        return missing(NAME, "derived", "FRED UNRATE/INDPRO balik kosong.", method_version=METHOD_VERSION)

    unrate_date, unrate_now = unrate_obs[0]
    _, unrate_6mo_ago = fred.observation_near(unrate_obs, (date.fromisoformat(unrate_date) - timedelta(days=182)).isoformat())
    unrate_rising = unrate_now > unrate_6mo_ago + 0.2

    indpro_date, indpro_now = indpro_obs[0]
    _, indpro_year_ago = fred.observation_near(indpro_obs, (date.fromisoformat(indpro_date) - timedelta(days=365)).isoformat())
    indpro_yoy = (indpro_now - indpro_year_ago) / indpro_year_ago * 100.0 if indpro_year_ago else 0.0

    expansive = indpro_yoy > 0
    if gdp_qoq < 0 and unrate_rising:
        stage = "recession"
    elif gdp_qoq > 2.5 and expansive and not unrate_rising:
        stage = "early-cycle"
    elif not expansive and unrate_rising:
        stage = "late-cycle"
    else:
        stage = "mid-cycle"

    narrative = (
        f"GDP QoQ {gdp_qoq:+.1f}% ({gdp_date}), Industrial Production {indpro_yoy:+.1f}% YoY "
        f"(proksi PMI, {indpro_date}), pengangguran {unrate_now:.1f}% "
        f"({'naik' if unrate_rising else 'stabil/turun'} vs 6 bulan lalu) → {stage}."
    )
    rule = (
        "GDP QoQ < 0 & unemployment naik → recession; "
        "GDP QoQ > 2.5% & INDPRO YoY > 0 & unemployment tidak naik → early-cycle; "
        "INDPRO YoY ≤ 0 & unemployment naik → late-cycle; selain itu → mid-cycle"
    )

    return ComponentReading(
        name=NAME,
        value={
            "stage": stage,
            "gdp_qoq_pct": gdp_qoq,
            "indpro_yoy_pct": indpro_yoy,
            "unemployment_rate": unrate_now,
            "unemployment_rising": unrate_rising,
        },
        status="ok",
        kind="derived",
        method_version=METHOD_VERSION,
        sources=[source("FRED")],
        note="PMI diganti Industrial Production YoY (INDPRO) — ISM PMI tidak gratis di FRED.",
        narrative=narrative,
        narrative_version="1.0.0",
        evidence=[
            ev("gdp_qoq_pct", gdp_qoq, gdp_date, "FRED A191RL1Q225SBEA (real GDP QoQ SAAR)"),
            ev("indpro_yoy_pct", indpro_yoy, indpro_date, "FRED INDPRO (YoY, proksi PMI)"),
            ev("unemployment_rate", unrate_now, unrate_date, "FRED UNRATE"),
            ev("unemployment_rising", unrate_rising, unrate_date, "FRED UNRATE (vs 6 bulan lalu)"),
        ],
        rule=rule,
        thresholds=[
            th("early-cycle butuh GDP QoQ di atas ini", ">", 2.5),
            th("unemployment dianggap naik jika delta 6bln di atas ini", ">", 0.2),
        ],
        raw_score=RAW_SCORE[stage],
    )
