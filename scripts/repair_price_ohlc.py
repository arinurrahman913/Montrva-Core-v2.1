"""Isi ulang open/high/low/close/volume yang kosong di evidence.json + cache.

Yahoo menambahkan baris untuk sesi yang belum selesai (Volume terisi, OHLC
NaN). `fetch_price_market_data` dulu mengambil `.iloc[-1]` tanpa memeriksa,
jadi 4.044 dari 4.207 ticker (96,1%) punya OHLC null padahal `volume` di
sebelahnya terisi -- dari baris kosong yang sama.

Kodenya sudah diperbaiki (sources/yahoo_evidence.py), tapi data on-disk baru
ikut benar setelah run penuh berikutnya. Skrip ini memperbaikinya sekarang
TANPA fetch baru: nilainya diambil dari bar terakhir yang lengkap di
`price_history` ticker itu sendiri -- sumber yang sama, cuma barisnya yang
dipilih dengan benar.

Kelima angka diambil dari SATU bar supaya konsisten satu hari. Ticker yang
tidak punya satu pun bar lengkap dilewati, tidak diisi dengan tebakan.

Idempoten: hanya menyentuh entri yang open/close-nya masih null.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alphaforge.json_safe import dump_safe, dumps_safe  # noqa: E402

EVIDENCE = ROOT / "dashboard" / "data" / "evidence.json"
PRICE_CACHE = ROOT / ".cache" / "price_market_data"

FIELDS = ("open", "high", "low", "close", "volume")


def last_complete_bar(bars: list[dict] | None) -> dict | None:
    if not bars:
        return None
    for bar in reversed(bars):
        if not isinstance(bar, dict):
            continue
        if all(isinstance(bar.get(k), (int, float)) for k in ("open", "high", "low", "close")):
            return bar
    return None


def repair_mapping(pm: dict, bars: list[dict] | None) -> bool:
    """True kalau ada yang berubah (OHLC diisi, tanggal dilengkapi, atau bar
    kosong dibuang dari price_history)."""
    changed = False

    # Bar tanpa Close dibuang: ia mengalir ke chart, evaluasi outcome, dan
    # rekonstruksi harga entry -- sumber `null.toFixed()` yang mengosongkan
    # dashboard (commit 65dfc7a).
    if isinstance(bars, list):
        kept = [b for b in bars if isinstance(b, dict) and isinstance(b.get("close"), (int, float))]
        if len(kept) != len(bars):
            pm["price_history"] = kept
            bars = kept
            changed = True

    bar = last_complete_bar(bars)
    if bar is None:
        return changed

    if pm.get("open") is None or pm.get("close") is None:
        for k in FIELDS:
            v = bar.get(k)
            if v is not None:
                pm[k] = v
        changed = True
    # Tanggal sumber OHLC: tanpa ini, angka dari sesi lengkap terakhir tampil
    # bersebelahan dengan `last_price` hari ini seolah dari hari yang sama.
    if pm.get("ohlc_date") is None and bar.get("date"):
        pm["ohlc_date"] = bar["date"]
        changed = True
    return changed


def repair_cache() -> tuple[int, int]:
    fixed = skipped = 0
    if not PRICE_CACHE.exists():
        return 0, 0
    for p in PRICE_CACHE.glob("*.json"):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            skipped += 1
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            skipped += 1
            continue
        if repair_mapping(data, data.get("price_history")):
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(dumps_safe(payload), encoding="utf-8")
            tmp.replace(p)
            fixed += 1
    return fixed, skipped


def repair_evidence() -> tuple[int, int]:
    if not EVIDENCE.exists():
        return 0, 0
    doc = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    packages = doc.get("packages") or []
    fixed = untouched = 0
    for pkg in packages:
        pm = pkg.get("price_market")
        if not isinstance(pm, dict):
            continue
        if repair_mapping(pm, pm.get("price_history")):
            fixed += 1
        else:
            untouched += 1
    if fixed:
        # Ditulis STREAMING dengan ensure_ascii=False, meniru _atomic_write di
        # refresh_full_pipeline.py. Dua alasan, dua-duanya pernah menggigit:
        # (1) merakit string 250 MB lebih dulu pernah membunuh run 2026-07-30
        #     dengan MemoryError di titik tulis evidence.json;
        # (2) ensure_ascii default meng-escape karakter non-ASCII di deskripsi
        #     perusahaan jadi \uXXXX -- versi pertama skrip ini menggelembungkan
        #     berkas 232 MB jadi 250 MB tanpa menambah satu pun informasi.
        tmp = EVIDENCE.with_name(EVIDENCE.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            dump_safe(doc, f, indent=None, ensure_ascii=False)
        tmp.replace(EVIDENCE)
    return fixed, untouched


def main() -> int:
    c_fixed, c_skipped = repair_cache()
    print(f"cache price_market_data : {c_fixed} diperbaiki, {c_skipped} dilewati")
    e_fixed, e_untouched = repair_evidence()
    print(f"evidence.json           : {e_fixed} diperbaiki, {e_untouched} tidak perlu/tidak bisa")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
