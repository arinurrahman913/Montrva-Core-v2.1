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
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Response, jsonify, request

from backend import big_json
from montrva.layer2 import rehydrate
from montrva.layer2.sources.live_quote import fetch_live_quote
from montrva.personal import build_personal_call_set, due_for_review
from montrva.personal import portfolio as pf
from montrva.personal.personal_contracts import ACTION_ALIASES

# path -> (mtime, isi, kedaluwarsa). Kedaluwarsa None = ditahan permanen
# (berkas kecil); berisi angka = dilepas sesudah TTL (lihat _LARGE_FILE_BYTES).
_stage_cache: dict[str, tuple[float, dict, float | None]] = {}
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


# Ambang "berkas besar" dan berapa lama isinya boleh ditahan di memori sesudah
# terakhir dipakai.
#
# personal_history.json tumbuh jadi 267 MB (riwayat 4.441 ticker, run 15 Agu).
# `_load_json` menahannya sebagai dict Python selamanya — terukur +461 MB RAM
# backend sekali panggil, di mesin dengan 2,7 GB bebas dan Chrome jalan. Backend
# mati dua kali pada 15 Agu, log berhenti mendadak TANPA traceback (tanda
# proses dibunuh, bukan exception), dan halaman Riwayat Pribadi menampilkan
# "TypeError: Failed to fetch" — yang artinya servernya hilang, bukan permintaan
# yang ditolak.
#
# Kenapa TTL, bukan sekadar "jangan di-cache": satu kali muat halaman Riwayat
# memanggil /candidates lalu /tickers dalam hitungan detik, dan yang kedua butuh
# akses acak ke dict penuh. Tanpa penahanan sama sekali, tiap muat halaman
# membayar parse 267 MB (terukur 18,3 detik) DUA kali. Ditahan sebentar lalu
# dilepas: letupannya sekali per kunjungan, bukan permanen.
#
# Berkas kecil (personal_calls.json 13 MB, calibration 30 KB) tetap ditahan
# permanen seperti sebelumnya — bukan itu yang membunuh backend.
_LARGE_FILE_BYTES = 50 * 1024 * 1024
_LARGE_FILE_TTL_SECONDS = 120


def _sweep_expired(now: float) -> None:
    for key, entry in list(_stage_cache.items()):
        if len(entry) == 3 and entry[2] is not None and entry[2] < now:
            del _stage_cache[key]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    now = time.time()
    # Pelepasan terjadi di sini, saat ADA permintaan lain masuk — bukan lewat
    # timer latar. Cukup karena tiap halaman pribadi memanggil endpoint ini
    # lagi; yang penting memorinya tidak ditahan sampai proses mati.
    _sweep_expired(now)

    mtime = path.stat().st_mtime
    key = str(path)
    cached = _stage_cache.get(key)
    if cached is not None and cached[0] == mtime:
        if len(cached) == 3 and cached[2] is not None:
            # Diakses lagi -> perpanjang, supaya burst permintaan dalam satu
            # kunjungan halaman tidak kehilangan cache di tengah jalan.
            _stage_cache[key] = (cached[0], cached[1], now + _LARGE_FILE_TTL_SECONDS)
        return cached[1]

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    expires = now + _LARGE_FILE_TTL_SECONDS if path.stat().st_size >= _LARGE_FILE_BYTES else None
    _stage_cache[key] = (mtime, data, expires)
    return data


def _index_by_ticker(items: list[dict]) -> dict[str, dict]:
    return {item["ticker"]: item for item in items if "ticker" in item}


# Indeks offset personal_history.json, dipegang per-mtime. Bentuknya identik
# dengan historical_timeline.json (objek berkunci ticker, tiap nilai memuat
# `total_entries` sekali), jadi builder yang sama dipakai -- bukan builder
# kedua yang menebak format yang sama.
_history_index: dict[str, tuple[float, object]] = {}


def _history_record(path: Path, ticker: str) -> dict | None:
    """Satu ticker dari personal_history.json TANPA mem-parse seluruhnya.

    Kenapa ini ada: berkasnya 291 MB dan `json.load`-nya terukur 10,7 detik
    (18,3 detik saat memori sesak) sambil memegang GIL, jadi SEMUA permintaan
    lain ikut tertahan selama itu -- termasuk /api/ticker/<t> yang tidak ada
    hubungannya. Gejalanya di browser: modal berhenti di "Memuat detail…"
    sampai 25 detik, muncul-hilang tanpa pola karena cuma terjadi ketika TTL
    120 detik kebetulan sudah lewat. Terukur sesudah indeks: 5 ms per ticker.

    Jatuh ke `_load_json` kalau indeksnya gagal dibangun (bentuk berkas
    berubah) -- lambat, tidak pernah salah data: `build_historical_index`
    memvalidasi hasilnya dan mengembalikan None kalau penandanya meleset.
    """
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    key = str(path)
    cached = _history_index.get(key)
    if cached is None or cached[0] != mtime:
        cached = (mtime, big_json.build_historical_index(path))
        _history_index[key] = cached
    index = cached[1]
    if index is None:
        history = _load_json(path)
        return (history.get("timelines") or history).get(ticker)
    return index.get(ticker)


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


