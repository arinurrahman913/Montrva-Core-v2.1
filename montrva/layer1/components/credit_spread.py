"""HY OAS (ICE BofA US High Yield Option-Adjusted Spread, FRED BAMLH0A0HYM2)
— kind=direct, komponen leaf.

Ditambahkan pasca-audit (2026-07): credit spread adalah salah satu leading
indicator risk-appetite paling reliable secara historis (melebar mendahului
risk-off, menyempit mendahului risk-on) dan sebelumnya tidak ada satu pun
komponen credit di Layer 1 — gap signifikan untuk sinyal kondisi pasar.

Skor berbasis PERCENTILE historis, bukan angka ambang tetap seperti
komponen lain (mis. band % perubahan tetap) — posisi spread hari ini
relatif terhadap histori yang tersedia lebih bermakna dan tahan-rezim
dibanding angka absolut yang maknanya berubah antar siklus suku bunga/kredit.

Catatan jujur soal panjang histori: seri ini idealnya punya data sejak
1996-12 di FRED, tapi per pengecekan langsung ke API (2026-07), FRED cuma
mengembalikan ~795 observasi mulai 2023-07-18 (count field API = 795)
walau diminta limit jauh lebih besar — indikasi seri ICE BofA ini sempat
di-restart/direvisi lisensinya di FRED, bukan bug di sisi kita. Percentile
di bawah ini karenanya dihitung atas ~3 tahun yang benar-benar tersedia,
bukan 5 tahun yang diklaim di awal pengembangan komponen ini — cukup
bermakna secara statistik, tapi lebih tipis dari idealnya.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..contracts import ComponentReading
from ..sources import fred
from ._util import ev, missing, percentile_rank, source, th

NAME = "credit_spread"
METHOD_VERSION = "1.0.0"
SERIES_ID = "BAMLH0A0HYM2"
LOOKBACK_OBS = 1300  # diminta sebanyak ini; FRED balikin sebanyak yang benar2 ada (lihat catatan di atas)

# Momentum: spread rendah TAPI naik cepat = warning dini (risk-off mulai
# terbentuk sebelum level absolut terlihat lebar). 1pp OAS = 100 bps.
MOMENTUM_WINDOW_DAYS = 30
RISING_FAST_BPS = 20.0  # Δ 30 hari di atas ini dianggap melebar cepat


def compute() -> ComponentReading:
    try:
        obs = fred.series_observations(SERIES_ID, limit=LOOKBACK_OBS)
    except Exception as exc:
        return missing(NAME, "direct", f"FRED {SERIES_ID} gagal ditarik: {exc}", method_version=METHOD_VERSION)

    if not obs:
        return missing(NAME, "direct", f"FRED {SERIES_ID} balik kosong.", method_version=METHOD_VERSION)

    as_of, current = obs[0]
    history_values = [v for _, v in obs]
    # 0 = spread tersempit dalam window historis, 1 = terlebar
    percentile = percentile_rank(history_values, current)

    if percentile <= 0.25:
        level = "tight"
    elif percentile >= 0.75:
        level = "wide"
    else:
        level = "normal"

    raw_score = (1.0 - percentile) * 100.0  # spread lebar (credit stress) → score rendah

    # Momentum 30 hari dalam basis point (Δ OAS × 100). observation_near tahan
    # gap tanggal (seri harian tapi hanya hari bursa).
    target = (date.fromisoformat(as_of[:10]) - timedelta(days=MOMENTUM_WINDOW_DAYS)).isoformat()
    prior_as_of, prior = fred.observation_near(obs, target)
    current_bps = current * 100.0
    momentum_bps = (current - prior) * 100.0
    rising_fast = momentum_bps >= RISING_FAST_BPS
    mom_dir = "melebar" if momentum_bps > 5 else "menyempit" if momentum_bps < -5 else "stabil"

    if level == "tight" and rising_fast:
        summary = (f"Spread masih rendah (persentil-{round(percentile * 100)}) TAPI melebar cepat "
                   f"({momentum_bps:+.0f} bps/30h) — warning dini risk-off, jangan hanya lihat level.")
    elif level == "wide":
        summary = f"Spread lebar (persentil-{round(percentile * 100)}) — credit stress, kondisi risk-off."
    elif rising_fast:
        summary = f"Spread {mom_dir} {momentum_bps:+.0f} bps/30h — momentum kredit memburuk, pantau."
    else:
        summary = (f"Spread {level} (persentil-{round(percentile * 100)}), momentum {mom_dir} "
                   f"({momentum_bps:+.0f} bps/30h) — kondisi kredit terkendali.")

    narrative = (
        f"HY OAS {current:.2f}pp ({current_bps:.0f} bps), persentil-{round(percentile * 100)} dari "
        f"{len(obs)} observasi (~{len(obs) // 252} tahun) — {level}. Momentum 30h: {momentum_bps:+.0f} bps ({mom_dir}). {summary}"
    )
    rule = (
        "percentile = fraksi observasi historis dgn spread <= hari ini (0=tersempit, 1=terlebar); "
        "score = (1-percentile)*100 — spread lebar (credit stress) → score rendah; "
        "percentile<=0.25 → tight, >=0.75 → wide, selain itu → normal"
    )

    return ComponentReading(
        name=NAME,
        value={
            "oas_pct": current,
            "oas_bps": round(current_bps, 0),
            "percentile_5y": percentile,
            "level": level,
            "momentum_30d_bps": round(momentum_bps, 0),
            "momentum_direction": mom_dir,
            "rising_fast": rising_fast,
            "summary": summary,
        },
        status="ok",
        kind="direct",
        method_version=METHOD_VERSION,
        sources=[source("FRED")],
        narrative=narrative,
        narrative_version="1.1.0",
        evidence=[
            ev("oas_pct", current, as_of, f"FRED {SERIES_ID} (ICE BofA US High Yield OAS)"),
            ev("oas_bps", round(current_bps, 0), as_of, f"FRED {SERIES_ID} (dalam basis point)"),
            ev("momentum_30d_bps", round(momentum_bps, 0), as_of, f"FRED {SERIES_ID} (Δ vs {prior_as_of[:10]}, ~30 hari)"),
            ev("percentile_5y", round(percentile, 3), as_of, f"FRED {SERIES_ID} (persentil vs {len(obs)} observasi)"),
        ],
        rule=rule,
        thresholds=[
            th("tight jika percentile di bawah ini", "<=", 0.25),
            th("wide jika percentile di atas ini", ">=", 0.75),
        ],
        raw_score=raw_score,
    )
