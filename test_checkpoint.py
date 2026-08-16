"""Uji checkpoint antara (montrva/personal/personal_checkpoint.py).

Yang dijaga di sini bukan cuma benar/salahnya vonis, tapi PEMBATAS-nya: bahwa
checkpoint tidak menyala sebelum ada laporan baru, dan bahwa kosakatanya tidak
pernah bertabrakan dengan kosakata outcome tesis. Dua hal itu yang membuatnya
bukan vonis kedua.

Jalankan: python test_checkpoint.py
"""
from __future__ import annotations

from datetime import date

from montrva.personal import personal_checkpoint as cp

_passed = 0


def check(label, actual, expected):
    global _passed
    if actual != expected:
        raise AssertionError(f"{label}: dapat {actual!r}, harusnya {expected!r}")
    _passed += 1
    print(f"  ok  {label}: {actual!r}")


def section(t):
    print(f"\n{t}")


Q = "quality_compound"
M = "multibagger"

section("1. Stance yang menentukan, skor cuma pemecah seri")
check("kuat -> rapuh = melemah",
      cp.classify(Q, "compounding_kuat", 90, "compounding_rapuh", 90), "mekanisme_melemah")
check("rapuh -> kuat = menguat",
      cp.classify(Q, "compounding_rapuh", 40, "compounding_kuat", 40), "mekanisme_menguat")
check("stance sama, tier sama = bertahan",
      cp.classify(Q, "compounding_kuat", 88, "compounding_kuat", 72), "mekanisme_bertahan")
check("stance sama, tier turun high->medium = melemah",
      cp.classify(Q, "compounding_kuat", 75, "compounding_kuat", 60), "mekanisme_melemah")
check("stance sama, tier naik medium->high = menguat",
      cp.classify(M, "ruang_terbuka", 60, "ruang_terbuka", 75), "mekanisme_menguat")
check("stance turun MESKI skor naik tetap melemah",
      cp.classify(M, "ruang_terbuka", 40, "ruang_sempit", 95), "mekanisme_melemah")

section("2. Tak terbaca bukan 'melemah'")
check("sekarang tak terbaca", cp.classify(Q, "compounding_kuat", 80, "mesin_tak_terbaca", 50), "tidak_terbaca")
check("dulu tak terbaca, kini terbaca",
      cp.classify(Q, "mesin_tak_terbaca", 50, "compounding_rapuh", 55), "mekanisme_bertahan")
check("stance asing tidak diam-diam dinilai", cp.classify(Q, "compounding_kuat", 80, "ngawur", 80), "tidak_terbaca")

section("3. Kosakatanya tidak boleh bertabrakan dengan outcome tesis")
OUTCOME = {"terbukti", "meleset", "ambigu", "tidak_berlaku"}
check("nol kata yang sama", sorted(set(cp.CHECKPOINT_VERDICTS) & OUTCOME), [])
check("tidak ada fungsi hit rate di modul ini",
      [n for n in dir(cp) if "hit" in n.lower() or "rate" in n.lower()], [])

section("4. Pemicu earnings — checkpoint diam sebelum ada laporan baru")
RH = [
    {"kind": "earnings", "lifecycle_status": "completed", "expected_at": "2026-05-10"},
    {"kind": "earnings", "lifecycle_status": "completed", "expected_at": "2026-08-10"},
    {"kind": "dividend", "lifecycle_status": "completed", "expected_at": "2026-06-01"},
    {"kind": "earnings", "lifecycle_status": "cancelled", "expected_at": "2026-07-01"},
]
check("earnings pertama sesudah 1 Apr", cp.earnings_since(RH, date(2026, 4, 1)), date(2026, 5, 10))
check("earnings pertama sesudah 1 Jun", cp.earnings_since(RH, date(2026, 6, 1)), date(2026, 8, 10))
check("tidak ada sesudah 1 Sep", cp.earnings_since(RH, date(2026, 9, 1)), None)
check("dividen tidak dihitung sebagai laporan", cp.earnings_since(
    [{"kind": "dividend", "lifecycle_status": "completed", "expected_at": "2026-07-01"}], date(2026, 1, 1)), None)
# Katalis batal = laporannya TIDAK terbit = tidak ada data baru.
check("earnings batal tidak dihitung", cp.earnings_since(
    [{"kind": "earnings", "lifecycle_status": "cancelled", "expected_at": "2026-07-01"}], date(2026, 1, 1)), None)
check("riwayat kosong aman", cp.earnings_since([], date(2026, 1, 1)), None)
check("tanggal rusak dilewati", cp.earnings_since(
    [{"kind": "earnings", "lifecycle_status": "completed", "expected_at": "kemarin"}], date(2026, 1, 1)), None)

section("5. build_checkpoints — gerbang 'belum ada data' benar-benar mengunci")
tl = {"AAA": {"entries": [{
    "analyzed_at": "2026-06-01T00:00:00+00:00",
    "personal_call_set": {
        Q: {"source_stance": "compounding_kuat", "thesis_score": 80},
        M: {"source_stance": "ruang_terbuka", "thesis_score": 80},
    },
}]}}
now = {"AAA": {Q: {"stance": "bukan_compounder", "thesis_score": 30},
               M: {"stance": "ruang_terbuka", "thesis_score": 80}}}

# Belum ada earnings sesudah 1 Jun -> harus diam, bukan memvonis melemah.
r = cp.build_checkpoints(tl, now, {"AAA": []}, today=date(2026, 8, 15))
check("tanpa earnings -> belum_ada_data", r["modules"][Q]["belum_ada_data"], 1)
check("tanpa earnings -> nol vonis melemah", r["modules"][Q]["mekanisme_melemah"], 0)

# Earnings 10 Agu + buffer 5 hari = layak dibaca pada 15 Agu.
r = cp.build_checkpoints(tl, now, {"AAA": RH}, today=date(2026, 8, 15))
check("sesudah earnings -> melemah terhitung", r["modules"][Q]["mekanisme_melemah"], 1)
check("lensa lain dinilai terpisah", r["modules"][M]["mekanisme_bertahan"], 1)
check("contoh melemah menyebut tickernya", r["modules"][Q]["contoh_melemah"][0]["ticker"], "AAA")

# Buffer belum lewat (earnings 10 Agu, hari ini 13 Agu) -> masih diam.
r = cp.build_checkpoints(tl, now, {"AAA": RH}, today=date(2026, 8, 13))
check("buffer belum lewat -> belum_ada_data", r["modules"][Q]["belum_ada_data"], 1)

section("6. Spekulatif sengaja TIDAK ikut checkpoint")
check("modul yang dicek", list(cp.CHECKPOINT_MODULES), ["multibagger", "quality_compound"])
r = cp.build_checkpoints(tl, now, {"AAA": RH}, today=date(2026, 8, 15))
check("tidak ada blok speculative", "speculative" in r["modules"], False)

print(f"\n{_passed} pemeriksaan lolos.")
