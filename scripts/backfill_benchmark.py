"""Pulihkan pembanding indeks untuk outcome yang terlanjur dievaluasi tanpa
`excess_return_pct`.

Latar: `excess_return_pct` null di 167 dari 167 tesis berarah, karena dua
kegagalan yang menumpuk — `benchmark_at_call` tidak pernah terisi (spx_history
kosong saat call dibuat, tanpa satu baris log pun), DAN pemanggilan
`evaluate_due_entries()` di refresh_full_pipeline.py tidak pernah mengoper
`benchmark_price_now` sama sekali, jadi sisi keluarnya juga None. Keduanya
sudah diperbaiki; skrip ini mengurus yang terlanjur tercatat.

Kenapa ini BUKAN mengarang data: `entry_date` dan `exit_date` sudah tersimpan
di tiap outcome (sejak `5c8818a`/`1beac8a`), dan penutupan ^GSPC pada dua
tanggal itu adalah fakta publik yang tetap — bukan rekonstruksi dari keadaan
sekarang. Yang tidak punya salah satu tanggalnya DILEWATI, tidak ditebak.
Pelajaran yang sama dengan backfill harga (`1beac8a`): periksa dulu apakah
angka yang hilang bisa diturunkan dari yang tersimpan.

Aman dijalankan berulang (idempoten) dan tidak mengubah field lain sama
sekali — verifikasi ada di --verify.

Pakai:
    python scripts/backfill_benchmark.py --dry-run
    python scripts/backfill_benchmark.py
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from montrva.json_safe import dumps_safe, write_text_atomic  # noqa: E402
from montrva.layer1.benchmark_history import (  # noqa: E402
    load_benchmark_history, sync_benchmark_history,
)
from montrva.personal.personal_evaluation import resolve_benchmark_pair  # noqa: E402

DATA_DIR = ROOT / "dashboard" / "data"
HISTORY_PATH = DATA_DIR / "personal" / "personal_history.json"
BENCHMARK_PATH = DATA_DIR / "benchmark_history.json"
LAYER1_PATH = DATA_DIR / "layer1_context.json"


def _pct(a: float | None, b: float | None) -> float | None:
    if not a or b is None:
        return None
    return (b - a) / a * 100.0


def bootstrap_series() -> dict[str, float]:
    """Isi benchmark_history.json dari spx_history di layer1_context.json.

    Sekali jalan saja gunanya: sesudah ini pipeline yang menambah bar tiap
    run. spx_history cuma 90 bar terakhir, jadi tesis yang tanggal masuknya
    lebih tua dari itu memang tidak bisa dipulihkan dari sini — dan tidak
    dikarang; dilaporkan sebagai dilewati.
    """
    series = load_benchmark_history(BENCHMARK_PATH)
    if LAYER1_PATH.exists():
        layer1 = json.loads(LAYER1_PATH.read_text(encoding="utf-8"))
        series = sync_benchmark_history(layer1.get("spx_history"), BENCHMARK_PATH)
    return series


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="hitung saja, jangan tulis")
    args = ap.parse_args()

    series = bootstrap_series()
    if not series:
        print("Deret indeks kosong — tidak ada yang bisa dipulihkan.")
        return 1
    print(f"Deret indeks: {len(series)} bar ({min(series)} s/d {max(series)})")

    print(f"Membaca {HISTORY_PATH.name} ...")
    data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    timelines = data.get("timelines", data)

    stats = Counter()
    # Satu tesis = satu outcome yang disalin ke SEMUA entry dalam streak-nya
    # (lihat _find_streaks). Perhitungan di sini karena itu di-cache per
    # thesis_key: hasilnya wajib identik di tiap salinan, kalau tidak baris
    # Riwayat yang berbeda menampilkan excess yang berbeda untuk tesis sama.
    computed: dict[tuple[str, str, str], dict] = {}

    for ticker, tl in timelines.items():
        for entry in (tl.get("entries", []) if isinstance(tl, dict) else []):
            for module, rec in (entry.get("outcome") or {}).items():
                if not isinstance(rec, dict):
                    continue
                stats["outcome_total"] += 1
                if rec.get("excess_return_pct") is not None:
                    stats["sudah_ada"] += 1
                    continue
                key = (ticker, module, rec.get("thesis_key") or "")
                if key in computed:
                    patch = computed[key]
                else:
                    entry_date, exit_date = rec.get("entry_date"), rec.get("exit_date")
                    if not entry_date or not exit_date:
                        computed[key] = {}
                        stats["dilewati_tanggal_kosong"] += 1
                        continue
                    b_in, b_out, source = resolve_benchmark_pair(
                        series, None, entry_date, exit_date, None,
                    )
                    bench_return = _pct(b_in, b_out)
                    if bench_return is None:
                        computed[key] = {}
                        stats["dilewati_di_luar_deret"] += 1
                        continue
                    ret = rec.get("return_pct")
                    patch = {
                        "benchmark_at_entry": round(b_in, 4),
                        "benchmark_at_exit": round(b_out, 4),
                        "benchmark_return_pct": round(bench_return, 2),
                        "benchmark_source": source,
                        # Ditandai supaya kartu di UI bisa jujur menyebut ini
                        # hasil pemulihan, sama seperti `prices_backfilled`.
                        "benchmark_backfilled": True,
                    }
                    if ret is not None:
                        patch["excess_return_pct"] = round(ret - bench_return, 2)
                    computed[key] = patch
                    stats["tesis_dipulihkan"] += 1
                if patch:
                    rec.update(patch)
                    stats["outcome_ditulis"] += 1

    print("\n--- hasil ---")
    for k, v in stats.most_common():
        print(f"  {k:28s} {v}")

    if args.dry_run:
        print("\n--dry-run: tidak ada yang ditulis.")
        return 0
    if not stats["outcome_ditulis"]:
        print("\nTidak ada yang perlu ditulis.")
        return 0

    print(f"\nMenulis {HISTORY_PATH.name} ...")
    write_text_atomic(HISTORY_PATH, dumps_safe(data, indent=2, ensure_ascii=False))
    print("Selesai.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
