"""Isi `miss_reason` untuk outcome yang sudah terlanjur dievaluasi.

Turunan murni dari angka yang sudah tersimpan (action, classification,
return_pct, threshold_pct, excess_return_pct) lewat fungsi produksi
`diagnose_miss()` yang sama persis — bukan salinan logikanya. Kalau suatu saat
definisi sebabnya berubah, jalankan ulang skrip ini dan seluruh riwayat ikut
bergerak bersama kode, tanpa ada dua versi kebenaran.

Jalankan SESUDAH scripts/backfill_benchmark.py: `turun_bareng_pasar` butuh
`excess_return_pct`, dan tanpa itu tesis yang turun lebih sedikit dari indeks
akan salah dilabeli `arah_salah`.

Aman diulang (idempoten): menulis ulang nilai yang sama.

Pakai:
    python scripts/backfill_miss_reason.py --dry-run
    python scripts/backfill_miss_reason.py
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaforge.json_safe import dumps_safe, write_text_atomic  # noqa: E402
from alphaforge.personal.personal_evaluation import diagnose_miss  # noqa: E402

HISTORY_PATH = ROOT / "dashboard" / "data" / "personal" / "personal_history.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Membaca {HISTORY_PATH.name} ...")
    data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    timelines = data.get("timelines", data)

    stats = Counter()
    reasons = Counter()
    # Per thesis_key: satu tesis punya satu sebab, disalin ke semua snapshot
    # dalam streak-nya (konvensi yang sama dengan outcome itu sendiri).
    seen: set[tuple[str, str, str]] = set()

    for ticker, tl in timelines.items():
        for entry in (tl.get("entries", []) if isinstance(tl, dict) else []):
            calls = entry.get("personal_call_set") or {}
            for module, rec in (entry.get("outcome") or {}).items():
                if not isinstance(rec, dict):
                    continue
                stats["outcome_total"] += 1
                # `action` diambil dari call di snapshot yang sama; kalau
                # snapshot ini bukan tempat tesisnya lahir, action-nya bisa
                # sudah bergeser — tapi diagnose_miss cuma memakainya untuk
                # membedakan masuk vs keluar, dan satu streak menurut
                # definisinya punya action yang sama sepanjang hidupnya.
                call = calls.get(module) or {}
                action = call.get("action")
                if not action:
                    stats["dilewati_tanpa_action"] += 1
                    continue
                reason = diagnose_miss(
                    action, rec.get("classification"), rec.get("return_pct"),
                    rec.get("threshold_pct"), rec.get("excess_return_pct"),
                )
                if rec.get("miss_reason") == reason and "miss_reason" in rec:
                    stats["sudah_sesuai"] += 1
                    continue
                rec["miss_reason"] = reason
                stats["ditulis"] += 1
                key = (ticker, module, rec.get("thesis_key") or "")
                if key not in seen:
                    seen.add(key)
                    reasons[reason or f"(bukan meleset: {rec.get('classification')})"] += 1

    print("\n--- hasil (per outcome) ---")
    for k, v in stats.most_common():
        print(f"  {k:26s} {v}")
    print("\n--- sebab (per TESIS unik) ---")
    for k, v in reasons.most_common():
        print(f"  {str(k):40s} {v}")

    if args.dry_run:
        print("\n--dry-run: tidak ada yang ditulis.")
        return 0
    if not stats["ditulis"]:
        print("\nTidak ada yang perlu ditulis.")
        return 0
    print(f"\nMenulis {HISTORY_PATH.name} ...")
    write_text_atomic(HISTORY_PATH, dumps_safe(data, indent=2, ensure_ascii=False))
    print("Selesai.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
