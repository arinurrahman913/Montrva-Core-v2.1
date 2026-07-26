"""Flask API + static server for the AlphaForge dashboard.

Read-only: serves whatever the pipeline has already written to
dashboard/data/*.json, plus the built React app in frontend/dist. Does not
trigger Screening/Evidence/etc itself — that's scripts/refresh_layer1.py
(every ~2h) and scripts/refresh_full_pipeline.py (daily), run by Windows
Task Scheduler independently of whether this Flask process is even running.

Each stage file is reloaded lazily based on mtime (_get_stage below)
instead of being read once at import time — so once a scheduled refresh
script finishes writing new data, the next request picks it up automatically,
no restart needed. The stage files themselves are written atomically
(tmp file + os.replace) by the refresh scripts, so this never observes a
half-written file mid-reload.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alphaforge.layer2.sources.live_quote import fetch_live_quote  # noqa: E402
from alphaforge.layer2.sources.sector_map import (  # noqa: E402
    KNOWN_SECTORS, load_sector_map_meta as sector_map_meta
)
from alphaforge.layer2.ai_narrative import get_or_generate_narrative  # noqa: E402

DATA_DIR = ROOT / "dashboard" / "data"
FRONTEND_DIST = ROOT / "frontend" / "dist"

STAGE_FILES = {
    "layer1": "layer1_context.json",
    "layer1_history": "layer1_history.json",
    "screening": "screening.json",
    "evidence": "evidence.json",
    "knowledge": "knowledge.json",
    "catalyst": "catalysts.json",
    "peer": "peer_results.json",
    "confidence": "confidence_scores.json",
    "risk": "risk_assessments.json",
    "reasoning": "reasoning_outputs.json",
    "aggregator": "final_recommendations.json",
    "historical": "historical_timeline.json",
    "source_health": "source_health_history.json",
}

# name -> (mtime at last load, parsed JSON). Populated lazily on first request.
_stage_cache: dict[str, tuple[float, dict]] = {}


def _get_stage(name: str) -> dict:
    path = DATA_DIR / STAGE_FILES[name]
    if not path.exists():
        return {}

    mtime = path.stat().st_mtime
    cached = _stage_cache.get(name)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _stage_cache[name] = (mtime, data)
    return data


def _index_by_ticker(items: list[dict]) -> dict[str, dict]:
    return {item["ticker"]: item for item in items if "ticker" in item}


def _get_price_target_store() -> dict[str, list[dict]]:
    """Small ticker->[snapshot] file, appended to daily by
    price_target.sync_price_target_history() during a pipeline run — kept
    separate from evidence.json (mtime-cached the same way) so the dashboard
    can show accumulated history without waiting for the next full refresh
    to fold it back into the (much larger) evidence.json stage file."""
    path = DATA_DIR / "price_target_history.json"
    if not path.exists():
        return {}

    mtime = path.stat().st_mtime
    cached = _stage_cache.get("_price_target_store")
    if cached is not None and cached[0] == mtime:
        return cached[1]

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _stage_cache["_price_target_store"] = (mtime, data)
    return data


app = Flask(__name__, static_folder=str(FRONTEND_DIST), static_url_path="")


@app.get("/api/<stage>")
def get_stage(stage: str):
    if stage not in STAGE_FILES:
        return jsonify({"error": f"unknown stage '{stage}'"}), 404
    return jsonify(_get_stage(stage))


@app.get("/api/ticker/<ticker>")
def get_ticker_detail(ticker: str):
    ticker = ticker.upper()
    evidence = _index_by_ticker(_get_stage("evidence").get("packages", []))
    knowledge = _index_by_ticker(_get_stage("knowledge").get("profiles", []))
    catalyst = _index_by_ticker(_get_stage("catalyst").get("catalyst_sets", []))
    peer = _index_by_ticker(_get_stage("peer").get("comparisons", []))
    confidence = _index_by_ticker(_get_stage("confidence").get("scores", []))
    risk = _index_by_ticker(_get_stage("risk").get("assessments", []))
    reasoning = _index_by_ticker(_get_stage("reasoning").get("reasoning_outputs", []))
    aggregator = _index_by_ticker(_get_stage("aggregator").get("recommendations", []))
    historical = _get_stage("historical")

    evidence_entry = evidence.get(ticker)
    pt_history = _get_price_target_store().get(ticker)
    if evidence_entry and pt_history and evidence_entry.get("analyst_estimates"):
        # Shallow-copy so we never mutate the shared, mtime-cached evidence
        # dict — this merge is per-request display enrichment only.
        evidence_entry = dict(evidence_entry)
        evidence_entry["analyst_estimates"] = {
            **evidence_entry["analyst_estimates"],
            "price_target_history": pt_history,
        }

    return jsonify({
        "ticker": ticker,
        "evidence": evidence_entry,
        "knowledge": knowledge.get(ticker),
        "catalyst": catalyst.get(ticker),
        "peer": peer.get(ticker),
        "confidence": confidence.get(ticker),
        "risk": risk.get(ticker),
        "reasoning": reasoning.get(ticker),
        "aggregator": aggregator.get(ticker),
        "historical": historical.get(ticker),
    })


@app.get("/api/ticker/<ticker>/live")
def get_ticker_live_quote(ticker: str):
    """Level 3 freshness: fetches the current quote from Yahoo Finance right
    now (fast_info only, no history download), not the pipeline snapshot.
    Best-effort — times out and returns {"stale": true} rather than blocking
    the request if Yahoo is slow/unreachable."""
    return jsonify(fetch_live_quote(ticker))


@app.get("/api/ticker/<ticker>/ai-narrative")
def get_ticker_ai_narrative(ticker: str):
    """On-demand AI narrative (Gemini) for one ticker — reads the FULL
    Evidence package (all 8 sections) plus a few already-computed Knowledge
    metrics (trends/streak/price-target upside), deliberately NOT part of
    the full pipeline refresh (see ai_narrative.py docstring: cost/latency
    at ~5000-ticker scale for data nobody may ever view). Cached to
    dashboard/data/ai_narrative_cache.json, keyed by ticker +
    KnowledgeMetadata.evidence_date, so repeat views of the same ticker on
    the same data don't re-call the API."""
    ticker = ticker.upper()
    knowledge = _index_by_ticker(_get_stage("knowledge").get("profiles", []))
    profile = knowledge.get(ticker)
    if not profile:
        return jsonify({"narrative": None, "available": False, "error": "no knowledge profile"}), 404

    evidence = _index_by_ticker(_get_stage("evidence").get("packages", []))
    evidence_entry = evidence.get(ticker)
    if not evidence_entry:
        return jsonify({"narrative": None, "available": False, "error": "no evidence package"}), 404

    catalyst = _index_by_ticker(_get_stage("catalyst").get("catalyst_sets", []))
    catalyst_entry = catalyst.get(ticker)

    peer = _index_by_ticker(_get_stage("peer").get("comparisons", []))
    peer_entry = peer.get(ticker)

    result = get_or_generate_narrative(evidence_entry, profile, DATA_DIR / "ai_narrative_cache.json", catalyst_entry, peer_entry)
    return jsonify(result)


