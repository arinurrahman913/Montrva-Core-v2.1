"""Evaluasi outcome PersonalCall — lanjutan draft §12, disetujui pengguna
2026-07-27 secara eksplisit: berbeda dari Historical publik (outcome sengaja
None selamanya sampai v2.1), lapisan pribadi ini MEMANG dirancang untuk satu
orang yang menerima risiko salah menilai sendiri — jadi outcome dihitung
mekanis begitu call jatuh tempo, bukan ditunda tanpa batas.

Cuma action berklaim arah yang dievaluasi (masuk = klaim harga naik, keluar
= klaim harga turun/flat). Action tanpa klaim arah (pantau/tahan/tunggu_
katalis/lewati) diberi outcome "tidak_berlaku", bukan dipaksa punya
nilai terbukti/meleset yang sebenarnya tidak berarti apa-apa untuk mereka.

Lima keputusan penting di modul ini (hasil audit 2026-07-27):

1. JATUH TEMPO DIHITUNG PER LENS, BUKAN PER ENTRY. Versi sebelumnya
   mengevaluasi SEMUA lens begitu SALAH SATU lens lewat horizon — jadi call
   Speculative yang jatuh tempo di hari ke-28 ikut memvonis call Multibagger
   (365 hari) dan Quality (1825 hari) di hari ke-28 juga, lalu membekukannya
   selamanya karena outcome yang sudah terisi tidak pernah dihitung ulang.

2. DIEVALUASI PER STREAK (satu tesis), BUKAN PER ENTRY HARIAN. Satu tesis
   yang bertahan 200 hari menghasilkan 200 snapshot harian; entry mana pun
   yang independen jatuh tempo di tanggalnya sendiri berarti tesis yang sama
   bisa "dievaluasi" berkali-kali dengan current_price yang beda-beda tiap
   kali. `_find_streaks` mengelompokkan entry berturutan dengan action sama
   jadi satu unit, dievaluasi TEPAT SEKALI (dari tanggal mulai streak), lalu
   hasilnya ditulis SAMA ke semua entry dalam streak itu — baris Riwayat mana
   pun yang dilihat pengguna untuk tesis ini menunjukkan verdict yang sama.

3. BASELINE HARGA DIAMBIL DARI `price_at_call` YANG TERSIMPAN. Rekonstruksi
   dari price_history cuma dipakai untuk entry lama yang belum punya field
   itu — dan price_history cuma menyimpan 1 tahun, jadi untuk horizon panjang
   rekonstruksi itu diam-diam memakai bar tertua yang tersedia sebagai
   "harga entry". `baseline` di hasil menandai mana yang dipakai.

4. ADA PEMBANDING INDEKS. return mentah saja tidak bisa dibaca: naik 3% saat
   indeks naik 8% itu tertinggal, bukan berhasil. Klasifikasi terbukti/meleset
   tetap memakai threshold absolut (itu yang dijanjikan ke pengguna), tapi
   `excess_return_pct` disimpan berdampingan supaya bisa dibaca jujur.

5. ADA `thesis_key`. Sama untuk semua entry dalam satu streak — dipakai
   frontend untuk menghitung "berapa tesis yang terbukti/meleset" (bukan
   "berapa baris"), supaya tesis yang lama dipegang tidak mendominasi
   statistik track record cuma karena snapshot-nya lebih banyak.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from .personal_contracts import ACTION_CATEGORY_EXIT
from .personal_historical import call_due_date

if TYPE_CHECKING:
    from ..layer2.contracts import EvidencePackage

MODULES = ("multibagger", "quality_compound", "speculative")

# Semua action "masuk" (penuh maupun bertahap) -- klaim arahnya sama: harga
# naik. Lihat ACTION_CATEGORY_EXIT di personal_contracts.py untuk sisi lain.
ACTION_CATEGORY_ENTRY = frozenset({
    "mulai_posisi", "cicil_bertahap", "akumulasi", "akumulasi_saat_koreksi",
    "masuk_spekulatif", "tambah", "tambah_bertahap",
})

# Disetujui pengguna 2026-07-27: makin panjang horizon, makin besar target
# return-nya -- horizon pendek tidak boleh "menang" cuma karena noise harian.
HORIZON_OUTCOME_THRESHOLD_PCT = {
    "mingguan": 3.0,
    "bulanan": 5.0,
    "enam_bulan": 10.0,
    "satu_dua_tahun": 15.0,
    "lima_tahun": 30.0,
}


def _price_return_pct(start_price: float | None, current_price: float | None) -> float | None:
    if not start_price or current_price is None:
        return None
    return (current_price - start_price) / start_price * 100.0


# Sama seperti _ANCHOR_TOLERANCE_DAYS di knowledge_helpers.py (dipakai
# _find_price_on_or_before untuk anchor return_3y/return_5y) -- disamakan
# supaya toleransi "harga cukup dekat dengan tanggal target" konsisten di
# seluruh codebase.
_RECONSTRUCT_TOLERANCE_DAYS = 45


def _reconstruct_start_price(price_history: list[dict] | None, since_date: str) -> float | None:
    """Fallback untuk entry lama yang belum menyimpan `price_at_call`.

    price_history cuma memuat ~1 tahun HARIAN penuh; sisanya (tahun ke-2..5)
    satu bar per bulan kalender (_downsample_price_history di
    yahoo_evidence.py) -- bar TIDAK berjarak seragam.

    Audit item B11: versi sebelumnya mengambil bar PERTAMA yang tanggalnya
    >= since_date. Untuk call berumur 18 bulan (jatuh di wilayah bulanan),
    itu bisa jadi bar AKHIR BULAN yang meleset sampai ~30 hari dari tanggal
    call sebenarnya -- dan hasilnya langsung memberi makan klasifikasi
    terbukti/meleset (vonis biner atas satu tesis, bukan sekadar metrik
    pendukung seperti return_3y/5y). Sekarang mencari bar TERDEKAT ke
    since_date (arah mana pun, bukan cuma sesudahnya), dan menyerah (None)
    kalau bar terdekat pun masih lebih dari _RECONSTRUCT_TOLERANCE_DAYS —
    pemanggil sudah menangani None dengan benar (classification jadi None,
    entry dibiarkan pending untuk dicoba lagi run berikutnya), jauh lebih
    aman daripada diam-diam memvonis pakai harga yang salah tanggal.
    """
    if not price_history:
        return None
    try:
        target = date.fromisoformat(since_date)
    except ValueError:
        return None
    best_gap = None
    best_close = None
    for b in price_history:
        b_date = b.get("date")
        if not b_date:
            continue
        try:
            gap = abs((date.fromisoformat(b_date) - target).days)
        except ValueError:
            continue
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best_close = b.get("close")
    if best_gap is None or best_gap > _RECONSTRUCT_TOLERANCE_DAYS:
        return None
    return best_close


def _last_bar_date(price_history: list[dict] | None) -> str | None:
    """Tanggal bar terakhir yang tersedia -- dipakai sebagai tanggal harga
    penutup yang benar-benar dipakai evaluasi. price_history sudah dinormalkan
    ke dict oleh pemanggil."""
    if not price_history:
        return None
    dates = [b.get("date") for b in price_history if b.get("date")]
    return max(dates)[:10] if dates else None


def _classify(action: str, horizon: str, return_pct: float | None) -> str | None:
    if action not in ACTION_CATEGORY_ENTRY and action not in ACTION_CATEGORY_EXIT:
        return "tidak_berlaku"  # action tanpa klaim arah -- tidak dievaluasi
    if return_pct is None:
        return None  # data harga belum cukup, coba lagi run berikutnya
    threshold = HORIZON_OUTCOME_THRESHOLD_PCT.get(horizon)
    if threshold is None:
        return None
    if action in ACTION_CATEGORY_ENTRY:
        return "terbukti" if return_pct >= threshold else "meleset"
    # ACTION_CATEGORY_EXIT: klaim "hindari turun" -- naik kecil (0..threshold)
    # belum cukup jelas buat divonis, biarkan "ambigu" daripada dipaksa biner.
    if return_pct <= 0:
        return "terbukti"
    return "meleset" if return_pct >= threshold else "ambigu"


def _sorted_entries(entries: list[dict]) -> list[dict]:
    return sorted(entries, key=lambda e: e.get("analyzed_at", ""))


def _find_streaks(entries: list[dict], module: str) -> list[list[dict]]:
    """Kelompokkan entry (sudah terurut tanggal) jadi rentetan-rentetan
    berturutan yang action-nya sama untuk `module` ini. Satu rentetan = SATU
    tesis (§12) -- ini unit yang dievaluasi, BUKAN entry harian satu-satu.

    Tanpa pengelompokan ini, satu tesis yang bertahan 200 hari akan tampak
    seperti dievaluasi 200 kali secara independen (masing-masing entry
    hariannya sendiri jatuh tempo di tanggalnya sendiri-sendiri) -- statistik
    track record jadi didominasi tesis yang lama dipegang, dan tiap salinan
    bisa menghasilkan angka return_pct yang sedikit beda karena current_price
    ikut berubah tiap hari evaluasi berjalan.
    """
    ordered = _sorted_entries(entries)
    streaks: list[list[dict]] = []
    current: list[dict] = []
    current_action: str | None = None
    for e in ordered:
        call = (e.get("personal_call_set") or {}).get(module)
        action = call.get("action") if call else None
        if action is None:
            if current:
                streaks.append(current)
                current, current_action = [], None
            continue
        if action != current_action:
            if current:
                streaks.append(current)
            current, current_action = [], action
        current.append(e)
    if current:
        streaks.append(current)
    return streaks


def evaluate_due_entries(
    timelines: dict[str, dict],
    evidence_by_ticker: dict[str, "EvidencePackage"],
    today: date | None = None,
    benchmark_price_now: float | None = None,
) -> int:
    """Isi entry["outcome"][module] untuk tiap LENS yang sudah jatuh tempo dan
    belum dievaluasi. Mengubah `timelines` in-place (dict plain, sama konvensi
    dengan personal_historical.py). Return jumlah lens yang baru dievaluasi
    pass ini, buat logging."""
    today = today or datetime.now(timezone.utc).date()
    evaluated_count = 0

    for ticker, timeline in timelines.items():
        entries = timeline.get("entries", [])
        evidence = evidence_by_ticker.get(ticker)
        price_history = evidence.price_market.price_history if evidence else None
        current_price = evidence.price_market.last_price if evidence else None
        if price_history and not isinstance(price_history[0], dict):
            price_history = [
                b if isinstance(b, dict) else b.__dict__ for b in price_history
            ]

        for module in MODULES:
            for streak in _find_streaks(entries, module):
                start_entry = streak[0]
                # Sudah pernah dievaluasi (entry mana pun di streak ini yang
                # sudah punya outcome buat modul ini menandai SELURUH streak
                # sudah divonis -- lihat docstring _find_streaks) -- lompat.
                if any((e.get("outcome") or {}).get(module) for e in streak):
                    continue

                since = start_entry.get("analyzed_at") or ""
                start_call = (start_entry.get("personal_call_set") or {}).get(module) or {}
                if not start_call:
                    continue

                due = call_due_date(start_call, since)
                if due is None or today <= due:
                    continue  # tesis ini belum jatuh tempo

                start_price = start_call.get("price_at_call")
                baseline = "price_at_call"
                if start_price is None:
                    start_price = _reconstruct_start_price(price_history, since[:10])
                    baseline = "price_history"

                return_pct = _price_return_pct(start_price, current_price)
                bench_start = start_call.get("benchmark_at_call")
                bench_return = _price_return_pct(bench_start, benchmark_price_now)
                excess = None
                if return_pct is not None and bench_return is not None:
                    excess = return_pct - bench_return

                classification = _classify(start_call["action"], start_call["horizon"], return_pct)
                if classification is None:
                    continue  # harga belum bisa dibaca — biarkan pending, coba lagi run berikutnya

                threshold_pct = HORIZON_OUTCOME_THRESHOLD_PCT.get(start_call["horizon"])
                outcome_record = {
                    "classification": classification,
                    "return_pct": round(return_pct, 2) if return_pct is not None else None,
                    "benchmark_return_pct": round(bench_return, 2) if bench_return is not None else None,
                    "excess_return_pct": round(excess, 2) if excess is not None else None,
                    "threshold_pct": threshold_pct,
                    "baseline": baseline,
                    "due_at": due.isoformat(),
                    # Angka-angka DI BAWAH INI sudah dipegang fungsi ini sejak
                    # awal untuk menghitung return_pct, cuma tidak pernah
                    # disimpan -- akibatnya Riwayat Pribadi cuma bisa
                    # menampilkan "+9.37%" tanpa harga entry, target, harga
                    # penutup yang dipakai, atau tanggalnya (temuan pengguna
                    # 2026-08-03). Menyimpannya nol biaya: tidak ada fetch
                    # baru, tidak ada perhitungan ulang.
                    "entry_price": round(start_price, 4) if start_price is not None else None,
                    "entry_date": since[:10],
                    "exit_price": round(current_price, 4) if current_price is not None else None,
                    # Tanggal bar terakhir yang tersedia saat evaluasi -- BUKAN
                    # due_at. Keduanya sering beda dan itu bukan bug: BE jatuh
                    # tempo Minggu 2 Agu, harga yang dipakai tutupan Jumat 31
                    # Jul. Menulisnya sebagai "close 2 Agu" akan jadi klaim
                    # palsu, jadi tanggalnya dibawa apa adanya.
                    "exit_date": _last_bar_date(price_history),
                    # Target harga cuma bermakna untuk action berklaim NAIK.
                    # Untuk action keluar, threshold justru batas "tidak boleh
                    # naik lebih dari" -- beda arti, jadi sengaja None daripada
                    # dipaksa jadi angka yang menyesatkan.
                    "target_price": (
                        round(start_price * (1 + threshold_pct / 100.0), 4)
                        if start_price is not None and threshold_pct is not None
                        and start_call["action"] in ACTION_CATEGORY_ENTRY
                        else None
                    ),
                    "horizon_days": (due - date.fromisoformat(since[:10])).days,
                    # Berapa hari evaluasi tertinggal dari jatuh tempo (0 kalau
                    # tepat hari itu). Bukan hiasan: makin besar, makin jauh
                    # harga yang dipakai dari harga saat tesis benar-benar
                    # jatuh tempo.
                    "evaluation_lag_days": (today - due).days,
                    # Satu tesis = satu (modul, tanggal mulai streak). SATU
                    # evaluasi ini ditulis ke SETIAP entry harian dalam streak
                    # yang sama (bukan cuma entry terakhir) supaya baris mana
                    # pun di Riwayat yang dilihat pengguna untuk tesis ini
                    # menunjukkan verdict yang sama, konsisten.
                    "thesis_key": f"{module}:{since[:10]}",
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
                for e in streak:
                    outcome = e.get("outcome") or {}
                    outcome[module] = outcome_record
                    e["outcome"] = outcome
                evaluated_count += 1  # dihitung per TESIS, bukan per entry

    return evaluated_count
