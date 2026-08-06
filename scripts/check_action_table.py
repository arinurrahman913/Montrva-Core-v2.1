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

Keputusan pengguna 2026-08-06 atas temuan itu adalah OPSI A: ambangnya
dibiarkan, dan sel matinya diisi dengan apa yang benar-benar terjadi di pita
itu supaya tabelnya berhenti menjanjikan gradasi yang tidak ada. Skrip ini
yang menjaga keputusan itu tetap benar, lewat tiga pemeriksaan:

  1. tiap sel mati isinya harus salah satu action yang BENAR-BENAR tercapai
     di baris stance-nya. Ini inti opsi A: tabel tidak boleh memuat action
     yang aljabar skornya tidak bisa hasilkan untuk stance itu.
  2. tidak ada action yang hilang total dari sebuah sel (position, module) —
     kalau sel mati menelan satu-satunya tempat sebuah action bisa muncul,
     yang hilang adalah perilaku, bukan cuma teks.
  3. tidak ada sel HIDUP di kolom "low" yang berisi action ACTION_FULL_
     EXPOSURE. Dulu ini terbaca langsung dari teks tabel (kolom "low" memang
     tidak pernah berisi action penuh), tapi opsi A menaruh `akumulasi`/
     `tambah` di kolom "low" pita atas — sel yang tidak bisa menyala. Sifat
     yang sebenarnya penting cuma berlaku untuk sel yang bisa menyala, dan
     itu yang diperiksa di sini.

Ketiganya membuat skrip keluar dengan status != 0.

Pakai:
    python scripts/check_action_table.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alphaforge.layer2.reasoning import STANCE_STRONG_THRESHOLD  # noqa: E402
from alphaforge.personal.personal_contracts import ACTION_FULL_EXPOSURE  # noqa: E402
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

    dead: list[str] = []
    phantom: list[str] = []   # (1) sel mati menjanjikan action di luar barisnya
    lost: list[str] = []      # (2) action hilang total dari sel ini
    p4: list[str] = []        # (3) sel hidup kolom "low" berisi eksposur penuh

    for position, modules in ACTION_TABLE.items():
        for module, table in modules.items():
            reach = reachable_pairs(module)

            def is_live(stance: str, tier: str) -> bool:
                return (stance, tier) in reach or stance.endswith(UNREADABLE_SUFFIX)

            live_actions = {
                action
                for stance, row in table.items()
                for tier, action in row.items()
                if is_live(stance, tier)
            }
            for stance, row in table.items():
                live_in_row = {a for t, a in row.items() if is_live(stance, t)}
                for tier, action in row.items():
                    where = f"{position}/{module} [{stance}][{tier}] -> {action!r}"
                    if is_live(stance, tier):
                        if tier == "low" and action in ACTION_FULL_EXPOSURE:
                            p4.append(f"  {where}")
                        continue
                    dead.append(f"  {where}")
                    if action not in live_in_row:
                        phantom.append(
                            f"  {where} — baris ini cuma bisa menghasilkan "
                            f"{sorted(live_in_row)}"
                        )
                    if action not in live_actions:
                        lost.append(f"  {where}")

    if dead:
        print(f"Sel yang tidak bisa tercapai ({len(dead)}) — diisi sesuai pita-nya (opsi A):")
        print("\n".join(sorted(dead)))
        print()

    failed = False
    for label, rows, explain in (
        ("SEL MATI MENJANJIKAN ACTION DI LUAR BARISNYA", phantom,
         "Tabel memuat action yang aljabar skornya tidak bisa hasilkan untuk stance\n"
         "itu. Inilah yang membuat tabel ini terbaca seolah punya gradasi yang tidak\n"
         "ada — isi selnya dengan action yang benar-benar keluar di pita tersebut."),
        ("ACTION HILANG TOTAL", lost,
         "Sebuah action tidak bisa dipilih untuk kombinasi mana pun di sel ini.\n"
         "Yang hilang perilaku, bukan cuma teks."),
        ("P4 BOCOR DI SEL HIDUP", p4,
         "Sel yang BISA menyala menaruh action eksposur penuh di kolom 'low'.\n"
         "Penegakan runtime (downgrade_full_exposure) masih menangkapnya, tapi\n"
         "sifat 'aman by construction' hilang — kembalikan ke action non-penuh."),
    ):
        if rows:
            failed = True
            print(f"{label} ({len(rows)}):")
            print("\n".join(sorted(rows)))
            print(f"\n{explain}\n")

    if failed:
        return 1

    print("Ketiga pemeriksaan lolos: sel mati konsisten dengan pita-nya, nol action")
    print("hilang, nol kebocoran P4 di sel hidup.")
    print(
        "\nCatatan tetap (opsi A, keputusan pengguna 2026-08-06): kolom tingkat\n"
        "memang inert di pita atas. Untuk sisi `holding` itu berarti posisi\n"
        "berstance teratas selalu menerima action penambah eksposur, dan yang\n"
        "menahannya cuma P4 (band == 'low'), bukan kekuatan tesisnya. Memberi pita\n"
        "atas rem kedua yang independen dari skor adalah keputusan kalibrasi\n"
        "sekelas A7/A8 di docs/AUDIT_LOG.md — bukan bug."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
