"""Bangun ulang dashboard/data/personal/calibration.json dari riwayat di disk.

Berdiri sendiri supaya rapor bisa dibangun ulang tanpa menjalankan pipeline
penuh (3-4 jam) — sama alasannya dengan build_institutional_flow.py. Nol
jaringan: cuma membaca personal_history.json.

Aman dijalankan kapan saja termasuk saat pipeline berjalan: hanya MEMBACA
riwayat dan hanya MENULIS calibration.json (berkas yang tidak disentuh
tahap lain).

Pakai:
    python scripts/build_calibration.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaforge.json_safe import dumps_safe, write_text_atomic  # noqa: E402
from alphaforge.personal.personal_calibration import build_calibration  # noqa: E402

PERSONAL_DIR = ROOT / "dashboard" / "data" / "personal"


def main() -> int:
    history_path = PERSONAL_DIR / "personal_history.json"
    if not history_path.exists():
        print(f"Tidak ada {history_path}")
        return 1

    print(f"Membaca {history_path.name} ...")
    timelines = json.loads(history_path.read_text(encoding="utf-8"))
    timelines = timelines.get("timelines", timelines)

    report = build_calibration(timelines, baselines_path=PERSONAL_DIR / "baselines.json")
    out = PERSONAL_DIR / "calibration.json"
    write_text_atomic(out, dumps_safe(report, indent=2, ensure_ascii=False))

    o = report["overall"]
    print(f"\nTesis: {report['theses_total']} total, {report['theses_directional']} berarah")
    print(f"Hit rate: {o['hit_rate_pct']}%  ({o['terbukti']} terbukti / {o['meleset']} meleset)")
    print(f"Tanggal masuk berbeda: {o['entry_dates']}  -> evaluable={o['evaluable']}")
    for lim in o["limiters"]:
        print(f"  penghambat: {lim}")
    print(f"Median excess: {o['median_excess_pct']}%  (unggul indeks {o['beat_index']}/{o['with_excess']})")
    print(f"\nSebab meleset: {report['miss_reasons']}")
    print("\nBelum bisa dinilai:")
    for n in report["not_yet_evaluable"]:
        print(f"  {n['module']:18s} divonis={n['evaluated']:4d} call aktif={n['active_calls']:5d} "
              f"vonis paling cepat={n['earliest_possible_verdict']}")
    n_eval = sum(1 for s in report["slices"] if s["evaluable"])
    print(f"\nIrisan: {len(report['slices'])} total, {n_eval} lolos gerbang bukti")
    print(f"Ditulis: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
