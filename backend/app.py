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

import gzip
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alphaforge import runlock  # noqa: E402
from alphaforge.layer2.sources.live_quote import fetch_live_quote  # noqa: E402
from alphaforge.layer2.sources.sector_map import (  # noqa: E402
    KNOWN_SECTORS, load_sector_map_meta as sector_map_meta
)
from alphaforge.layer2.ai_narrative import get_or_generate_narrative  # noqa: E402

DATA_DIR = ROOT / "dashboard" / "data"
# Sama dengan LOG_DIR di scripts/refresh_full_pipeline.py — log kegagalan
# refresh ditulis ke sini (lihat _dump_failure_log).
LOG_DIR = ROOT / "logs"

# Lapisan pribadi -- OPSIONAL. Kalau alphaforge/personal/ atau
# backend/personal_routes.py dihapus (rilis publik), import ini gagal dan
# PERSONAL_ENABLED jadi False -- sisa route di bawah tidak terpengaruh sama
# sekali. Frontend membaca PERSONAL_ENABLED lewat /api/capabilities untuk
# tahu apakah grup nav "Pribadi" perlu ditampilkan.
try:
    from backend.personal_routes import register as register_personal_routes  # noqa: E402
    PERSONAL_ENABLED = True
except ImportError:
    PERSONAL_ENABLED = False
FRONTEND_DIST = ROOT / "frontend" / "dist"

