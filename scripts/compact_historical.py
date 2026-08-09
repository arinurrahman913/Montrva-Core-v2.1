"""Pangkas historical_timeline.json yang sudah terlanjur gemuk.

Berkas ini menyimpan snapshot AggregatorOutput UTUH per ticker per hari sejak
awal: 570 MB untuk 13 hari, 97,3% di antaranya `aggregator_output`, dengan
proyeksi ~31 GB pada retensi lama (730 hari). Aturan barunya ada di
alphaforge/layer2/historical.py: entry TERAKHIR tiap ticker tetap penuh,
sisanya jadi bentuk tipis (`thin_entry`), retensi 365 hari.

`update_timeline` sudah menerapkan aturan itu tiap run, jadi skrip ini
sebenarnya cuma MEMPERCEPAT — tanpa dijalankan pun berkasnya rapi sendiri
sesudah run penuh berikutnya. Gunanya: tidak perlu menunggu run 2-3 jam, dan
run itu sendiri jadi jauh lebih ringan (`load_historical_timeline` melakukan
`json.load` atas seluruh berkas ini di tengah pipeline).

MEMORI: puncaknya satu record ticker, bukan seluruh berkas — dibaca lewat
indeks offset `backend/big_json.py` yang sama dengan yang dipakai backend,
dan ditulis mengalir per record. `json.load` atas 570 MB di mesin 7,5 GB
justru masalah yang sedang diperbaiki, jadi skrip perbaikannya tidak boleh
melakukannya.

Aman diulang (idempoten). JANGAN dijalankan saat pipeline berjalan: keduanya
menulis berkas yang sama.

Pakai:
    python scripts/compact_historical.py            # tulis hasilnya
    python scripts/compact_historical.py --dry-run  # cuma laporkan ukurannya
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaforge.json_safe import dump_safe  # noqa: E402
from alphaforge.layer2.historical import RETENTION_DAYS, thin_entry  # noqa: E402
from backend import big_json  # noqa: E402

TIMELINE_PATH = ROOT / "dashboard" / "data" / "historical_timeline.json"


def _mb(num_bytes: int) -> float:
    return num_bytes / 1024 / 1024


def _keep(entry: dict, cutoff: datetime) -> bool:
    """Sama aturannya dengan `_prune_old_entries`: tanggal tak terbaca
    DIPERTAHANKAN, bukan dibuang diam-diam."""
    try:
        dt = datetime.fromisoformat(entry.get("analyzed_at"))
    except (ValueError, TypeError):
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


def compact_record(timeline: dict, cutoff: datetime) -> tuple[dict, int, int]:
    """Kembalikan (timeline terpangkas, entry dibuang, entry ditipiskan).

    `total_entries`/`first_entry_date` TIDAK disentuh — keduanya penghitung
    SEUMUR HIDUP ticker (dipakai StatCards "Total Snapshots" & kolom
    "Snapshot Terakhir"), bukan hitungan isi buffer.
    """
    entries = list(timeline.get("entries") or [])
    kept = [e for e in entries if _keep(e, cutoff)]
    dropped = len(entries) - len(kept)

    thinned = 0
    for i in range(len(kept) - 1):
        before = kept[i]
        after = thin_entry(before)
        if after is not before:
            thinned += 1
        kept[i] = after

    out = dict(timeline)
    out["entries"] = kept
    return out, dropped, thinned


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not TIMELINE_PATH.exists():
        print(f"Tidak ada {TIMELINE_PATH}")
        return 1

    size_before = TIMELINE_PATH.stat().st_size
    print(f"Membaca indeks {TIMELINE_PATH.name} ({_mb(size_before):.1f} MB) ...")
    index = big_json.build_historical_index(TIMELINE_PATH)
    if index is None:
        # Sengaja BUKAN jatuh ke json.load: kalau indeksnya gagal, bentuk
        # berkasnya di luar dugaan skrip ini, dan menulis ulang berdasarkan
        # dugaan yang sudah terbukti salah adalah cara kehilangan riwayat.
        print("Indeks gagal dibangun — berkas tidak dalam bentuk yang dikenali. "
              "Tidak ada yang ditulis; jalankan run penuh, `update_timeline` "
              "akan merapikannya sendiri.")
        return 1
    print(f"{len(index)} ticker terindeks")

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    tmp = TIMELINE_PATH.with_name(TIMELINE_PATH.name + ".compact.tmp")
    total_dropped = total_thinned = total_entries = 0

    try:
        with open(os.devnull if dry_run else tmp, "w", encoding="utf-8") as f:
            f.write("{")
            first = True
            for i, (ticker, timeline) in enumerate(index.iter_records(), 1):
                record, dropped, thinned = compact_record(timeline, cutoff)
                total_dropped += dropped
                total_thinned += thinned
                total_entries += len(record["entries"])

                if not first:
                    f.write(",")
                first = False
                # Format byte-identik dengan yang ditulis pipeline
                # (`_atomic_write` -> `dump_safe(indent=None)`): kompak, tanpa
                # spasi sesudah titik dua. big_json membangun indeksnya dari
                # bentuk itu.
                dump_safe(ticker, f)
                f.write(":")
                dump_safe(record, f)

                if i % 500 == 0:
                    print(f"  {i}/{len(index)} ticker ...", flush=True)
            f.write("}")
    except BaseException:
        if not dry_run:
            Path(tmp).unlink(missing_ok=True)
        raise

    print(f"\nEntry: {total_entries} disimpan, {total_thinned} ditipiskan, "
          f"{total_dropped} dibuang (lebih tua dari {RETENTION_DAYS} hari)")

    if dry_run:
        print("--dry-run: tidak ada yang ditulis.")
        return 0

    size_after = tmp.stat().st_size

    # Buktikan hasilnya SEBELUM menimpa aslinya: indeks harus terbangun
    # ulang, jumlah tickernya sama, dan contoh recordnya benar-benar parse.
    # Tanpa langkah ini, satu salah tebak format menukar berkas 570 MB dengan
    # berkas rusak yang baru ketahuan saat dashboard dibuka.
    check = big_json.build_historical_index(tmp)
    if check is None or len(check) != len(index):
        tmp.unlink(missing_ok=True)
        print(f"GAGAL verifikasi: indeks hasil "
              f"{'tidak terbangun' if check is None else f'{len(check)} != {len(index)}'}. "
              "Berkas asli TIDAK disentuh.")
        return 1

    os.replace(tmp, TIMELINE_PATH)
    print(f"{_mb(size_before):.1f} MB -> {_mb(size_after):.1f} MB "
          f"({100 * (1 - size_after / size_before):.1f}% lebih kecil)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
