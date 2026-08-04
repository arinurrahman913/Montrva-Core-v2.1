"""Pulihkan rincian harga untuk outcome yang sudah terlanjur dievaluasi.

personal_evaluation.py baru menyimpan entry_price/exit_price/target_price/
tanggal sejak 2026-08-03. 749 outcome yang dievaluasi sebelum itu cuma punya
return_pct -- panelnya di Riwayat Pribadi jadi tidak bisa menunjukkan angka
apa pun di balik vonisnya.

Yang dipulihkan di sini SEMUANYA turunan eksak, bukan taksiran:

  entry_price  price_at_call kalau tersimpan (persis angka yang dulu dipakai);
               kalau tidak, direkonstruksi lewat _reconstruct_start_price --
               FUNGSI YANG SAMA yang dipakai evaluator waktu itu, atas harga
               tutup masa lalu yang tidak berubah.
  exit_price   entry_price x (1 + return_pct/100). return_pct memang dihitung
               dari kedua harga itu, jadi ini membalik aritmetika yang sama,
               bukan menebak harga baru.
  exit_date    tanggal bar yang harganya cocok dengan exit_price (toleransi
               ketat). Kalau tidak ada yang cocok -> None, TIDAK dikarang:
               last_price saat evaluasi bisa saja harga intraday yang tidak
               pernah jadi harga tutup.
  target_price entry_price x (1 + threshold_pct/100), cuma untuk action
               berklaim naik (sama aturannya dengan evaluator).
  horizon_days due_at - entry_date.

Yang TIDAK dipulihkan: evaluation_lag_days (butuh tanggal evaluasi vs due --
sebenarnya ada di evaluated_at, jadi ikut diisi) dan apa pun yang menyentuh
classification/return_pct. Nilai yang sudah ada tidak pernah ditimpa.

Ditandai `prices_backfilled: true` supaya jujur bahwa angka ini dipulihkan
setelah fakta, bukan dicatat saat evaluasi.

Aman dijalankan berkali-kali (idempoten) dan aman dijalankan saat pipeline
sedang berjalan: tahap personal baru membaca berkas ini di akhir run, dan
outcome yang sudah terisi tidak pernah dievaluasi ulang.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alphaforge.json_safe import dumps_safe  # noqa: E402
from alphaforge.personal.personal_evaluation import (  # noqa: E402
    ACTION_CATEGORY_ENTRY, _reconstruct_start_price,
)

HISTORY = ROOT / "dashboard" / "data" / "personal" / "personal_history.json"
PRICE_CACHE = ROOT / ".cache" / "price_history"
MODULES = ("multibagger", "quality_compound", "speculative")

# Bar dianggap cocok dengan exit_price kalau selisihnya < 0,05% -- cukup ketat
# untuk tidak salah menunjuk hari lain, cukup longgar untuk menyerap
# pembulatan return_pct ke 2 desimal.
_MATCH_TOLERANCE = 0.0005


def load_price_history(ticker: str) -> list[dict] | None:
    """Baca cache price_history mentah -> bentuk yang sama dengan yang dipakai
    evaluator ({"date", "close"})."""
    p = PRICE_CACHE / f"{ticker}.json"
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8")).get("data") or []
    except (json.JSONDecodeError, OSError):
        return None
    bars = []
    for b in raw:
        if not isinstance(b, dict):
            continue
        d, c = b.get("__date__") or b.get("date"), b.get("Close", b.get("close"))
        if d and c is not None:
            bars.append({"date": str(d)[:10], "close": float(c)})
    return bars or None


def match_bar_date(bars: list[dict] | None, price: float, not_after: str | None) -> str | None:
    if not bars or price is None:
        return None
    best = None
    for b in bars:
        if not_after and b["date"] > not_after:
            continue
        if b["close"] and abs(b["close"] - price) / price < _MATCH_TOLERANCE:
            if best is None or b["date"] > best:
                best = b["date"]
    return best


def backfill(history: dict) -> tuple[int, int, int]:
    filled = skipped = no_price = 0
    cache: dict[str, list[dict] | None] = {}

    for ticker, timeline in history.items():
        entries = timeline.get("entries") or []
        for entry in entries:
            outcome = entry.get("outcome") or {}
            for module in MODULES:
                o = outcome.get(module)
                if not o or o.get("entry_price") is not None:
                    continue
                if o.get("return_pct") is None:
                    skipped += 1
                    continue

                entry_date = o.get("entry_date") or _streak_start(entries, module, entry)
                call = (entry.get("personal_call_set") or {}).get(module) or {}
                entry_price = call.get("price_at_call")
                if entry_price is None:
                    if ticker not in cache:
                        cache[ticker] = load_price_history(ticker)
                    entry_price = _reconstruct_start_price(cache[ticker], entry_date)
                if entry_price is None:
                    no_price += 1
                    continue

                exit_price = entry_price * (1 + o["return_pct"] / 100.0)
                if ticker not in cache:
                    cache[ticker] = load_price_history(ticker)
                threshold = o.get("threshold_pct")
                action = call.get("action")

                o["entry_price"] = round(entry_price, 4)
                o["entry_date"] = entry_date
                o["exit_price"] = round(exit_price, 4)
                o["exit_date"] = match_bar_date(cache[ticker], exit_price, o.get("evaluated_at", "")[:10] or None)
                o["target_price"] = (
                    round(entry_price * (1 + threshold / 100.0), 4)
                    if threshold is not None and action in ACTION_CATEGORY_ENTRY else None
                )
                o["horizon_days"] = _days_between(entry_date, o.get("due_at"))
                o["evaluation_lag_days"] = _days_between(o.get("due_at"), (o.get("evaluated_at") or "")[:10])
                o["prices_backfilled"] = True
                filled += 1

    return filled, skipped, no_price


def _streak_start(entries: list[dict], module: str, entry: dict) -> str:
    """thesis_key formatnya "<module>:<tanggal mulai streak>" -- sumber paling
    tepat untuk tanggal entry, karena satu vonis ditulis ke semua entry dalam
    streak (jadi tanggal entry harian tidak boleh dipakai)."""
    key = ((entry.get("outcome") or {}).get(module) or {}).get("thesis_key") or ""
    if ":" in key:
        return key.split(":", 1)[1][:10]
    return (entry.get("analyzed_at") or "")[:10]


def _days_between(start: str | None, end: str | None) -> int | None:
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days
    except ValueError:
        return None


def main() -> int:
    if not HISTORY.exists():
        print(f"tidak ada {HISTORY}")
        return 1
    history = json.loads(HISTORY.read_text(encoding="utf-8"))
    filled, skipped, no_price = backfill(history)

    tmp = HISTORY.with_name(HISTORY.name + ".tmp")
    tmp.write_text(dumps_safe(history, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(HISTORY)

    print(f"dipulihkan : {filled} outcome")
    print(f"dilewati   : {skipped} (return_pct kosong)")
    print(f"tanpa harga: {no_price} (price_history tidak menjangkau tanggal entry)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
