"""Sel ACTION_TABLE mana yang MUSTAHIL tercapai — penjaga terhadap gerbang
yang kebetulan saling meniadakan.

Kenapa ini ada, dan kenapa sebagai skrip yang bisa gagal alih-alih sebagai
catatan di dokumen: proyek ini sudah empat kali kena kelas kesalahan yang sama
persis — dua ambang yang kebetulan bernilai sama membuat satu cabang mati
tanpa satu pun error.

  - `confidence.band == "high"` tidak pernah tercapai (2026-07-29), sehingga
    seluruh kolom "high" ACTION_TABLE mustahil dipilih;
  - horizon `lima_tahun` mustahil (2026-07-31), karena kata kunci
    `acceleration_signal` salah;
  - cek streak `"miss"` di risk.py tidak pernah cocok (2026-08-03), sehingga
    komponen momentum selalu 0;
  - dan yang membuat berkas ini ada: `STANCE_STRONG_THRESHOLD` (70) sama
    dengan gerbang tier `_thesis_score_tier` (70), sehingga sel campuran
    ACTION_TABLE mustahil (2026-07-31, diperbaiki sebagian di `402a51d`).

Semuanya baru ketahuan lewat pemeriksaan manual berbulan-bulan kemudian.
Kelasnya sama: dua gerbang yang membaca ANGKA YANG SAMA, jadi kombinasi
tertentu tidak punya nilai yang memenuhinya.

Audit 2026-08-06 (D4) menemukan `402a51d` baru menutup separuhnya. Menaikkan
ambang stance ke 75 memang menghidupkan `[pita tengah][tier high]` (skor
70-74), tapi `[pita atas][tier medium/low]` TETAP mustahil: stance dan tier
dihitung dari bilangan yang sama (`thesis_score` == `score` lensa), dan
75 > 70, jadi `score >= 75` SELALU berarti tier "high".

Skrip ini tidak menghakimi apakah sel mati itu salah — kadang memang wajar
(sel matinya menghasilkan action yang identik dengan kembaran hidupnya, jadi
tidak ada perilaku yang hilang). Yang dilakukannya: menyebutkan semuanya,
memisahkan yang tidak berbahaya dari yang MENGHILANGKAN sebuah action, dan
keluar dengan status != 0 untuk yang kedua.

Pakai:
    python scripts/check_action_table.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaforge.layer2.reasoning import STANCE_STRONG_THRESHOLD  # noqa: E402
from alphaforge.personal.personal_reasoning import (  # noqa: E402
    ACTION_TABLE, _thesis_score_tier,
)

# Ambang stance per modul, PERSIS seperti di reasoning.py. Didaftar di sini
# alih-alih diimpor karena di sana ia tertanam di dalam badan tiap lensa
# (`if score >= ...`), bukan sebagai konstanta bersama. Kalau salah satu
# berubah tanpa yang lain, skrip ini yang paling mungkin kejeblos duluan —
# jadi angkanya ikut dicetak supaya ketidakcocokan kelihatan.
STANCE_BANDS = {
    "quality_compound": [
        (STANCE_STRONG_THRESHOLD, "compounding_kuat"),
        (45.0, "compounding_rapuh"),
        (float("-inf"), "bukan_compounder"),
    ],
    "multibagger": [
        (STANCE_STRONG_THRESHOLD, "ruang_terbuka"),
        (45.0, "ruang_sempit"),
        (float("-inf"), "ruang_tertutup"),
    ],
    # Spekulatif punya dua stance di pita atas yang dipilih oleh ADA/TIDAKNYA
    # katalis, bukan oleh skor — keduanya diperlakukan sebagai hasil yang
    # mungkin untuk pita yang sama.
    "speculative": [
        (60.0, ("asimetri_berkatalis", "asimetri_tanpa_katalis")),
        (float("-inf"), "tanpa_asimetri"),
    ],
}

# Stance "tak terbaca" dipilih oleh jumlah knowledge_gaps, bukan oleh skor,
# jadi seluruh barisnya tercapai di skor berapa pun — dikecualikan.
UNREADABLE_SUFFIX = "tak_terbaca"


def _stances_at(module: str, score: float) -> set[str]:
    for threshold, names in STANCE_BANDS[module]:
        if score >= threshold:
            return set(names) if isinstance(names, tuple) else {names}
    return set()


def reachable_pairs(module: str) -> set[tuple[str, str]]:
    """(stance, tier) yang punya setidaknya satu skor yang menghasilkannya."""
    out: set[tuple[str, str]] = set()
    for i in range(0, 10001):
        score = i / 100.0
        tier = _thesis_score_tier(score)
        for stance in _stances_at(module, score):
            out.add((stance, tier))
    return out


def main() -> int:
    print(f"Ambang stance kuat : {STANCE_STRONG_THRESHOLD}")
    print(f"Gerbang tier 'high': 70 (_thesis_score_tier)")
    print("Keduanya membaca thesis_score yang SAMA.\n")

    harmless: list[str] = []
    harmful: list[str] = []

    for position, modules in ACTION_TABLE.items():
        for module, table in modules.items():
            reach = reachable_pairs(module)
            live_actions = {
                action
                for stance, row in table.items()
                for tier, action in row.items()
                if (stance, tier) in reach or stance.endswith(UNREADABLE_SUFFIX)
            }
            for stance, row in table.items():
                if stance.endswith(UNREADABLE_SUFFIX):
                    continue
                for tier, action in row.items():
                    if (stance, tier) in reach:
                        continue
                    where = f"{position}/{module} [{stance}][{tier}] -> {action!r}"
                    if action in live_actions:
                        harmless.append(f"  {where} (action ini tetap tercapai lewat sel lain)")
                    else:
                        harmful.append(f"  {where}")

    if harmless:
        print(f"SEL MATI TANPA KEHILANGAN ACTION ({len(harmless)}):")
        print("\n".join(sorted(harmless)))
        print()

    if harmful:
        print(f"SEL MATI YANG MENGHILANGKAN ACTION ({len(harmful)}):")
        print("\n".join(sorted(harmful)))
        print(
            "\nSetiap baris di atas berarti sebuah action tidak akan pernah bisa\n"
            "dipilih untuk kombinasi mana pun — tabelnya menjanjikan sesuatu yang\n"
            "aljabar skornya tidak bisa berikan."
        )
        return 1

    print("Tidak ada action yang hilang.")
    if harmless:
        print(
            "\nCatatan: sel mati di atas tidak menghilangkan action, TAPI membuat\n"
            "kolom tingkat jadi inert di stance tersebut. Untuk sisi `holding` itu\n"
            "berarti posisi berstance teratas selalu menerima action eksposur penuh\n"
            "dan tidak pernah sekadar 'tahan' — satu-satunya rem yang tersisa di\n"
            "sana adalah penurunan P4 (band == 'low'). Apakah itu yang diinginkan\n"
            "adalah keputusan kalibrasi, bukan bug yang bisa diperbaiki sepihak\n"
            "(sekelas A7/A8 di docs/AUDIT_LOG.md)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