# --- Refresh pipeline dari dashboard (tombol Generate) -----------------------
# Menjalankan script refresh yang sudah ada sebagai subprocess di thread
# background, supaya request HTTP tidak nge-block. Status di-poll oleh frontend.
REFRESH_SCRIPTS = {
    "layer1": ROOT / "scripts" / "refresh_layer1.py",
    "full": ROOT / "scripts" / "refresh_full_pipeline.py",
}

_refresh_lock = threading.Lock()
_refresh_state: dict = {
    "running": False,
    "mode": None,
    "sector": None,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "message": None,
}


def _run_refresh(mode: str, sector: str | None = None) -> None:
    script = REFRESH_SCRIPTS[mode]
    ok = False
    msg = ""
    try:
        # mode="full" sekarang scan full-market (~5000+ ticker) secara default
        # (lihat SCREENING_LIMIT di scripts/refresh_full_pipeline.py) — bisa
        # makan waktu berjam-jam, jauh di atas 30 menit lama yang cukup untuk
        # sample 60-ticker. Kalau `sector` diisi, scope-nya jauh lebih kecil
        # (satu sektor GICS) jadi tetap cepat walau mode="full".
        # 6 jam (bukan 4) -- run 2026-07-24 selesai di ~3.3 jam, tapi run
        # 2026-07-25 kena banyak error/rate-limit Yahoo ekstra dan baru
        # dibunuh di batas 4 jam lama tanpa sempat selesai (all-or-nothing
        # writes berarti itu total kerja 4 jam hilang, gak ada yang tersimpan).
        timeout = 6 * 3600 if mode == "full" and not sector else 1800
        env = dict(os.environ)
        if sector:
            env["SCREENING_SECTOR"] = sector
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        ok = proc.returncode == 0
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if ok:
            msg = out.splitlines()[-1] if out else "Selesai."
        else:
            tail = err.splitlines()[-1] if err else f"exit code {proc.returncode}"
            msg = f"Gagal: {tail}"
    except subprocess.TimeoutExpired:
        # Pesan sebelumnya hardcode ">30 menit" walau timeout sesungguhnya
        # yang dipakai bisa 4/6 jam (mode="full") -- salah info soal berapa
        # lama proses itu benar-benar jalan sebelum dibunuh.
        msg = f"Timeout (>{timeout // 3600} jam)."
    except Exception as exc:  # noqa: BLE001
        msg = f"Error: {exc}"
    finally:
        with _refresh_lock:
            _refresh_state.update(running=False, finished_at=time.time(), ok=ok, message=msg)


