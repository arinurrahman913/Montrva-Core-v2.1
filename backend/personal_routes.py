"""Personal layer routes -- /api/personal/*.

Terpisah dari app.py (bukan Flask Blueprint, karena app.py sendiri tidak
memakai pola Blueprint di mana pun -- lihat _get_stage/@app.get langsung di
sana; register() di sini cuma menambahkan @app.get ke instance `app` yang
sama dengan cara yang identik). Dipanggil app.py lewat try/except import
supaya publish tanpa folder alphaforge/personal/ ATAU file ini tidak
mematikan sisa dashboard (lihat app.py).

Semua endpoint di sini WAJIB tetap local-only (tidak diekspos ke internet) --
holdings.json (portofolio & harga beli riil) mengalir lewat sini. Ini
asumsi yang sudah berlaku untuk seluruh dashboard sekarang (jalan di
localhost:5000), ditegaskan lagi di sini karena taruhannya naik.
"""
from __future__ import annotations

import json
from pathlib import Path

from flask import jsonify

from alphaforge.personal import due_for_review

_stage_cache: dict[str, tuple[float, dict]] = {}


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

    @app.get("/api/personal/history")
    def get_personal_history():
        return jsonify(_load_json(personal_dir / "personal_history.json"))

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
