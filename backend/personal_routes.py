"""Personal layer routes -- /api/personal/*.

Terpisah dari app.py (bukan Flask Blueprint, karena app.py sendiri tidak
memakai pola Blueprint di mana pun -- lihat _get_stage/@app.get langsung di
sana; register() di sini cuma menambahkan @app.get ke instance `app` yang
sama dengan cara yang identik). Dipanggil app.py lewat try/except import
supaya publish tanpa folder montrva/personal/ ATAU file ini tidak
mematikan sisa dashboard (lihat app.py).

Semua endpoint di sini WAJIB tetap local-only (tidak diekspos ke internet) --
holdings.json (portofolio & harga beli riil) mengalir lewat sini. Ini
asumsi yang sudah berlaku untuk seluruh dashboard sekarang (jalan di
localhost:5000), ditegaskan lagi di sini karena taruhannya naik.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from flask import Response, jsonify, request

from montrva.personal import due_for_review
from montrva.personal.personal_contracts import ACTION_ALIASES

_stage_cache: dict[str, tuple[float, dict]] = {}
_derived_cache: dict[str, tuple[float, str, bytes]] = {}

# Field yang dipakai comparePersonalPicks di frontend/src/format.js untuk
# meranking kandidat top-3 (skor tesis -> risk flag tinggi -> risk flag sedang
# -> kelengkapan data -> ticker). Diproyeksikan apa adanya: PERINGKATNYA tetap
# dihitung di frontend, karena aturan seri itu sudah pernah bikin dua halaman
# menyebut top-3 yang berbeda untuk hari yang sama (lihat komentar panjang di
# atas comparePersonalPicks). Yang dipindah ke sini cuma penyaringan kasarnya.
_RANK_FIELDS = ("thesis_score", "source_confidence", "risk_flags_high", "risk_flags_medium")

# Batas ticker per permintaan /history/tickers. Ada supaya endpoint ini tidak
# bisa dipakai menarik kembali seluruh 160 MB satu permintaan -- itu justru
# yang sedang dihilangkan.
_MAX_TICKERS_PER_REQUEST = 250


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    mtime = path.stat().st_mtime
    key = str(path)
    cached = _stage_cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _stage_cache[key] = (mtime, data)
    return data


def _index_by_ticker(items: list[dict]) -> dict[str, dict]:
    return {item["ticker"]: item for item in items if "ticker" in item}


def _derived_json(path: Path, key: str, build) -> Response:
    """Hasil turunan dari sebuah berkas, di-cache dengan kunci mtime yang sama
    dengan _load_json. Proyeksi di bawah memindai ~113 ribu call tiap kali
    dibangun; tanpa cache ini, tiap muat halaman membayarnya lagi.

    Yang di-cache SUDAH BERUPA TEKS (dan versi gzip-nya), bukan dict: jsonify
    menyerialkan ulang tiap permintaan, dan untuk proyeksi kandidat (2,5 MB,
    17 ribu baris) itu terukur 4,1 detik per muat halaman — lebih mahal daripada
    membangunnya.

    Gzip-nya bukan hiasan: throughput ke browser di mesin ini terukur cuma
    ~0,5 MB/detik (2,56 MB butuh ~5 detik, 108 MB butuh 163 detik — laju yang
    sama), jadi jumlah BYTE-lah yang menentukan, bukan jumlah baris. Terkompresi
    payload ini tinggal ~sepersepuluh. Di-cache ikut mtime supaya kompresinya
    dibayar sekali, bukan tiap permintaan.
    """
    mtime = path.stat().st_mtime if path.exists() else 0.0
    cache_key = f"{path}#{key}"
    cached = _derived_cache.get(cache_key)
    if cached is None or cached[0] != mtime:
        payload = json.dumps(build(_load_json(path)), separators=(",", ":"))
        cached = (mtime, payload, gzip.compress(payload.encode("utf-8"), 6))
        _derived_cache[cache_key] = cached
    if "gzip" in (request.headers.get("Accept-Encoding") or ""):
        resp = Response(cached[2], mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
        return resp
    return Response(cached[1], mimetype="application/json")


def _is_best_action(module: str, action: str | None) -> bool:
    """Kembar dengan isBestAction() di frontend/src/format.js -- keduanya
    bersandar pada daftar alias yang sama supaya penggantian nama action
    (masuk_spekulatif -> siaga_gerakan) tidak membuat call lama tak terbaca."""
    return bool(action) and action in ACTION_ALIASES.get(module, frozenset())


def _build_candidates(history: dict) -> dict:
    """Semua call yang BERHAK ikut perebutan top-3, tanpa isi timeline-nya.

    Halaman Riwayat Pribadi butuh tahu ticker mana yang pernah masuk 3 besar
    (hari mana pun, lensa mana pun) -- pertanyaan yang jawabannya menuntut
    SELURUH populasi, dan selama ini dijawab dengan mengirim personal_history.json
    utuh (160 MB) ke browser. Proyeksi ini membawa persis yang dibutuhkan
    perankingan dan tidak lebih: 17 ribu baris, ~2,4 MB.
    """
    timelines = history.get("timelines", history)
    out = []
    for ticker, timeline in timelines.items():
        if not isinstance(timeline, dict):
            continue
        for entry in timeline.get("entries", []):
            day = (entry.get("analyzed_at") or "")[:10]
            if not day:
                continue
            call_set = entry.get("personal_call_set") or {}
            for module in ACTION_ALIASES:
                call = call_set.get(module) or {}
                if call.get("position_status") != "no_holding":
                    continue
                if not _is_best_action(module, call.get("action")):
                    continue
                row = {"ticker": ticker, "day": day, "module": module}
                row.update({f: call.get(f) for f in _RANK_FIELDS})
                out.append(row)
    return {"candidates": out}


def _build_previous_picks(history: dict) -> dict:
    """Ticker yang jadi kandidat top pick di snapshot SEBELUM yang terakhir,
    per lensa -- dasar badge "BARU" di Agregator Pribadi.

    Aturannya sama persis dengan previousTopPickTickers() yang dulu menghitung
    ini di browser: entry KEDUA DARI BELAKANG milik tiap ticker (bukan tanggal
    run sebelumnya secara global), disaring position_status=no_holding +
    action terkuat lensa itu. Dipindah ke sini karena satu-satunya alasan
    halaman Agregator mengunduh riwayat 160 MB adalah badge ini.
    """
    timelines = history.get("timelines", history)
    picks: dict[str, set[str]] = {m: set() for m in ACTION_ALIASES}
    as_of = None
    for ticker, timeline in timelines.items():
        if not isinstance(timeline, dict):
            continue
        entries = timeline.get("entries", [])
        if len(entries) < 2:
            continue
        prev = entries[-2]
        for module in ACTION_ALIASES:
            call = (prev.get("personal_call_set") or {}).get(module) or {}
            if call.get("position_status") == "no_holding" and _is_best_action(module, call.get("action")):
                picks[module].add(ticker)
        prev_at = prev.get("analyzed_at") or ""
        if not as_of or prev_at > as_of:
            as_of = prev_at
    return {"as_of": as_of, "tickers": {m: sorted(v) for m, v in picks.items()}}


def register(app, data_dir: Path) -> None:
    personal_dir = data_dir / "personal"

    @app.get("/api/personal/calls")
    def get_personal_calls():
        return jsonify(_load_json(personal_dir / "personal_calls.json"))

    @app.get("/api/personal/ticker/<ticker>")
    def get_personal_ticker(ticker: str):
        ticker = ticker.upper()
        calls = _index_by_ticker(_load_json(personal_dir / "personal_calls.json").get("call_sets", []))
        history = _load_json(personal_dir / "personal_history.json")
        return jsonify({
            "ticker": ticker,
            "call_set": calls.get(ticker),
            "history": history.get(ticker),
        })

    @app.get("/api/personal/action-table")
    def get_personal_action_table():
        """ACTION_TABLE + ambang tier, disajikan LANGSUNG dari Python.

        Panel "Jalur Keputusan" (TickerModal) menyorot sel yang menyala di
        dalam grid aslinya, jadi ia butuh seluruh tabelnya -- bukan cuma
        action yang sudah jadi. Tabelnya TIDAK diketik ulang di JS dengan
        sengaja: gerbang tier yang sama sudah tersalin di tiga tempat
        (personal_reasoning._thesis_score_tier, personal_calibration._score_tier,
        ThesisProof.scoreTierKey) dan komentar di sana sendiri mencatat "tidak
        ada yang menghubungkan mereka secara struktural". Menyalin 72 sel lagi
        akan melipatgandakan kelas kesalahan itu -- kalau tabelnya berubah, UI
        akan mengajarkan aturan yang sudah tidak dipakai tanpa satu pun test
        yang gagal.
        """
        from montrva.personal.personal_reasoning import ACTION_TABLE, SCORE_TIER_BOUNDS

        return jsonify({
            "action_table": ACTION_TABLE,
            "score_tier_bounds": SCORE_TIER_BOUNDS,
        })

    @app.get("/api/personal/history")
    def get_personal_history():
        """Riwayat UTUH -- 160 MB. Sengaja dipertahankan (alat bantu & skrip
        masih memakainya), tapi TIDAK ada halaman dashboard yang memanggilnya
        lagi: Riwayat Pribadi memakai /history/candidates + /history/tickers,
        Agregator memakai /history/previous-picks. Sebelum pemisahan itu,
        halaman Riwayat butuh ~1 menit hanya untuk mengunduh berkas ini."""
        return jsonify(_load_json(personal_dir / "personal_history.json"))

    @app.get("/api/personal/history/candidates")
    def get_personal_history_candidates():
        """Proyeksi tipis untuk menentukan siapa yang pernah masuk top-3 --
        lihat _build_candidates. Perankingannya tetap di frontend."""
        return _derived_json(
            personal_dir / "personal_history.json", "candidates", _build_candidates,
        )

    @app.get("/api/personal/history/tickers")
    def get_personal_history_tickers():
        """Timeline penuh untuk beberapa ticker sekaligus (?tickers=A,B,C).

        Batch, bukan satu permintaan per ticker: halaman Riwayat membuka 35
        timeline sekaligus, dan 35 permintaan berurutan menukar satu masalah
        (berkas raksasa) dengan masalah lain (antrean permintaan)."""
        raw = request.args.get("tickers", "")
        wanted = [t.strip().upper() for t in raw.split(",") if t.strip()]
        if not wanted:
            return jsonify({"timelines": {}})
        if len(wanted) > _MAX_TICKERS_PER_REQUEST:
            return jsonify({
                "error": f"maksimal {_MAX_TICKERS_PER_REQUEST} ticker per permintaan",
                "requested": len(wanted),
            }), 400
        history = _load_json(personal_dir / "personal_history.json")
        timelines = history.get("timelines", history)
        return jsonify({"timelines": {t: timelines[t] for t in wanted if t in timelines}})

    @app.get("/api/personal/history/previous-picks")
    def get_personal_history_previous_picks():
        """Kandidat top pick di snapshot sebelumnya, per lensa (badge "BARU")."""
        return _derived_json(
            personal_dir / "personal_history.json", "previous_picks", _build_previous_picks,
        )

    @app.get("/api/personal/calibration")
    def get_personal_calibration():
        """Rapor kalibrasi — sudah teragregasi jadi beberapa KB oleh
        personal_calibration.py, jadi endpoint ini sengaja TIDAK menghitung
        apa pun dari personal_history.json (127 MB; /api/personal/history
        mengirimkannya utuh dan halaman itu butuh ~1 menit memuat)."""
        return jsonify(_load_json(personal_dir / "calibration.json"))

    @app.get("/api/personal/due-for-review")
    def get_personal_due_for_review():
        """Ticker mana yang punya minimal satu snapshot historis layak
        ditinjau ulang (§12: umur snapshot vs horizon-nya, BUKAN vonis
        tepat/meleset -- dihitung saat dibaca, tidak pernah disimpan)."""
        history = _load_json(personal_dir / "personal_history.json")
        due = []
        for ticker, timeline in history.items():
            entries = timeline.get("entries", [])
            if entries and due_for_review(entries[-1]):
                due.append(ticker)
        return jsonify({"due_for_review": sorted(due)})
