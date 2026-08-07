"""Perbaiki outcome yang exit_date-nya mustahil, dan semua yang lahir darinya.

MASALAHNYA. Versi lama scripts/backfill_outcome_prices.py mencari exit_date
dengan mencocokkan harga ke bar, dengan batas atas tapi TANPA batas bawah.
exit_price umumnya last_price intraday yang tidak pernah jadi harga tutup, jadi
sering tidak ada bar yang cocok di dalam jendela -- dan pencariannya mundur ke
seluruh riwayat dua tahun, menunjuk hari yang kebetulan sama harganya. Live
2026-08-06: 450 objek outcome (222 tesis unik) punya exit_date SEBELUM
entry_date, mis. HTO masuk 27 Jul dan "keluar" 9 Apr.

KENAPA BUKAN SEKADAR DIKOSONGKAN. exit_date bukan hiasan di kartu: ia menentukan
di hari mana harga indeks dibaca (excess_return_pct) DAN berapa bar bursa masuk
ke jendela window_sigma_pct. Dengan tanggal mundur, n_bars jadi 0 dan vonis v2
-- satu-satunya vonis yang bermakna sesudah temuan 5 Agu -- diam-diam kosong
untuk 222 tesis. Mengosongkan tanggalnya membuang kerusakan tapi juga membuang
vonisnya.

APA YANG DIPULIHKAN, DAN KENAPA ITU BUKAN MENGARANG. exit_date dihitung ulang
dengan exit_date_as_of() -- aturan yang SAMA PERSIS dengan yang dipakai
evaluator (bar terakhir yang sudah tutup saat evaluasi berjalan), diimpor dari
personal_evaluation.py, bukan disalin ke sini. Diuji lawan 617 record backfill
yang tanggalnya wajar: aturan ini mereproduksi 91,9%; "tanggal evaluasi = tanggal
bar" cuma 1,1%.

SESUDAH TANGGALNYA BENAR, yang dihitung ulang cuma yang memang turunan
tanggal itu, lewat fungsi produksi (resolve_benchmark_pair, diagnose_miss):
benchmark_at_entry/at_exit/benchmark_return_pct/benchmark_source, lalu
excess_return_pct, lalu miss_reason. return_pct, entry_price, exit_price, dan
classification TIDAK disentuh -- tidak satu pun turunan dari exit_date.

Vonis v2 (classification_v2/z_excess/sigma_window_pct) sengaja TIDAK dihitung
di sini: scripts/backfill_verdict_v2.py sudah melakukannya untuk seluruh berkas
dengan aturan yang sama. Jalankan skrip ini dulu, lalu skrip itu.

Idempoten: jalan kedua kali tidak menemukan apa-apa untuk diperbaiki. Aman
dijalankan saat pipeline berjalan (tahap personal baru membaca berkas ini di
akhir run), tapi JANGAN dijalankan berbarengan dengan backfill lain yang juga
menulis berkas yang sama.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alphaforge.json_safe import dumps_safe, write_text_atomic  # noqa: E402
from alphaforge.layer1.benchmark_history import load_benchmark_history  # noqa: E402
from alphaforge.personal.personal_evaluation import (  # noqa: E402
    diagnose_miss, exit_date_as_of, resolve_benchmark_pair,
)

HISTORY = ROOT / "dashboard" / "data" / "personal" / "personal_history.json"
BENCHMARK = ROOT / "dashboard" / "data" / "benchmark_history.json"
PRICE_CACHE = ROOT / ".cache" / "price_history"


def bar_dates(ticker: str, cache: dict[str, list[str]]) -> list[str]:
    if ticker not in cache:
        p = PRICE_CACHE / f"{ticker}.json"
        dates: list[str] = []
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8")).get("data") or []
            except (json.JSONDecodeError, OSError):
                raw = []
            dates = sorted({
                str(b.get("__date__") or b.get("date"))[:10] for b in raw
                if isinstance(b, dict) and (b.get("Close", b.get("close"))) is not None
            })
        cache[ticker] = dates
    return cache[ticker]


def is_impossible(rec: dict) -> bool:
    """exit_date yang tidak mungkin benar: mendahului tanggal masuk, atau
    melewati hari evaluasi (harga penutupnya belum ada saat itu)."""
    ed, xd = rec.get("entry_date"), rec.get("exit_date")
    if not ed or not xd:
        return False
    evaluated = (rec.get("evaluated_at") or "")[:10]
    return xd < ed[:10] or bool(evaluated and xd > evaluated)


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or not a:
        return None
    return (b - a) / a * 100.0


def repair(timelines: dict, series: dict[str, float]) -> tuple[Counter, list[dict]]:
    stats = Counter()
    changes: list[dict] = []
    bars_cache: dict[str, list[str]] = {}
    # Satu tesis = satu outcome yang disalin ke SEMUA entry dalam streak-nya,
    # jadi perbaikannya dihitung sekali per tesis lalu ditulis ke tiap salinan.
    # Kalau tidak, dua baris Riwayat untuk tesis yang sama bisa menampilkan
    # tanggal (dan excess) yang berbeda.
    computed: dict[tuple[str, str, str], dict | None] = {}

    for ticker, tl in timelines.items():
        if not isinstance(tl, dict):
            continue
        for entry in tl.get("entries", []):
            calls = entry.get("personal_call_set") or {}
            for module, rec in (entry.get("outcome") or {}).items():
                if not isinstance(rec, dict):
                    continue
                stats["outcome_total"] += 1
                if not is_impossible(rec):
                    continue
                stats["outcome_rusak"] += 1

                key = (ticker, module, rec.get("thesis_key") or rec.get("entry_date") or "")
                if key not in computed:
                    computed[key] = _patch_for(
                        ticker, rec, calls.get(module) or {}, series, bars_cache, stats, changes,
                    )
                patch = computed[key]
                if patch:
                    rec.update(patch)
                    stats["outcome_ditulis"] += 1

    stats["tesis_diperbaiki"] = sum(1 for p in computed.values() if p)
    stats["tesis_tanpa_tanggal_pengganti"] = sum(1 for p in computed.values() if not p)
    return stats, changes


def _patch_for(
    ticker: str, rec: dict, call: dict, series: dict[str, float],
    bars_cache: dict[str, list[str]], stats: Counter, changes: list[dict],
) -> dict | None:
    entry_date = (rec.get("entry_date") or "")[:10]
    new_exit = exit_date_as_of(bar_dates(ticker, bars_cache), rec.get("evaluated_at"))
    if not new_exit or new_exit < entry_date:
        # Tidak ada bar yang sah di jendela ini. Tanggal lamanya tetap salah,
        # jadi dikosongkan -- "tidak tahu" lebih benar daripada tanggal palsu.
        stats["tanpa_tanggal_pengganti"] += 1
        return {"exit_date": None, "exit_date_repaired": True}

    patch: dict = {"exit_date": new_exit, "exit_date_repaired": True}

    # Sisi indeks dibaca ulang di tanggal yang benar, lewat fungsi produksi.
    # stored_at_call sengaja None: dengan begitu KEDUA sisi dibaca dari deret
    # yang sama dan `benchmark_source` merekam tanggal keduanya -- angka lama
    # di benchmark_at_entry sendiri lahir dari pemulihan, bukan dari call.
    b_in, b_out, source = resolve_benchmark_pair(
        series, None, entry_date, new_exit, None,
    )
    bench_return = _pct(b_in, b_out)
    if bench_return is None:
        stats["benchmark_tidak_tersedia"] += 1
        return patch

    ret = rec.get("return_pct")
    excess = round(ret - bench_return, 2) if ret is not None else None
    before = {
        "exit_date": rec.get("exit_date"),
        "benchmark_source": rec.get("benchmark_source"),
        "excess_return_pct": rec.get("excess_return_pct"),
        "miss_reason": rec.get("miss_reason"),
    }
    patch.update({
        "benchmark_at_entry": round(b_in, 4),
        "benchmark_at_exit": round(b_out, 4),
        "benchmark_return_pct": round(bench_return, 2),
        "benchmark_source": source,
        "excess_return_pct": excess,
    })
    # miss_reason memeriksa excess (turun bareng pasar vs arah salah), jadi ia
    # ikut basi kalau excess berubah. Action diambil dari call di snapshot yang
    # sama (outcome sendiri tidak menyimpannya) -- alasan yang sama dengan
    # backfill_miss_reason.py: diagnose_miss cuma memakainya untuk membedakan
    # masuk vs keluar, dan satu streak punya action yang sama sepanjang hidupnya.
    action = call.get("action")
    if action:
        patch["miss_reason"] = diagnose_miss(
            action, rec.get("classification"), rec.get("return_pct"),
            rec.get("threshold_pct"), excess,
        )
    if before["excess_return_pct"] != excess:
        stats["excess_berubah"] += 1
    if len(changes) < 25:
        changes.append({"ticker": ticker, "sebelum": before,
                        "sesudah": {k: patch.get(k) for k in before}})
    return patch


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="hitung saja, jangan tulis")
    args = ap.parse_args()

    if not HISTORY.exists():
        print(f"tidak ada {HISTORY}")
        return 1
    print(f"Membaca {HISTORY.name} ({HISTORY.stat().st_size // 2**20} MB) ...", flush=True)
    data = json.loads(HISTORY.read_text(encoding="utf-8"))
    timelines = data.get("timelines", data)
    series = load_benchmark_history(BENCHMARK) or {}
    print(f"deret indeks: {len(series)} bar", flush=True)

    stats, changes = repair(timelines, series)

    print("\n--- hasil ---")
    for k, v in stats.most_common():
        print(f"  {k:32s} {v}")
    print("\n--- contoh perubahan ---")
    for c in changes[:8]:
        print(f"  {c['ticker']}: {c['sebelum']}")
        print(f"      -> {c['sesudah']}")

    if args.dry_run:
        print("\n--dry-run: tidak ada yang ditulis.")
        return 0
    if not stats["outcome_ditulis"]:
        print("\nTidak ada yang perlu ditulis.")
        return 0
    print(f"\nMenulis {HISTORY.name} ...", flush=True)
    write_text_atomic(HISTORY, dumps_safe(data, indent=2, ensure_ascii=False))
    print("Selesai. Jalankan scripts/backfill_verdict_v2.py untuk vonis v2-nya.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
