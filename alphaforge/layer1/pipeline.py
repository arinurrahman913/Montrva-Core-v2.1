"""Orkestrasi Layer 1 — DAG di 01_ARCHITECTURE/02_LAYER1_MARKET_CONTEXT.md §5:
11 komponen leaf paralel, lalu market_sentiment (satu-satunya composite).

Explainability overhaul (2026-07): komponen cuma mengisi evidence/rule/
thresholds/raw_score sendiri-sendiri (lihat masing-masing file di
components/); modul ini yang mengisi data_freshness/confidence/
contribution/conflicts secara terpusat setelah semua komponen selesai,
supaya formulanya konsisten dan cuma didefinisikan sekali, bukan diulang
12 kali. Juga menghitung LayerScore (skor kondisi pasar 0-100, beda dari
Confidence yang mengukur kualitas data) dan reasoning Confidence yang
lebih detail.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from .sources import yahoo
from .components import (
    business_cycle_stage,
    commodity_signals,
    credit_spread,
    currency_dxy,
    liquidity_conditions,
    macro_calendar,
    market_breadth,
    market_regime,
    money_flow,
    sector_rotation,
    volatility_index,
    yield_curve,
)
from .components import market_sentiment as market_sentiment_mod
from .components import market_regime as market_regime_mod
from .contracts import (
    ComponentReading, Confidence, Contribution, ContextSummary, LayerScore,
    MarketContextPackage, ScoreContribution, now_iso,
)

LEAF_COMPONENTS = {
    "business_cycle_stage": business_cycle_stage.compute,
    "sector_rotation": sector_rotation.compute,
    "money_flow": money_flow.compute,
    "liquidity_conditions": liquidity_conditions.compute,
    "yield_curve": yield_curve.compute,
    "market_regime": market_regime.compute,
    "macro_calendar": macro_calendar.compute,
    "currency_dxy": currency_dxy.compute,
    "commodity_signals": commodity_signals.compute,
    "volatility_index": volatility_index.compute,
    "market_breadth": market_breadth.compute,
    "credit_spread": credit_spread.compute,
}

CONTEXT_SUMMARY_METHOD_VERSION = "1.0.0"
LAYER_SCORE_METHOD_VERSION = "1.1.0"

# Bobot kontribusi ke LayerScore final, jumlah = 1.0. market_sentiment
# dikasih bobot kecil karena secara struktur sudah composite dari
# volatility_index + market_breadth (dua-duanya sudah ditimbang sendiri
# di atas) — bobot besar di sini akan menghitung sinyal yang sama 2x.
#
# credit_spread (2026-07, pasca-audit): ditambahkan sebagai leading indicator
# risk-appetite yang sebelumnya tidak ada. Bobot 12 komponen lama diskalakan
# ×0.90 (proporsi relatif dipertahankan) lalu credit_spread diberi 0.10 —
# signifikan karena secara historis lebih prediktif dibanding banyak proksi
# lain di sini, tapi tidak dominan.
WEIGHTS = {
    "market_regime": 0.135,
    "business_cycle_stage": 0.108,
    "yield_curve": 0.09,
    "volatility_index": 0.09,
    "market_breadth": 0.09,
    "liquidity_conditions": 0.09,
    "credit_spread": 0.10,
    "sector_rotation": 0.072,
    "money_flow": 0.063,
    "currency_dxy": 0.063,
    "commodity_signals": 0.045,
    "macro_calendar": 0.027,
    "market_sentiment": 0.027,
}

# Cadence (hari) dipakai buat klasifikasi data_freshness: fresh ≤1.5×cadence,
# acceptable ≤3×cadence, selain itu stale. macro_calendar dikecualikan
# (forward-looking, bukan data yang "menua").
CADENCE_DAYS = {
    "yield_curve": 1,
    "volatility_index": 1,
    "currency_dxy": 1,
    "commodity_signals": 1,
    "market_regime": 1,
    "sector_rotation": 1,
    "money_flow": 1,
    "market_breadth": 1,
    "market_sentiment": 1,
    "credit_spread": 1,          # BAMLH0A0HYM2 rilis harian (business day)
    "liquidity_conditions": 7,   # berbasis WALCL (mingguan), lebih sering dari M2SL (bulanan)
    "business_cycle_stage": 30,  # berbasis INDPRO (bulanan), paling sering dari GDP (kuartalan)
}


def _freshness_anchor_date(reading: ComponentReading) -> date | None:
    """Tanggal evidence paling baru dari komponen — dipakai sebagai acuan
    freshness. Kalau ada beberapa evidence dengan cadence beda (mis.
    liquidity_conditions: WALCL mingguan + M2SL bulanan), ambil yang paling
    baru — otomatis jatuh ke seri yang lebih sering update tanpa perlu
    hardcode per komponen."""
    dates = []
    for e in reading.evidence:
        try:
            dates.append(date.fromisoformat(str(e.as_of)))
        except (ValueError, TypeError):
            continue
    return max(dates) if dates else None


def _classify_freshness(name: str, reading: ComponentReading, today: date) -> str | None:
    if reading.status == "missing":
        return None  # tidak ada reading sama sekali, "stale" akan menyesatkan (implikasinya ada tapi basi)
    if name == "macro_calendar":
        return "fresh"  # forward-looking, bukan data yang menua
    anchor = _freshness_anchor_date(reading)
    cadence = CADENCE_DAYS.get(name, 1)
    if anchor is None:
        return "stale"
    age_days = (today - anchor).days
    if age_days <= cadence * 1.5:
        return "fresh"
    if age_days <= cadence * 3:
        return "acceptable"
    return "stale"


def _compute_confidence(reading: ComponentReading, freshness: str | None) -> float | None:
    """Confidence 0-100 khusus komponen ini — formula seragam, bukan
    diulang per komponen: mulai 100, -15 kalau freshness acceptable,
    -40 kalau stale, -25 kalau status degraded, floor 0."""
    if reading.status == "missing":
        return None
    confidence = 100.0
    if freshness == "acceptable":
        confidence -= 15.0
    elif freshness == "stale":
        confidence -= 40.0
    if reading.status == "degraded":
        confidence -= 25.0
    return max(0.0, confidence)


def _apply_contribution(name: str, reading: ComponentReading) -> None:
    if reading.status != "ok" or reading.raw_score is None:
        reading.contribution = None
        return
    weight = WEIGHTS.get(name, 0.0)
    reading.contribution = Contribution(score=reading.raw_score, weight=weight, weighted=reading.raw_score * weight)


def _detect_conflicts(components: dict[str, ComponentReading]) -> list[str]:
    """Beberapa rule konflik yang ditulis manual (bukan generic engine —
    12 komponen tidak butuh satu). Tiap konflik dicatat di kedua komponen
    yang terlibat (reading.conflicts), plus dikumpulkan buat reasoning
    Confidence di _build_context_summary."""
    conflicts_found: list[str] = []

    def flag(a: str, b: str, msg: str) -> None:
        conflicts_found.append(msg)
        if a in components:
            components[a].conflicts.append(msg)
        if b in components:
            components[b].conflicts.append(msg)

    dxy = components.get("currency_dxy")
    liquidity = components.get("liquidity_conditions")
    if dxy and liquidity and dxy.status == "ok" and liquidity.status == "ok":
        dxy_strengthening = dxy.value["change_30d_pct"] > 1.0
        liquidity_easing = not liquidity.value["tightening"]
        if dxy_strengthening and liquidity_easing:
            flag("currency_dxy", "liquidity_conditions",
                 "Dolar menguat di tengah likuiditas yang longgar (Fed tidak tightening) — sinyal campuran untuk aset berisiko.")

    # Likuiditas ↔ Credit Spread & DXY — reasoning lintas-indikator (#9): kondisi
    # likuiditas jarang berdiri sendiri, credit spread & dolar mengkonfirmasi
    # atau membantahnya.
    credit = components.get("credit_spread")
    if liquidity and credit and liquidity.status == "ok" and credit.status == "ok":
        tightening = liquidity.value["tightening"]
        spread_stress = credit.value["level"] == "wide" or credit.value.get("rising_fast")
        if tightening and spread_stress:
            flag("liquidity_conditions", "credit_spread",
                 "Likuiditas mengetat DAN credit spread melebar — dua leading indicator risk-off "
                 "saling menguatkan; kondisi pendanaan memburuk, screening lebih hati-hati.")
        elif not tightening and spread_stress:
            flag("liquidity_conditions", "credit_spread",
                 "Likuiditas masih longgar tapi credit spread mulai melebar — divergensi; "
                 "kredit sering memimpin, waspadai pengetatan yang belum terlihat di agregat likuiditas.")

    if dxy and liquidity and dxy.status == "ok" and liquidity.status == "ok":
        if dxy.value["change_30d_pct"] > 1.0 and liquidity.value["tightening"]:
            flag("liquidity_conditions", "currency_dxy",
                 "Dolar menguat DAN likuiditas mengetat bersamaan — pengetatan ganda kanal dolar; "
                 "headwind kuat untuk EM, komoditas, dan growth.")

    commodity = components.get("commodity_signals")
    if dxy and commodity and dxy.status == "ok" and commodity.status == "ok":
        dxy_strengthening = dxy.value["change_30d_pct"] > 1.0
        gold_rising = commodity.value["gold_change_30d_pct"] > 1.0
        if dxy_strengthening and gold_rising:
            flag("currency_dxy", "commodity_signals",
                 "Dolar dan emas naik bersamaan — biasanya berlawanan arah, sinyal permintaan safe-haven yang kuat meski dolar kuat.")

    regime = components.get("market_regime")
    yc = components.get("yield_curve")
    if regime and yc and regime.status == "ok" and yc.status == "ok":
        if regime.value["regime"] == "bull" and yc.value["shape"] == "inverted":
            flag("market_regime", "yield_curve",
                 "Regime pasar bull tapi yield curve inverted — klasik divergensi peringatan pra-resesi.")

    bcs = components.get("business_cycle_stage")
    if regime and bcs and regime.status == "ok" and bcs.status == "ok":
        if regime.value["regime"] == "bull" and bcs.value["stage"] in ("late-cycle", "recession"):
            flag("market_regime", "business_cycle_stage",
                 f"Regime pasar bull tapi siklus bisnis {bcs.value['stage']} — harga belum mencerminkan perlambatan makro.")

    return conflicts_found


# Label komponen versi manusia (Bahasa Indonesia) untuk narasi executive summary.
# Enum/label teknis tetap Inggris; prosa naratif ikut gaya komponen lain (ID).
COMPONENT_LABEL_ID = {
    "yield_curve": "Yield curve",
    "business_cycle_stage": "Siklus bisnis",
    "market_regime": "Regime pasar",
    "liquidity_conditions": "Likuiditas",
    "market_breadth": "Breadth pasar",
    "volatility_index": "Volatilitas (VIX)",
    "commodity_signals": "Komoditas",
    "sector_rotation": "Rotasi sektor",
    "money_flow": "Money flow",
    "currency_dxy": "Dolar (DXY)",
    "macro_calendar": "Kalender makro",
    "market_sentiment": "Sentimen pasar",
    "credit_spread": "Credit spread",
}


def _score_band_label(score: float) -> str:
    """Regime dari Layer Score final (0-100), selaras 4 zona background tren.
    <35 Risk-Off; 35-50 Neutral; 50-65 Neutral Positive; >65 Risk-On."""
    if score < 35:
        return "Risk-Off"
    if score < 50:
        return "Neutral"
    if score <= 65:
        return "Neutral Positive"
    return "Risk-On"


def _build_executive_summary(layer_score: LayerScore, components: dict[str, ComponentReading]) -> str:
    """Satu-dua kalimat menjawab 'so what' untuk screening — dirakit dari
    band regime + kontributor terkuat (driver) & terlemah (drag). Bukan
    sekadar mendeskripsikan status, tapi menyimpulkan implikasi tindakan."""
    band = layer_score.band_label
    implication = {
        "Risk-On": "kondisi mendukung screening lebih agresif.",
        "Neutral Positive": "screening tetap selektif sambil menunggu konfirmasi arah.",
        "Neutral": "screening sebaiknya tetap selektif.",
        "Risk-Off": "screening defensif dan sangat selektif.",
    }.get(band, "screening tetap selektif.")

    contribs = layer_score.contributions
    if not contribs:
        return f"Market {band} — {implication}"

    # driver = kontribusi paling menarik skor ke atas (score>50), drag = paling menekan ke bawah.
    driver = max(contribs, key=lambda c: (c.score - 50.0) * c.weight)
    drag = min(contribs, key=lambda c: (c.score - 50.0) * c.weight)
    driver_name = COMPONENT_LABEL_ID.get(driver.component, driver.component)
    drag_name = COMPONENT_LABEL_ID.get(drag.component, drag.component)

    clauses = [f"Market {band}."]
    has_driver = (driver.score - 50.0) > 2.0
    has_drag = (drag.score - 50.0) < -2.0
    if has_driver and has_drag:
        clauses.append(f"{driver_name} jadi pendorong utama namun {drag_name} masih menahan, sehingga {implication}")
    elif has_driver:
        clauses.append(f"{driver_name} jadi pendorong utama, sehingga {implication}")
    elif has_drag:
        clauses.append(f"{drag_name} masih menekan kondisi, sehingga {implication}")
    else:
        clauses.append(f"Sinyal antar-komponen relatif seimbang, sehingga {implication}")
    return " ".join(clauses)


def _build_layer_score(components: dict[str, ComponentReading]) -> LayerScore:
    included = []
    excluded = []
    for name in WEIGHTS:
        reading = components.get(name)
        if reading and reading.status == "ok" and reading.contribution:
            included.append((name, reading.contribution))
        else:
            excluded.append(name)

    total_weight = sum(c.weight for _, c in included)
    if total_weight > 0:
        # Renormalisasi: bobot komponen yang tidak ok tidak diam-diam
        # menyeret skor ke default, cuma didistribusikan ulang ke yang ada.
        final_score = sum(c.weighted for _, c in included) / total_weight
    else:
        final_score = 50.0  # tidak ada komponen ok sama sekali — netral, bukan 0

    contributions = [
        ScoreContribution(component=name, score=c.score, weight=c.weight, weighted=c.weighted)
        for name, c in included
    ]

    if excluded:
        reasoning = (
            f"Rata-rata tertimbang dari {len(included)}/{len(WEIGHTS)} komponen berstatus ok "
            f"(bobot dinormalisasi ulang ke {total_weight:.2f}). Dikecualikan: {', '.join(excluded)}."
        )
    else:
        reasoning = f"Rata-rata tertimbang dari semua {len(included)} komponen, semua berstatus ok."

    final_rounded = round(final_score, 1)
    return LayerScore(
        final_score=final_rounded,
        formula_version=LAYER_SCORE_METHOD_VERSION,
        contributions=contributions,
        excluded=excluded,
        reasoning=reasoning,
        band_label=_score_band_label(final_rounded),
    )


def _build_context_summary(components: dict, conflicts_found: list[str], layer_score: LayerScore | None = None) -> ContextSummary:
    degraded = [name for name, r in components.items() if r.status in ("degraded", "missing")]
    n_ok = len(components) - len(degraded)
    # Confidence: komponen degraded (punya data parsial) dapat kredit separuh,
    # bukan disamakan dengan missing (nihil data) — dulu keduanya dihitung 0.
    n_degraded_only = sum(1 for r in components.values() if r.status == "degraded")

    parts = []
    yc = components["yield_curve"]
    if yc.status == "ok":
        parts.append(f"yield curve {yc.value['shape']}")
    bcs = components["business_cycle_stage"]
    if bcs.status == "ok":
        parts.append(f"siklus {bcs.value['stage']}")
    regime = components["market_regime"]
    if regime.status == "ok":
        parts.append(f"regime {regime.value['regime']}")
    sentiment = components["market_sentiment"]
    if sentiment.status in ("ok", "degraded") and sentiment.value:
        parts.append(f"sentimen {sentiment.value['label']}")
    credit = components.get("credit_spread")
    if credit and credit.status == "ok":
        parts.append(f"credit spread {credit.value['level']}")

    narrative = (", ".join(parts).capitalize() + ".") if parts else "Konteks tidak bisa disusun — mayoritas komponen kosong."
    if degraded:
        narrative += f" {len(degraded)} dari {len(components)} komponen degraded/missing: {', '.join(degraded)}."

    if n_ok == len(components):
        band = "high"
    elif n_ok >= len(components) * 0.75:
        band = "medium"
    else:
        band = "low"

    freshness_tally: dict[str, int] = {}
    for r in components.values():
        if r.data_freshness:
            freshness_tally[r.data_freshness] = freshness_tally.get(r.data_freshness, 0) + 1

    reasons = [f"{n_ok}/{len(components)} komponen aktif (status ok)"]
    if degraded:
        reasons.append(f"{len(degraded)} komponen degraded/missing: {', '.join(degraded)}")
    if freshness_tally:
        freshness_parts = ", ".join(f"{count} {label}" for label, count in freshness_tally.items())
        reasons.append(f"Data freshness: {freshness_parts}")
    if conflicts_found:
        reasons.append(f"{len(conflicts_found)} konflik terdeteksi: {'; '.join(conflicts_found)}")
    else:
        reasons.append("Tidak ada konflik antar-indikator terdeteksi")
    reasons.append("Evidence lengkap untuk semua komponen berstatus ok" if n_ok else "Evidence terbatas — sebagian besar komponen tidak aktif")

    executive_summary = _build_executive_summary(layer_score, components) if layer_score else ""

    return ContextSummary(
        method_version=CONTEXT_SUMMARY_METHOD_VERSION,
        narrative=narrative,
        confidence=Confidence(score=(n_ok + 0.5 * n_degraded_only) / len(components) * 100.0, band=band, limiters=degraded, reasons=reasons),
        components_degraded=degraded,
        executive_summary=executive_summary,
    )


def _get_spx_validation_history() -> list[dict] | None:
    """Fetch S&P 500 last 90 days untuk validation chart overlay.
    Normalisasi 0-100 relative ke 52-week min/max untuk compare dengan Layer Score."""
    try:
        df = yahoo.history("^GSPC", period="1y")
        close = df["Close"]
        ma50 = close.rolling(50).mean()
        ma200 = close.rolling(200).mean()
        # Ambil last 90 hari
        end_idx = len(df) - 1
        start_idx = max(0, end_idx - 90)
        df_window = df.iloc[start_idx:end_idx+1]
        close_window = close.iloc[start_idx:end_idx+1]
        ma50_window = ma50.iloc[start_idx:end_idx+1]
        ma200_window = ma200.iloc[start_idx:end_idx+1]

        # Normalisasi harga ke 0-100: (price - min52w) / (max52w - min52w) * 100
        min_52w = close.iloc[-252:].min()  # last 52w
        max_52w = close.iloc[-252:].max()
        price_range = max_52w - min_52w
        if price_range == 0:
            price_range = 1

        import math
        history = []
        for idx, (dt, row) in enumerate(df_window.iterrows()):
            close_val = float(row["Close"])
            # Bar tanpa harga tutup dilewati SAMA SEKALI. Yahoo mengembalikan
            # baris untuk hari yang belum diperdagangkan kalau fetch berjalan
            # setelah tengah malam waktu setempat (run 2026-08-04 selesai
            # 00:06); seluruh nilainya NaN, dan json_safe menulisnya sebagai
            # null. Bar seperti itu tidak membawa informasi apa pun, tapi
            # sempat mengosongkan seluruh dashboard lewat `null.toFixed()`
            # di Layer1PerformanceChart.
            if math.isnan(close_val):
                continue
            norm_price = (close_val - min_52w) / price_range * 100.0
            if math.isnan(norm_price):
                continue
            ma50_val = float(ma50_window.iloc[idx]) if not math.isnan(float(ma50_window.iloc[idx])) else None
            ma200_val = float(ma200_window.iloc[idx]) if not math.isnan(float(ma200_window.iloc[idx])) else None
            history.append({
                "date": dt.strftime("%Y-%m-%d"),
                "price": close_val,
                "ma50": ma50_val,
                "ma200": ma200_val,
                "norm_score": round(norm_price, 1),
            })
        return history if history else None
    except Exception:
        return None


def build_market_context_package(price_cache: dict | None = None, session_id: str | None = None) -> MarketContextPackage:
    session_id = session_id or f"session-{uuid.uuid4().hex[:12]}"
    today = date.today()

    components: dict = {}
    with ThreadPoolExecutor(max_workers=len(LEAF_COMPONENTS)) as pool:
        futures = {
            name: pool.submit(fn, price_cache) if name == "market_breadth" else pool.submit(fn)
            for name, fn in LEAF_COMPONENTS.items()
        }
        for name, fut in futures.items():
            try:
                components[name] = fut.result()
            except Exception as exc:  # noqa: BLE001
                # Jaring pengaman: satu komponen yang gagal tak terduga (mis.
                # IndexError dari respons FRED kosong yang lolos try lokalnya)
                # tidak boleh mematikan seluruh build — jadikan missing supaya
                # 11 komponen lain tetap terkirim (jaminan §5 "Kalau Ada
                # Komponen yang Gagal").
                components[name] = ComponentReading(
                    name=name, value=None, status="missing", kind="derived",
                    note=f"Komponen gagal tak terduga: {type(exc).__name__}: {exc}",
                )

    try:
        components["market_sentiment"] = market_sentiment_mod.compute(
            vix_reading=components["volatility_index"],
            breadth_reading=components["market_breadth"],
        )
    except Exception as exc:  # noqa: BLE001
        components["market_sentiment"] = ComponentReading(
            name="market_sentiment", value=None, status="missing", kind="derived",
            note=f"Komponen gagal tak terduga: {type(exc).__name__}: {exc}",
        )

    # Enrich market_regime dengan agregasi konteks (post-pass) — klasifikasi inti
    # (MA50/MA200) tetap, cuma tambahkan aggregate_signals + triggers untuk reasoning.
    market_regime_mod.enrich_regime_aggregation(components.get("market_regime"), components)

    # Post-processing terpusat: freshness -> confidence -> contribution,
    # urutannya penting karena confidence butuh freshness duluan.
    for name, reading in components.items():
        freshness = _classify_freshness(name, reading, today)
        reading.data_freshness = freshness
        reading.confidence = _compute_confidence(reading, freshness)
        _apply_contribution(name, reading)

    conflicts_found = _detect_conflicts(components)
    layer_score = _build_layer_score(components)
    context_summary = _build_context_summary(components, conflicts_found, layer_score)

    spx_history = _get_spx_validation_history()

    # Data quality summary untuk dashboard stats card
    n_total = len(components)
    n_ok = sum(1 for c in components.values() if c.status == "ok")
    n_degraded = sum(1 for c in components.values() if c.status == "degraded")
    freshness_tally = {}
    for c in components.values():
        if c.data_freshness:
            freshness_tally[c.data_freshness] = freshness_tally.get(c.data_freshness, 0) + 1
    confidence_band = context_summary.confidence.band if context_summary else "medium"
    data_quality_summary = {
        "total": n_total,
        "ok": n_ok,
        "degraded": n_degraded,
        "fresh": freshness_tally.get("fresh", 0),
        "acceptable": freshness_tally.get("acceptable", 0),
        "stale": freshness_tally.get("stale", 0),
        "confidence_band": confidence_band,
    }

    return MarketContextPackage(
        session_id=session_id,
        components=components,
        context_summary=context_summary,
        layer_score=layer_score,
        generated_at=now_iso(),
        spx_history=spx_history,
        data_quality_summary=data_quality_summary,
    )