# Kutipan live untuk seluruh portofolio sekaligus. fetch_live_quote sendiri
# sudah punya timeout 8 detik + cache 30 detik per ticker, tapi ia satu-ticker
# per panggilan: 20 posisi secara berurutan bisa jadi 160 detik di kasus
# terburuk. Pool kecil di sini membuat batas atasnya tetap ~8 detik apa pun
# jumlah posisinya. Ukurannya sengaja kecil — ini permintaan web, bukan batch.
_QUOTE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="portfolio-quote")


def _live_prices(tickers: list[str]) -> dict[str, float | None]:
    if not tickers:
        return {}
    quotes = _QUOTE_POOL.map(fetch_live_quote, tickers)
    return {t: (q.get("last_price") if not q.get("stale") else None) for t, q in zip(tickers, quotes)}


def _usd_idr() -> dict:
    """Kurs USD/IDR untuk TAMPILAN saja.

    Yang dikirim cuma kursnya, bukan nilai yang sudah dikonversi: dolar tetap
    mata uang pencatatan (brokernya USD), dan menyimpan angka rupiah berarti
    membekukan kurs satu titik waktu ke dalam catatan transaksi — besok angka
    itu salah tanpa ada yang tahu.

    Lewat fetch_live_quote yang sama dengan harga saham (timeout 8 detik +
    cache 30 detik sudah di dalamnya). Kalau gagal, `rate` None dan frontend
    tidak menampilkan rupiah sama sekali — bukan memakai kurs tebakan."""
    q = fetch_live_quote("USDIDR=X")
    rate = q.get("last_price") if not q.get("stale") else None
    return {
        "pair": "USDIDR=X",
        "rate": rate,
        "fetched_at": q.get("fetched_at"),
        "error": q.get("error"),
    }