@app.post("/api/refresh/<mode>")
def start_refresh(mode: str):
    if mode not in REFRESH_SCRIPTS:
        return jsonify({"error": f"unknown mode '{mode}'"}), 404
    sector = request.args.get("sector") or None
    with _refresh_lock:
        if _refresh_state["running"]:
            return jsonify({"running": True, "mode": _refresh_state["mode"], "already": True}), 409
        _refresh_state.update(
            running=True, mode=mode, sector=sector, started_at=time.time(), finished_at=None, ok=None, message=None
        )
    threading.Thread(target=_run_refresh, args=(mode, sector), daemon=True).start()
    return jsonify({"started": True, "mode": mode, "sector": sector})


@app.get("/api/refresh/status")
def refresh_status():
    with _refresh_lock:
        return jsonify(dict(_refresh_state))


def _avg(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _median(vals: list[float]) -> float | None:
    """Median, bukan mean — return_1y/pe_ratio/revenue_yoy semuanya fat-tailed
    (satu ticker naik ribuan persen menyeret rata-rata jauh dari kondisi
    ticker tipikal di sektor itu). Contoh nyata Technology di data live:
    mean return_1y +46.9% tapi median -3.1% — mean bikin kesan sektor sedang
    naik padahal saham tipikal di situ justru turun. institutional_pct tetap
    pakai mean (_avg) karena dibatasi 0-100%, jauh lebih tidak rawan skew."""
    vals = sorted(v for v in vals if v is not None)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


@app.get("/api/knowledge/sector-summary")
def get_knowledge_sector_summary():
    """Agregat per-sektor untuk Knowledge sector cards — dihitung di backend
    (bukan di browser) karena butuh join knowledge.json (profil) dengan
    reasoning_outputs.json (~40MB) dan risk_assessments.json (~15MB) per
    ticker; jauh lebih murah dilakukan sekali di sini daripada mengirim
    kedua file itu utuh ke browser untuk di-join di JS.

    "opportunity_count" = jumlah ticker dengan stance Speculative
    'asimetri_berkatalis' (termasuk yang dipicu insider Form 4 activity —
    lihat reasoning.py run_speculative_lens). "risk_flag_count" = jumlah
    ticker dengan >=1 RedFlag severity "high" (RiskAssessment.high_severity_count
    — leverage/liquidity/FCF/drawdown/valuation checks, lihat risk.py) ATAU
    >=1 spec Flag (04_RISK_REDFLAG_CHECK.md) berstatus triggered/halted.

    NOTE (dua sistem flag terpisah, lihat docstring Flag di risk_contracts.py):
    RiskAssessment.high_severity_count itu istilah Inggris "high" dari RedFlag
    lama (financial/valuation/momentum checks) — BUKAN Flag baru yang severity-
    nya "tinggi"/"ekstrem" (dilusi/auditor/restatement/litigasi/insider/fraud).
    Nama yang mirip ("high" vs Indonesia "tinggi") gampang ketuker; keduanya
    sengaja dihitung terpisah lalu di-OR di sini, bukan salah satu representasi
    "risiko tinggi" yang lebih otoritatif dari yang lain. Di data live saat ini
    Flag baru SELALU undetermined (Governance §7 fields belum diisi Evidence),
    jadi risk_flag_count secara praktis == high_severity_count>0 count; kolom
    ini tetap dijaga sinkron untuk sisi triggered/halted begitu Evidence
    diperluas.
    """
    profiles = _get_stage("knowledge").get("profiles", [])
    reasoning_by_ticker = _index_by_ticker(_get_stage("reasoning").get("reasoning_outputs", []))
    risk_by_ticker = _index_by_ticker(_get_stage("risk").get("assessments", []))

    by_sector: dict[str, list[dict]] = {}
    for p in profiles:
        by_sector.setdefault(p.get("sector") or "Lainnya", []).append(p)

    sectors = []
    for sector, tickers in by_sector.items():
        completions = [
            (t["metadata"]["fields_completed"] / t["metadata"]["fields_expected"]) * 100
            for t in tickers
            if t.get("metadata", {}).get("fields_expected")
        ]

        with_return = [t for t in tickers if t.get("historical_trend", {}).get("return_1y") is not None]
        leader = None
        if with_return:
            lp = max(with_return, key=lambda t: t["historical_trend"]["return_1y"])
            leader = {
                "ticker": lp["ticker"],
                "return_1y": lp["historical_trend"]["return_1y"],
                "pe_ratio": lp.get("valuation", {}).get("pe_ratio_trailing"),
            }

        opportunity_count = 0
        risk_flag_count = 0
        insider_active = 0
        insider_total = 0
        for t in tickers:
            r = reasoning_by_ticker.get(t["ticker"])
            if r and r.get("speculative", {}).get("stance") == "asimetri_berkatalis":
                opportunity_count += 1
            rk = risk_by_ticker.get(t["ticker"])
            spec_flag_triggered = any(
                f.get("status") == "triggered" for f in (rk.get("flags") or [])
            ) if rk else False
            if rk and (rk.get("high_severity_count", 0) > 0 or rk.get("halted") or spec_flag_triggered):
                risk_flag_count += 1
            n = t.get("ownership", {}).get("insider_filing_activity_30d") or 0
            insider_total += n
            if n > 0:
                insider_active += 1

        sectors.append({
            "sector": sector,
            "count": len(tickers),
            "avg_completion": _avg(completions),
            "median_return_1y": _median([t["historical_trend"]["return_1y"] for t in with_return]),
            "median_revenue_yoy": _median([
                t["financial_health"]["revenue_trend"]["yoy_q4"] for t in tickers
                if t.get("financial_health", {}).get("revenue_trend", {}).get("yoy_q4") is not None
            ]),
            "median_pe_ratio": _median([
                t["valuation"]["pe_ratio_trailing"] for t in tickers
                if t.get("valuation", {}).get("pe_ratio_trailing") is not None
            ]),
            "avg_institutional_pct": _avg([
                t["ownership"]["institutional_pct"] for t in tickers
                if t.get("ownership", {}).get("institutional_pct") is not None
            ]),
            "insider_active_tickers": insider_active,
            "insider_total_filings_30d": insider_total,
            "opportunity_count": opportunity_count,
            "risk_flag_count": risk_flag_count,
            "leader": leader,
        })

    sectors.sort(key=lambda s: s["count"], reverse=True)
    return jsonify({"sectors": sectors})


@app.get("/api/evidence/summary")
def get_evidence_summary():
    """Versi ringan evidence.json untuk EvidenceView — evidence.json penuh bisa
    ~275MB di skala full-market (price_history 1 tahun + quarterly_data + news
    + trades per ticker), terlalu besar untuk di-fetch+parse browser utuh demi
    tabel ringkasan. Endpoint ini strip array besar itu (price_history, item
    quarterly_data, item news, item trades) dan cuma kirim field skalar yang
    dipakai StatCards/tabel/source-health cards — metadata.status tiap section
    tetap disertakan (dipakai EvidenceView.jsx sourceStatus()/computeSourceStats()).
    Detail lengkap 1 ticker (utuh, termasuk array) tetap lewat /api/ticker/<t>
    yang cuma index 1 ticker, bukan seluruh populasi."""
    pkgs = _get_stage("evidence").get("packages", [])
    rows = []
    for p in pkgs:
        pm = p.get("price_market") or {}
        fd = p.get("fundamental") or {}
        io = p.get("institutional_ownership") or {}
        ia = p.get("institutional_activity") or {}
        nw = p.get("news") or {}
        sf = p.get("sec_filings") or {}
        rows.append({
            "ticker": p.get("ticker"),
            "price_market": {
                "close": pm.get("close"),
                "market_cap": pm.get("market_cap"),
                "metadata": pm.get("metadata"),
            },
            "fundamental": {
                "revenue": fd.get("revenue"),
                "net_income": fd.get("net_income"),
                "pe_ratio": fd.get("pe_ratio"),
                "quarterly_count": len(fd.get("quarterly_data") or []),
                "metadata": fd.get("metadata"),
            },
            "institutional_ownership": {
                "percentage": io.get("percentage"),
                "metadata": io.get("metadata"),
            },
            "institutional_activity": {
                "buy_count_30d": ia.get("buy_count_30d"),
                "metadata": ia.get("metadata"),
            },
            "news": {
                "count": nw.get("count", 0),
                "metadata": nw.get("metadata"),
            },
            "sec_filings": {
                "count": sf.get("count", 0),
                "metadata": sf.get("metadata"),
            },
        })
    return jsonify({"packages": rows, "total": len(rows)})


@app.get("/api/sectors")
def get_sectors():
    """Daftar sektor GICS yang bisa dipilih di dashboard + status sector_map
    (kapan terakhir dibangun, berapa ticker sudah termapping) — dipakai
    tombol screening per-sektor supaya user tahu mapnya sudah siap atau belum
    sebelum klik (kalau belum dibangun, scripts/build_sector_map.py perlu
    dijalankan dulu, kalau tidak filter sektor apapun hasilkan 0 kandidat)."""
    meta = sector_map_meta()
    return jsonify({
        "known_sectors": KNOWN_SECTORS,
        "map_built": meta is not None,
        "generated_at": meta.get("generated_at") if meta else None,
        "total_mapped": meta.get("total_mapped") if meta else 0,
        "coverage": meta.get("coverage") if meta else {},
    })


@app.get("/")
@app.get("/<path:path>")
def serve_frontend(path: str = ""):
    full_path = FRONTEND_DIST / path
    if path and full_path.exists() and full_path.is_file():
        return send_from_directory(FRONTEND_DIST, path)
    return send_from_directory(FRONTEND_DIST, "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