STAGE_FILES = {
    "layer1": "layer1_context.json",
    "layer1_history": "layer1_history.json",
    "screening": "screening.json",
    "evidence": "evidence.json",
    "knowledge": "knowledge.json",
    "catalyst": "catalysts.json",
    "institutional_flow": "institutional_flow.json",
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

# Manual gzip instead of flask-compress -- one dependency avoided for one
# after_request hook. Full-market runs (4000+ tickers) pushed /api/peer,
# /api/reasoning etc into the tens-of-MB range (was low hundreds-of-KB at
# the old ~90-ticker sample scale), so this went from "nice to have" to
# "page is blank for several seconds while the browser downloads it" --
# see dashboard perf notes 2026-07-26. Only compress if the client says it
# can decompress (Accept-Encoding) and the body's big enough that gzip's
# CPU cost is worth it; skip already-encoded/streamed responses.
_GZIP_MIN_BYTES = 1024


@app.after_request
def _compress_response(response):
    accepts_gzip = "gzip" in request.headers.get("Accept-Encoding", "")
    if (
        not accepts_gzip
        or response.direct_passthrough
        or response.content_encoding
        or response.content_length is None
        or response.content_length < _GZIP_MIN_BYTES
    ):
        return response
    response.set_data(gzip.compress(response.get_data(), compresslevel=6))
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Vary"] = "Accept-Encoding"
    return response


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


# Baris-baris yang BUKAN pesan exception sesungguhnya: catatan konteks PEP 678
# (Python 3.11+) yang dicetak SETELAH baris exception, plus rangka traceback.
# Dulu kode ini cuma menyimpan `err.splitlines()[-1]`, yang untuk error json
# justru mengambil catatannya ("when serializing dict item 'packages'") dan
# MEMBUANG penyebab aslinya (run 2026-07-30: MemoryError) — 2 jam kerja hilang
# tanpa satu pun petunjuk yang bisa dipakai.
_TRACEBACK_NOISE_PREFIXES = (
    "when serializing ", "Traceback (most recent call last)", "  File \"", "    ",
    "The above exception", "During handling of", "^", "~",
)

# Baris terakhir sebuah traceback Python: "NamaError: pesan" / "MemoryError".
# Mencarinya secara eksplisit jauh lebih andal daripada sekadar "baris terakhir
# yang bukan noise" (lihat docstring di bawah).
_EXCEPTION_LINE = re.compile(
    r"^(?:[A-Za-z_][\w.]*\.)?[A-Za-z_]\w*(?:Error|Exception|Interrupt|Warning|Exit)\b")

# Pesan gagal diteruskan ke /api/refresh/status lalu dirender apa adanya di
# dashboard. Exception dari pandas/numpy bisa membawa repr array raksasa —
# tanpa batas ini, field JSON yang di-poll tiap 2.5 detik bisa jadi megabyte.
_FAILURE_MSG_MAX_CHARS = 400


def _summarize_failure(err: str, returncode: int, out: str = "") -> str:
    """Ambil baris exception yang benar-benar informatif dari keluaran proses.

    HARUS memeriksa stdout juga, bukan stderr saja: scripts/refresh_full_pipeline.py
    memasang `logging.StreamHandler(sys.stdout)` dan melaporkan kegagalan lewat
    `log.exception(...)`, jadi traceback untuk hampir semua kegagalan (Screening,
    Evidence, Knowledge, dst) mendarat di STDOUT. Sementara stderr justru penuh
    baris progres ("Peer 4050/4055: ZIM", "Peer Comparison complete: ...") yang
    tidak cocok dengan pola noise mana pun — versi sebelumnya karena itu bisa
    melaporkan baris SUKSES sebagai sebab kegagalan.

    Strategi: cari baris exception sungguhan (pola `NamaError: ...`) dari bawah
    ke atas di stderr lalu stdout; kalau tidak ada, baru jatuh ke pemindaian
    "baris bermakna terakhir".
    """
    def _split(text: str) -> list[str]:
        return [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]

    def _clip(msg: str) -> str:
        return msg if len(msg) <= _FAILURE_MSG_MAX_CHARS else msg[:_FAILURE_MSG_MAX_CHARS] + " […]"

    streams = [_split(err), _split(out)]
    if not any(streams):
        return f"exit code {returncode}"

    # 1) Baris exception sesungguhnya, plus catatan PEP 678 sesudahnya bila ada.
    for lines in streams:
        note = None
        for line in reversed(lines):
            if line.startswith("when serializing "):
                note = line
                continue
            if _EXCEPTION_LINE.match(line):
                return _clip(f"{line} [{note}]" if note else line)

    # 2) Tidak ada pola exception — ambil baris bermakna terakhir.
    for lines in streams:
        for line in reversed(lines):
            if not line.startswith(_TRACEBACK_NOISE_PREFIXES):
                return _clip(line)

    return _clip(streams[0][-1] if streams[0] else f"exit code {returncode}")


def _dump_failure_log(mode: str, sector: str | None, returncode: int, out: str, err: str) -> None:
    """Simpan stdout+stderr LENGKAP ke file supaya kegagalan bisa didiagnosis.

    `subprocess.run(capture_output=True)` menahan seluruh keluaran di memori,
    dan sebelumnya semuanya dibuang kecuali satu baris. Sekarang utuh di disk."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        path = LOG_DIR / f"refresh_failure_{mode}_{stamp}.log"
        with open(path, "w", encoding="utf-8", errors="replace") as f:
            f.write(f"mode={mode} sector={sector} returncode={returncode}\n")
            f.write(f"=== STDERR ({len(err)} chars) ===\n{err}\n")
            f.write(f"=== STDOUT ({len(out)} chars) ===\n{out}\n")
        print(f"[refresh] kegagalan lengkap ditulis ke {path}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[refresh] gagal menulis log kegagalan: {exc}", file=sys.stderr)


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
            msg = f"Gagal: {_summarize_failure(err, proc.returncode, out)}"
            _dump_failure_log(mode, sector, proc.returncode, out, err)
    except subprocess.TimeoutExpired as exc:
        # Pesan sebelumnya hardcode ">30 menit" walau timeout sesungguhnya
        # yang dipakai bisa 4/6 jam (mode="full") -- salah info soal berapa
        # lama proses itu benar-benar jalan sebelum dibunuh.
        # Dinyatakan dalam menit kalau di bawah sejam: `1800 // 3600` == 0,
        # jadi run per-sektor dulu melaporkan "Timeout (>0 jam)".
        msg = (f"Timeout (>{timeout // 3600} jam)." if timeout >= 3600
               else f"Timeout (>{timeout // 60} menit).")
        # Timeout adalah kegagalan TERMAHAL yang bisa terjadi (bisa 6 jam kerja
        # hilang) dan justru satu-satunya jalur yang dulu tidak menyimpan apa
        # pun. TimeoutExpired membawa stdout/stderr yang sudah terkumpul —
        # dulu dibuang begitu saja.
        t_out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        t_err = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        _dump_failure_log(mode, sector, "TIMEOUT", t_out, t_err)
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

        # Kunci di disk dicek juga, bukan cuma state in-memory: run bisa
        # dijalankan dari terminal atau Task Scheduler, di luar sepengetahuan
        # proses Flask ini. Tanpa cek ini tombol Generate tetap bisa menembak
        # run kedua di atas run manual yang sedang jalan -- persis tabrakan
        # 2026-08-01. Kalau _refresh_state bilang tidak running tapi kunci ada,
        # pemegangnya pasti proses lain.
        active = runlock.read_lock()
        if active is not None:
            return jsonify({
                "running": True, "already": True, "external": True, "mode": None,
                "message": f"Run lain sedang jalan di luar dashboard: {active.get('script')} "
                           f"(pid {active.get('pid')}, mulai {active.get('started_at')})",
            }), 409
        _refresh_state.update(
            running=True, mode=mode, sector=sector, started_at=time.time(), finished_at=None, ok=None, message=None
        )
    threading.Thread(target=_run_refresh, args=(mode, sector), daemon=True).start()
    return jsonify({"started": True, "mode": mode, "sector": sector})


@app.get("/api/refresh/status")
def refresh_status():
    """Status gabungan: state in-memory + kunci di disk.

    State in-memory saja tidak cukup dan pernah berbohong: 2026-08-01 Flask
    restart di tengah pekerjaan, ingatannya hilang, dan endpoint ini melaporkan
    running=false padahal masih ada .tmp separuh tertulis. Kunci di disk
    bertahan melintasi restart, jadi dia yang jadi sumber kebenaran untuk
    "ada yang sedang jalan atau tidak"."""
    with _refresh_lock:
        state = dict(_refresh_state)

    active = runlock.read_lock()
    if active is not None and not state["running"]:
        # Ada run yang jalan, tapi bukan yang dispawn dashboard ini.
        state.update(
            running=True, external=True, mode=None,
            started_at=active.get("started_epoch"),
            message=f"{active.get('script')} (pid {active.get('pid')})",
        )
    else:
        state["external"] = False
    return jsonify(state)


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


@app.get("/api/historical/summary")
def get_historical_summary():
    """Versi ringan historical_timeline.json untuk HistoricalView (audit
    2026-07-30, item C6) -- file penuh sekarang ~469MB dan bertambah ~82MB
    per run (satu snapshot AggregatorOutput UTUH per ticker per hari, sejak
    v2.0 -- lihat historical.py docstring), jauh lebih besar dari yang view
    itu sebenarnya pakai (5 skalar per ticker). Sebelumnya HistoricalView
    memanggil GET /api/historical mentah: seluruh 469MB dimateralisasi jadi
    string lalu di-gzip SEKALIGUS single-threaded sambil memegang GIL
    (_compress_response) di setiap klik nav -- selama itu request lain,
    termasuk /api/refresh/status yang di-poll tiap 2.5 detik, ikut menggantung.

    Endpoint ini strip `entries[].aggregator_output` (payload besarnya) dan
    cuma kirim yang dipakai HistoricalView.jsx: ticker, total_entries,
    snapshot terakhir, plus dua boolean turunan (halted di entry terakhir,
    ada/tidaknya entry dengan outcome terisi) yang sebelumnya dihitung ulang
    di browser dari array `entries` penuh. Detail 1 ticker (utuh, termasuk
    entries) tetap lewat /api/ticker/<t>, yang cuma index 1 ticker.

    Catatan: ini tidak mengurangi memori yang ditahan _stage_cache (masih
    parse penuh sekali di sini) -- itu bagian C3 yang lebih struktural
    (pertumbuhan historical_timeline.json sendiri tidak dibatasi, dan
    _warm_cache memuat semua stage file penuh saat startup). Endpoint ini
    menghilangkan biaya kirim+gzip 469MB PER REQUEST, yang merupakan risiko
    paling akut (satu klik nav bisa membuat request lain menggantung)."""
    timelines = _get_stage("historical")
    rows = []
    for ticker, t in timelines.items():
        entries = t.get("entries") or []
        last = entries[-1] if entries else None
        rows.append({
            "ticker": ticker,
            "total_entries": t.get("total_entries", 0),
            "last_entry_date": t.get("last_entry_date"),
            "last_halted": (last.get("aggregator_output") or {}).get("halted") if last else None,
            "has_outcome": any(e.get("outcome") is not None for e in entries),
        })
    return jsonify({"tickers": rows, "total": len(rows)})


# Semua stage file yang dijaga gerbang all-or-nothing DAN punya wrapper
# level-atas tempat "session_id" bisa disisipkan tanpa mencemari struktur
# datanya (audit item C2/C9) -- historical_timeline.json sengaja dikecualikan
# (bentuknya {ticker: {...}} tanpa wrapper).
#
# institutional_flow ikut di sini walau bisa juga dibangun di luar pipeline
# lewat scripts/build_institutional_flow.py: script itu MENYALIN session_id
# dari evidence.json yang dibacanya, jadi file hasilnya tetap mengaku berasal
# dari run yang sama dengan datanya -- kalau ia mengarang session_id sendiri,
# pemeriksaan ini akan melapor "tercampur" justru saat datanya konsisten.
CONSISTENCY_CHECKED_STAGES = [
    "screening", "evidence", "knowledge", "catalyst", "institutional_flow",
    "peer", "confidence", "risk", "reasoning", "aggregator",
]


@app.get("/api/consistency")
def get_consistency():
    """Deteksi (BUKAN cegah -- lihat audit item C2/C9) dashboard/data/ yang
    mencampur dua run pipeline berbeda. refresh_full_pipeline.py menulis 10+
    file stage atomik SATU-SATU di gerbang "every stage succeeded", bukan
    sebagai satu transaksi lintas file -- kill eksternal DI TENGAH blok tulis
    itu (proses dibunuh paksa, disk penuh, dst) bisa menyisakan sebagian file
    dari run baru (session_id baru) berdampingan dengan sebagian file yang
    belum sempat ditimpa (session_id run sebelumnya).

    Setiap file di CONSISTENCY_CHECKED_STAGES menerima "session_id" yang
    SAMA PERSIS dari satu variabel kanonik saat run itu berhasil sampai ke
    gerbang tulis -- jadi kalau session_id-nya tidak seragam di semua file,
    itu berarti gerbang terakhir yang benar-benar tuntas TIDAK mencakup
    semuanya. File lama (ditulis sebelum perbaikan ini, tidak punya
    "session_id" sama sekali) dilaporkan sebagai None, bukan dianggap error --
    supaya endpoint ini tidak langsung berteriak "tidak konsisten" cuma
    karena belum semua file pernah ditulis ulang sejak perbaikan ini
    di-deploy."""
    session_ids: dict[str, str | None] = {}
    for name in CONSISTENCY_CHECKED_STAGES:
        session_ids[name] = _get_stage(name).get("session_id")

    seen = {sid for sid in session_ids.values() if sid is not None}
    consistent = len(seen) <= 1

    return jsonify({
        "consistent": consistent,
        "session_ids": session_ids,
        "distinct_session_ids": sorted(seen),
    })


@app.get("/api/capabilities")
def get_capabilities():
    """Dibaca frontend sekali di startup untuk tahu apakah grup nav
    "Pribadi" perlu ditampilkan (§8/§9 draft personal layer) -- rilis publik
    (folder alphaforge/personal/ dihapus) otomatis membuat ini False, tanpa
    perlu env var atau config terpisah."""
    return jsonify({"personal_enabled": PERSONAL_ENABLED})


if PERSONAL_ENABLED:
    register_personal_routes(app, DATA_DIR)


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


def _warm_cache() -> None:
    """Pre-load every stage file into _stage_cache BEFORE app.run() starts
    accepting connections, instead of leaving the cold-load cost
    (evidence.json alone is 337MB at full-market scale) to whichever
    request happens to arrive first -- that request was the dashboard's
    ticker-detail modal hitting a 7s+ "Memuat detail..." stall after every
    restart (dashboard perf notes 2026-07-26).

    Deliberately SYNCHRONOUS (called before app.run(), not spun off in a
    background thread) -- a background-thread version was tried first and
    made things WORSE: CPython's json decoder holds the GIL for the whole
    parse of a single json.load() call, so a warm-up thread parsing
    evidence.json would starve the request-handling thread of GIL time for
    the entire parse. Measured live: a ticker-detail request that used to
    take ~7s cold took 67.5s instead, blocked behind the background warm-up
    thread's own evidence.json parse (and the two were redundantly
    double-parsing the same file, since nothing was serializing access to
    _stage_cache). Blocking startup on this instead means one predictable
    ~60-90s delay before the server is reachable at all, in exchange for
    zero chance of a request racing the warm-up thread for the GIL.
    Best-effort: one broken/missing stage file logs and moves on rather
    than taking down the whole warm-up pass."""
    t0 = time.time()
    for name in STAGE_FILES:
        try:
            _get_stage(name)
        except Exception as exc:  # noqa: BLE001
            print(f"[warm_cache] gagal load stage '{name}': {exc}", file=sys.stderr)
    try:
        _get_price_target_store()
    except Exception as exc:  # noqa: BLE001
        print(f"[warm_cache] gagal load price_target_store: {exc}", file=sys.stderr)
    print(f"[warm_cache] selesai dalam {time.time() - t0:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    # Audit item C7: dashboard ini diasumsikan local-only di seluruh
    # codebase (lihat personal_routes.py -- holdings.json portofolio riil
    # lewat sini) dan tidak punya autentikasi sama sekali, tapi sebelumnya
    # bind ke 0.0.0.0 -- siapa pun di jaringan yang sama bisa memicu
    # pipeline 6 jam lewat POST /api/refresh/<mode> atau membaca
    # /api/refresh/status tanpa kredensial apa pun. render.yaml (deploy ke
    # Render.com sebagai web service publik) sudah tidak dipakai lagi
    # (dikonfirmasi pengguna) -- 127.0.0.1 di sini benar-benar merealisasikan
    # asumsi "local-only" yang sudah dinyatakan di tempat lain, bukan
    # keputusan baru.
    port = int(os.environ.get("PORT", 5000))
    _warm_cache()
    app.run(host="127.0.0.1", port=port)