def _build_portfolio(personal_dir: Path, data_dir: Path, get_stage) -> dict:
    """Isi halaman Portofolio: posisi + harga + ringkasan + call yang DIHITUNG
    ULANG untuk ticker yang dipegang.

    Perhitungan ulang itu inti Poin 3. `personal_calls.json` lahir sekali per
    run penuh (~3,5 jam), jadi posisi yang dicatat hari ini akan terbaca
    `no_holding` di seluruh dashboard sampai run berikutnya — dan kolom
    `holding` di ACTION_TABLE (36 sel) tidak pernah menyala. Di sini
    `build_personal_call_set()` YANG SAMA dipanggil ulang dengan holdings
    terbaru; bahannya (reasoning/catalyst/risk) semuanya sudah ada di disk.
    Yang TIDAK dilakukan: menulis ulang personal_calls.json di luar pipeline
    (merusak keseragaman session_id yang dijaga /api/consistency), dan
    menyalin ACTION_TABLE ke JS.
    """
    book = pf.ensure_book(personal_dir)
    positions = pf.derive_positions(book)
    open_tickers = [p["ticker"] for p in positions if p["is_open"]]

    prices = _live_prices(open_tickers)
    calls_file = _load_json(personal_dir / "personal_calls.json")
    snapshot_calls = _index_by_ticker(calls_file.get("call_sets", []))

    # Cadangan harga: harga yang dipakai run terakhir. Bukan pengganti kutipan
    # live (bisa berumur sehari), tapi jauh lebih baik daripada None — posisi
    # tanpa harga hilang dari nilai pasar & bobot sama sekali.
    price_source: dict[str, str | None] = {}
    for ticker in open_tickers:
        if prices.get(ticker) is not None:
            price_source[ticker] = "live"
            continue
        fallback = ((snapshot_calls.get(ticker) or {}).get("multibagger") or {}).get("price_at_call")
        prices[ticker] = fallback
        price_source[ticker] = "snapshot" if fallback is not None else None

    weights = pf.position_weights(positions, prices)
    summary = pf.summarize(positions, prices)

    holdings = {p["ticker"]: p for p in positions if p["is_open"]}
    reasoning = _index_by_ticker(get_stage("reasoning").get("reasoning_outputs", []))
    risk = _index_by_ticker(get_stage("risk").get("assessments", []))
    catalyst = _index_by_ticker(get_stage("catalyst").get("catalyst_sets", []))
    # Harga benchmark run terakhir — sama untuk semua ticker dalam satu run,
    # jadi diambil dari call mana pun yang punya (lihat build_personal_call_sets).
    benchmark = next(
        (c["multibagger"]["benchmark_at_call"] for c in snapshot_calls.values()
         if (c.get("multibagger") or {}).get("benchmark_at_call") is not None),
        None,
    )

    rows = []
    for pos in positions:
        ticker = pos["ticker"]
        row = dict(pos)
        price = prices.get(ticker)
        row["price"] = price
        row["price_source"] = price_source.get(ticker)
        row["weight_pct"] = weights.get(ticker)
        row["in_universe"] = ticker in reasoning
        row["market_value"] = price * pos["quantity"] if (price is not None and pos["is_open"]) else None
        row["unrealized_pnl"] = (row["market_value"] - pos["total_cost"]) if row["market_value"] is not None else None
        row["unrealized_pct"] = (
            row["unrealized_pnl"] / pos["total_cost"] * 100.0
            if row["unrealized_pnl"] is not None and pos["total_cost"] else None
        )
        row["call_set"] = None
        # Sudahkah run terakhir tahu kita memegang ini? Kalau snapshot-nya masih
        # `no_holding`, halaman Agregator Pribadi memang akan menyebut hal lain
        # sampai run berikutnya — itu dilaporkan, bukan disembunyikan.
        snapshot_status = ((snapshot_calls.get(ticker) or {}).get("multibagger") or {}).get("position_status")
        row["pending_next_run"] = bool(pos["is_open"]) and snapshot_status != "holding"

        bundle_dict = reasoning.get(ticker)
        if pos["is_open"] and bundle_dict:
            try:
                call_set = build_personal_call_set(
                    rehydrate.reasoning_bundle(bundle_dict),
                    holdings,
                    catalyst=rehydrate.catalyst_set(catalyst[ticker]) if ticker in catalyst else None,
                    risk=rehydrate.risk_assessment(risk[ticker]) if ticker in risk else None,
                    current_price=price,
                    benchmark_price=benchmark,
                )
                row["call_set"] = call_set.to_dict()
            except (TypeError, KeyError, ValueError) as exc:
                # Satu ticker yang kontraknya tidak cocok (berkas dari versi
                # lama) tidak boleh mengosongkan seluruh halaman.
                row["call_error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)

    return {
        "positions": rows,
        "summary": summary,
        "transactions": pf.sort_transactions(book)[::-1],  # terbaru dulu, untuk tabel buku
        "cost_basis_method": "average_cost",
        "fx": _usd_idr(),
        "pending_next_run": sorted(r["ticker"] for r in rows if r["pending_next_run"]),
        "snapshot_session_id": calls_file.get("session_id"),
        "snapshot_generated_at": calls_file.get("generated_at"),
    }


def register(app, data_dir: Path, get_stage=None) -> None:
    personal_dir = data_dir / "personal"

    # Berbagi cache stage milik app.py kalau diberikan. Penting untuk memori,
    # bukan kerapian: reasoning_outputs.json 25 MB + risk 7 MB + catalysts 2 MB
    # sudah dipegang _stage_cache app.py begitu halaman Reasoning/Risk/Catalyst
    # atau satu detail ticker dibuka. Membacanya lewat _load_json di modul ini
    # akan menyimpan SALINAN KEDUA — persis kelas pemborosan yang dikejar
    # habis-habisan waktu backend diturunkan dari 3,32 GB ke 0,59 GB.
    if get_stage is None:
        _STAGE_FILES = {"reasoning": "reasoning_outputs.json", "risk": "risk_assessments.json",
                        "catalyst": "catalysts.json"}

        def get_stage(name):  # noqa: ANN001
            return _load_json(data_dir / _STAGE_FILES[name])

    @app.get("/api/personal/calls")
    def get_personal_calls():
        return jsonify(_load_json(personal_dir / "personal_calls.json"))

    @app.get("/api/personal/ticker/<ticker>")
    def get_personal_ticker(ticker: str):
        ticker = ticker.upper()
        calls = _index_by_ticker(_load_json(personal_dir / "personal_calls.json").get("call_sets", []))
        return jsonify({
            "ticker": ticker,
            "call_set": calls.get(ticker),
            "history": _history_record(personal_dir / "personal_history.json", ticker),
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
        from montrva.personal.personal_reasoning import (
            ACTION_TABLE, SCORE_TIER_BOUNDS, _thesis_score_tier,
        )

        # Berapa ticker BENAR-BENAR mendarat di tiap sel pada run terakhir.
        # Dihitung DI SINI, bukan di JS: memetakan skor ke tingkat butuh
        # SCORE_TIER_BOUNDS, dan menyalinnya ke frontend akan menambah tempat
        # KEEMPAT untuk gerbang yang sama (docstring di atas sudah mencatat
        # tiga yang ada dan bahwa tidak ada yang menghubungkannya).
        #
        # Gunanya bukan statistik: sel yang aturannya ada tapi TIDAK PERNAH
        # menyala cuma bisa terlihat kalau aturan dan kenyataan ditaruh
        # berdampingan. Begitulah dulu ketahuan bahwa kolom "high" mustahil
        # tercapai saat gerbangnya masih confidence.band, dan bahwa seluruh
        # blok "holding" menganggur sebelum ada portofolio.
        counts: dict = {}
        tier_hist: dict = {}
        for cs in _load_json(personal_dir / "personal_calls.json").get("call_sets", []):
            for module in ("multibagger", "quality_compound", "speculative"):
                call = cs.get(module)
                if not isinstance(call, dict):
                    continue
                status = call.get("position_status")
                stance = call.get("source_stance")
                tier = _thesis_score_tier(call.get("thesis_score") or 50.0)
                if not status or not stance:
                    continue
                # Ditulis bertahap, BUKAN satu ekspresi berantai: pada
                # `a[k] = b` Python mengevaluasi sisi KANAN lebih dulu, jadi
                # versi berantai membaca counts[status] sebelum setdefault di
                # sisi kiri sempat membuatnya -> KeyError.
                per_stance = counts.setdefault(status, {}).setdefault(module, {}).setdefault(stance, {})
                per_stance[tier] = per_stance.get(tier, 0) + 1
                per_module = tier_hist.setdefault(module, {})
                per_module[tier] = per_module.get(tier, 0) + 1

        return jsonify({
            "action_table": ACTION_TABLE,
            "score_tier_bounds": SCORE_TIER_BOUNDS,
            "cell_counts": counts,
            "tier_distribution": tier_hist,
            "session_id": _load_json(personal_dir / "personal_calls.json").get("session_id"),
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
        # Lewat indeks offset juga: 35 timeline = 35 seek+parse potongan
        # (terukur ~5 ms masing-masing), bukan satu parse 291 MB yang menahan
        # GIL belasan detik untuk seluruh backend.
        path = personal_dir / "personal_history.json"
        found = {t: rec for t in wanted if (rec := _history_record(path, t)) is not None}
        return jsonify({"timelines": found})

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

    # --- Portofolio (buku transaksi + posisi turunan) ---------------------
    #
    # SATU-SATUNYA jalur tulis di lapisan pribadi. Sama seperti seluruh
    # /api/personal/*, endpoint ini WAJIB tetap local-only: yang mengalir di
    # sini bukan lagi pendapat sistem, tapi portofolio dan harga beli riil.

    @app.get("/api/personal/portfolio")
    def get_personal_portfolio():
        return jsonify(_build_portfolio(personal_dir, data_dir, get_stage))

    @app.get("/api/personal/transactions")
    def get_personal_transactions():
        book = pf.ensure_book(personal_dir)
        return jsonify({
            "transactions": pf.sort_transactions(book)[::-1],
            "positions": pf.derive_positions(book),
        })

    @app.post("/api/personal/transactions")
    def post_personal_transaction():
        body = request.get_json(silent=True) or {}
        tx, errors = pf.add_transaction(
            personal_dir,
            ticker=str(body.get("ticker", "")),
            side=str(body.get("side", "")),
            tx_date=str(body.get("date", "")),
            price=body.get("price"),
            # Halaman mengirim nominal; `quantity` tetap diterima supaya alat
            # bantu/skrip lama tidak perlu ikut berubah.
            amount=body.get("amount"),
            quantity=body.get("quantity"),
            fee=body.get("fee") or 0.0,
            note=str(body.get("note") or ""),
        )
        if errors:
            # 422, bukan 400: bentuk permintaannya sah, isinya yang ditolak
            # aturan buku (mis. jual melebihi kepemilikan pada tanggal itu).
            return jsonify({"errors": errors}), 422
        return jsonify({"transaction": tx}), 201

    @app.delete("/api/personal/transactions/<tx_id>")
    def delete_personal_transaction(tx_id: str):
        removed, errors = pf.delete_transaction(personal_dir, tx_id)
        if errors:
            status = 404 if removed is None and "tidak ditemukan" in errors[0] else 422
            return jsonify({"errors": errors}), status
        return jsonify({"removed": removed})

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
