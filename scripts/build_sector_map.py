"""Build/refresh sector reference map — job terpisah (jalan manual atau
dijadwalkan bulanan, BUKAN bagian dari refresh harian) yang mengklasifikasi
tiap ticker di universe Screening ke sektor GICS-nya, supaya screening
sektor-spesifik harian tidak perlu fetch .info per-ticker lagi (tinggal
baca cache).

Resumable: seed dari .cache/fundamental_data/ (hasil Evidence run yang
sudah pernah jalan) + sector_map.json lama kalau ada, lalu cuma fetch
ticker yang masih belum termapping. Nulis checkpoint tiap 100 ticker
supaya Ctrl+C / crash di tengah jalan cuma hilang progress sebagian kecil,
bukan seluruh run.

Cara pakai: python scripts/build_sector_map.py
Kadensa yang disarankan: bulanan (klasifikasi sektor jarang berubah).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from montrva.layer2.sources.listing import fetch_universe, cheap_filter  # noqa: E402
from montrva.layer2.sources.sector_map import (  # noqa: E402
    load_sector_map_meta, save_sector_map, seed_from_fundamental_cache, fetch_sector,
)

BATCH_SIZE = 25
BATCH_DELAY_SECONDS = 2.0
CHECKPOINT_EVERY = 100


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                         help="Batasi jumlah ticker BARU yang di-fetch (buat testing, bukan production run)")
    args = parser.parse_args()

    universe = cheap_filter(fetch_universe())
    all_tickers = [r.symbol for r in universe]
    print(f"Universe: {len(all_tickers)} ticker (setelah cheap filter)")

    existing_meta = load_sector_map_meta()
    tickers_map: dict[str, str] = dict(existing_meta.get("tickers", {})) if existing_meta else {}
    if existing_meta:
        print(f"Resume dari map lama: {len(tickers_map)} ticker (dibangun {existing_meta.get('generated_at')})")

    seeded = seed_from_fundamental_cache()
    new_from_seed = 0
    for t, s in seeded.items():
        if t not in tickers_map:
            tickers_map[t] = s
            new_from_seed += 1
    print(f"Seed dari fundamental_data cache: +{new_from_seed} ticker")

    to_fetch = [t for t in all_tickers if t not in tickers_map]
    if args.limit:
        to_fetch = to_fetch[:args.limit]
    print(f"Perlu fetch baru: {len(to_fetch)} ticker")

    if not to_fetch:
        save_sector_map(tickers_map)
        print(f"Sudah lengkap. Total termapping: {len(tickers_map)}/{len(all_tickers)} ticker.")
        return 0

    fetched = 0
    started = time.time()
    for i, ticker in enumerate(to_fetch, 1):
        if i > 1 and (i - 1) % BATCH_SIZE == 0:
            time.sleep(BATCH_DELAY_SECONDS)

        sector = fetch_sector(ticker)
        if sector:
            tickers_map[ticker] = sector
            fetched += 1

        if i % CHECKPOINT_EVERY == 0:
            save_sector_map(tickers_map)
            elapsed = time.time() - started
            rate = i / elapsed if elapsed > 0 else 0
            eta_min = (len(to_fetch) - i) / rate / 60 if rate > 0 else float("nan")
            print(f"  {i}/{len(to_fetch)} diproses ({fetched} berhasil) — checkpoint tersimpan, ETA ~{eta_min:.0f} menit")

    save_sector_map(tickers_map)
    print(f"Selesai. Total termapping: {len(tickers_map)}/{len(all_tickers)} ticker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
