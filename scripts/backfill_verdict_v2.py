"""Hitung ulang SELURUH riwayat di bawah vonis v2 (excess / volatilitas).

Vonis v1 (`classification`, target tetap 3%) tidak disentuh sama sekali —
lihat blok komentar di personal_evaluation.py. Yang ditambahkan di sebelahnya:
`classification_v2`, `z_excess`, `sigma_window_pct`, `z_threshold`.

Sumber harga: .cache/price_history (per-ticker, ~80 KB/berkas) dan BUKAN
evidence.json 262 MB — sama seperti scripts/measure_baseline.py. Di mesin ini
membaca ribuan berkas kecil jauh lebih murah daripada mem-parse satu berkas
raksasa, dan hanya ticker yang benar-benar punya outcome yang dibuka.

Aman diulang (idempoten): menulis nilai yang sama.

Pakai (JANGAN disalurkan ke tail/head — progresnya jadi tidak kelihatan):
    python -u scripts/backfill_verdict_v2.py --dry-run
    python -u scripts/backfill_verdict_v2.py
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
from alphaforge.personal.personal_evaluation import (  # noqa: E402
    Z_TERBUKTI, classify_v2, window_sigma_pct,
)

CACHE = ROOT / ".cache" / "price_history"
HISTORY = ROOT / "dashboard" / "data" / "personal" / "personal_history.json"


def load_bars(ticker: str) -> list[dict]:
    p = CACHE / f"{ticker}.json"
    if not p.exists():
        p = CACHE / f"_{ticker}.json"  # nama haram Windows (CON, PRN, ...)
        if not p.exists():
            return []
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [
        {"date": str(b["__date__"])[:10], "close": b["Close"]}
        for b in (payload.get("data") or [])
        if b.get("__date__") and isinstance(b.get("Close"), (int, float))
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    print(f"Membaca {HISTORY.name} ({HISTORY.stat().st_size // 2**20} MB) ...", flush=True)
    data = json.loads(HISTORY.read_text(encoding="utf-8"))
    timelines = data.get("timelines", data)

    # Ticker mana saja yang punya outcome -- cuma itu yang perlu dibuka
    # berkas harganya (sekitar 2 ribu, bukan 5.265).
    need = [
        t for t, tl in timelines.items()
        if isinstance(tl, dict) and any(e.get("outcome") for e in tl.get("entries", []))
    ]
    print(f"{len(need)} ticker punya outcome — memuat bar harganya ...", flush=True)
    bars_by_ticker: dict[str, list[dict]] = {}
    for i, t in enumerate(need, 1):
        b = load_bars(t)
        if b:
            bars_by_ticker[t] = b
        if i % 250 == 0:
            print(f"  {i}/{len(need)} ...", flush=True)
    print(f"  {len(bars_by_ticker)} punya bar", flush=True)

    stats = Counter()
    verdicts = Counter()
    seen: set[str] = set()

    for ticker, tl in timelines.items():
        bars = bars_by_ticker.get(ticker)
        for entry in (tl.get("entries", []) if isinstance(tl, dict) else []):
            calls = entry.get("personal_call_set") or {}
            for module, rec in (entry.get("outcome") or {}).items():
                if not isinstance(rec, dict):
                    continue
                stats["outcome_total"] += 1
                action = (calls.get(module) or {}).get("action")
                if not action:
                    stats["dilewati_tanpa_action"] += 1
                    continue

                sigma = window_sigma_pct(bars, rec.get("entry_date"), rec.get("exit_date"))
                cls, z = classify_v2(action, rec.get("excess_return_pct"), sigma)
                rec["classification_v2"] = cls
                rec["z_excess"] = z
                rec["sigma_window_pct"] = round(sigma, 3) if sigma is not None else None
                rec["z_threshold"] = Z_TERBUKTI
                stats["ditulis"] += 1

                key = f"{ticker}:{module}:{rec.get('thesis_key')}"
                if key not in seen:
                    seen.add(key)
                    verdicts[str(cls)] += 1
                    if cls is None:
                        if rec.get("excess_return_pct") is None:
                            verdicts["  (sebab: excess kosong)"] += 1
                        elif sigma is None:
                            verdicts["  (sebab: bar historis kurang)"] += 1

    print("\n--- per outcome ---")
    for k, v in stats.most_common():
        print(f"  {k:26s} {v}")
    print("\n--- vonis v2 per TESIS unik ---")
    for k, v in verdicts.most_common():
        print(f"  {k:32s} {v}")

    if args.dry_run:
        print("\n--dry-run: tidak ada yang ditulis.")
        return 0
    print(f"\nMenulis {HISTORY.name} ...", flush=True)
    write_text_atomic(HISTORY, dumps_safe(data, indent=2, ensure_ascii=False))
    print("Selesai.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
