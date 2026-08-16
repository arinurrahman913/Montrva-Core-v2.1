"""Checkpoint antara — menguji MEKANISME tesis, bukan tesisnya.

Masalah yang dijawab modul ini: tesis Multibagger jatuh tempo 365-1.825 hari,
Quality/Compound 96%-nya 1.825 hari. Vonis pertama untuk keduanya baru mungkin
ada 2027-07-27 dan 2028-07-28 (diukur 15 Agu 2026). Sampai saat itu rapor
kalibrasi berisi SATU lensa dari tiga, dan syarat ketiga gerbang penyetelan
mekanis ("minimal satu lensa non-spekulatif sudah punya tesis yang jatuh
tempo") mustahil terpenuhi.

Yang jatuh tempo bertahun-tahun itu HARGANYA. Tapi klaim mekanismenya — margin
bertahan, leverage waras, ruang tumbuh masih terbaca — diperbarui tiap kuartal.
Checkpoint menanyakan itu, dan HANYA itu: apakah dasar yang membuat sistem
mengambil sikap masih berdiri?

LIMA PEMBATAS YANG MEMBENTUK MODUL INI:

1. PEMICUNYA TANGGAL EARNINGS, BUKAN HITUNGAN HARI. Checkpoint yang jatuh
   sebelum laporan baru terbit membaca angka yang sama persis dengan saat
   tesisnya dibuat — ia tidak mengukur apa pun. Pemicunya `resolved_history`
   di CatalystSet (1.685 earnings selesai per 15 Agu 2026), pola yang sama
   dengan `horizon_anchor` yang sudah terbukti di Spekulatif.

2. YANG DIBANDINGKAN KELUARAN LENSA ITU SENDIRI. Tidak ada daftar "metrik
   penting per lensa" yang ditulis tangan di sini: stance dan thesis_score
   sudah merupakan ringkasan lensa atas seluruh faktornya. Kalau bobot lensa
   berubah nanti, checkpoint ikut berubah sendiri — tidak ada salinan aturan
   yang bisa menyimpang diam-diam.

3. KOSAKATANYA SENGAJA TIDAK BISA DIJUMLAHKAN DENGAN HIT RATE. Bukan
   terbukti/meleset. Pelajaran `claim_type` masih segar: begitu dua penggaris
   memakai kata yang sama, seseorang akan menjumlahkannya jadi satu angka yang
   tidak berarti apa-apa. Modul ini juga TIDAK menghitung hit rate apa pun.

4. NILAINYA DARI MELEWATI EARNINGS. `annotate_action_streaks` sudah melacak
   stance bertahan berapa run. Tapi bertahan di hari yang datanya tidak berubah
   itu inersia, bukan bukti. Yang informatif: bertahan MELEWATI batas earnings,
   saat masukannya benar-benar diganti.

5. TIDAK MENYENTUH ACTION, THESIS_SCORE, ATAU GERBANG KALIBRASI. Kalau
   checkpoint boleh menggerakkan action, ia jadi mesin skor kedua — persis yang
   dilarang D-04. Dan ia TIDAK membuka gerbang penyetelan: syaratnya berbunyi
   "tesis yang JATUH TEMPO", dan mekanisme yang bertahan satu kuartal tidak
   mengatakan apa pun tentang harga lima tahun.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..layer2.reasoning_contracts import STANCE_VOCAB, UNREADABLE_STANCE

# Kosakata vonis checkpoint. Tidak ada satu pun yang sama dengan kosakata
# outcome tesis (terbukti/meleset/ambigu/tidak_berlaku) — itu disengaja.
CHECKPOINT_VERDICTS = (
    "mekanisme_menguat",
    "mekanisme_bertahan",
    "mekanisme_melemah",
    "tidak_terbaca",
    "belum_ada_data",
)

# Jeda sesudah tanggal earnings sebelum checkpoint dianggap layak dibaca.
# Sama nilainya dengan ANCHOR_BUFFER_DAYS di personal_historical: harga & data
# fundamental butuh beberapa hari untuk benar-benar masuk ke pipeline.
EARNINGS_BUFFER_DAYS = 5

# Lensa yang punya masalah horizon panjang. Spekulatif SENGAJA tidak ikut: ia
# sudah dinilai penuh dalam median 80 hari lewat horizon_anchor, jadi ia tidak
# butuh checkpoint antara — menambahkannya cuma akan menghasilkan angka kedua
# yang menjelaskan hal yang sudah dijawab vonis aslinya.
CHECKPOINT_MODULES = ("multibagger", "quality_compound")


def _rank(module: str, stance: str | None) -> int | None:
    """Peringkat stance: 0 = paling kuat. None kalau tak terbaca / tak dikenal.

    Diambil dari urutan STANCE_VOCAB apa adanya — tuple-nya memang sudah
    tersusun kuat -> lemah -> tak_terbaca (lihat reasoning_contracts.py).
    Menulis ulang urutannya di sini akan menciptakan salinan kedua yang bisa
    menyimpang saat kosakata berubah."""
    if not stance:
        return None
    vocab = STANCE_VOCAB.get(module)
    if not vocab or stance not in vocab or stance == UNREADABLE_STANCE.get(module):
        return None
    return vocab.index(stance)


def earnings_since(resolved_history: list[dict] | None, since: date) -> date | None:
    """Tanggal earnings SELESAI paling awal sesudah `since`, atau None.

    `cancelled` dibuang: katalis yang batal berarti laporannya TIDAK terbit,
    jadi tidak ada data baru — memperlakukannya sebagai batas yang terlewati
    akan membuat checkpoint membandingkan dua pembacaan atas angka yang sama."""
    best: date | None = None
    for r in resolved_history or []:
        if r.get("kind") != "earnings" or r.get("lifecycle_status") != "completed":
            continue
        raw = r.get("expected_at")
        if not raw:
            continue
        try:
            d = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if d > since and (best is None or d < best):
            best = d
    return best


def _latest_earnings_before(resolved_history: list[dict] | None, cutoff: date) -> date | None:
    """Earnings SELESAI paling akhir yang tanggalnya <= cutoff.

    Beda dari earnings_since(): yang dicari di sini batas data TERBARU yang
    sudah pasti masuk pipeline, bukan yang pertama sesudah sebuah tesis."""
    best: date | None = None
    for r in resolved_history or []:
        if r.get("kind") != "earnings" or r.get("lifecycle_status") != "completed":
            continue
        try:
            d = date.fromisoformat(str(r.get("expected_at"))[:10])
        except (ValueError, TypeError):
            continue
        if d <= cutoff and (best is None or d > best):
            best = d
    return best


def classify(
    module: str,
    stance_before: str | None,
    score_before: float | None,
    stance_now: str | None,
    score_now: float | None,
) -> str:
    """Vonis checkpoint dari dua pembacaan lensa yang sama.

    Stance diperiksa DULU dan tier skor cuma jadi pemecah saat stance-nya sama:
    stance adalah kesimpulan lensa, skor cuma bahan mentahnya. Tesis yang turun
    dari `compounding_kuat` ke `compounding_rapuh` sudah melemah, berapa pun
    skornya bergeser."""
    from .personal_reasoning import _thesis_score_tier

    if stance_now is None or _rank(module, stance_now) is None:
        return "tidak_terbaca"
    r_before, r_now = _rank(module, stance_before), _rank(module, stance_now)
    if r_before is None:
        # Dulu tak terbaca, sekarang terbaca — datanya membaik, tapi itu
        # pernyataan tentang KELENGKAPAN DATA, bukan tentang mekanismenya.
        return "mekanisme_bertahan"
    if r_now < r_before:
        return "mekanisme_menguat"
    if r_now > r_before:
        return "mekanisme_melemah"

    if score_before is None or score_now is None:
        return "mekanisme_bertahan"
    tier_before, tier_now = _thesis_score_tier(score_before), _thesis_score_tier(score_now)
    if tier_before == tier_now:
        return "mekanisme_bertahan"
    order = {"low": 0, "medium": 1, "high": 2}
    return "mekanisme_menguat" if order[tier_now] > order[tier_before] else "mekanisme_melemah"


def build_checkpoints(
    timelines: dict[str, dict],
    current_reasoning: dict[str, dict],
    resolved_by_ticker: dict[str, list[dict]],
    today: date | None = None,
) -> dict:
    """Ringkasan checkpoint seluruh populasi.

    Mengembalikan RINGKASAN, bukan baris per ticker: hasilnya menumpang di
    calibration.json (30 KB) dan personal_history.json sudah 267 MB — berkas itu
    sudah dua kali membunuh backend minggu ini dan tidak boleh ditambahi lagi.

    `current_reasoning`: {ticker: ReasoningBundle-as-dict} dari run ini.
    `resolved_by_ticker`: {ticker: CatalystSet.resolved_history}.
    """
    today = today or date.today()
    tally = {m: {v: 0 for v in CHECKPOINT_VERDICTS} for m in CHECKPOINT_MODULES}
    diperiksa = {m: 0 for m in CHECKPOINT_MODULES}
    contoh: dict[str, list[dict]] = {m: [] for m in CHECKPOINT_MODULES}

    for ticker, tl in timelines.items():
        entries = tl.get("entries") if isinstance(tl, dict) else None
        if not entries:
            continue
        now_bundle = current_reasoning.get(ticker) or {}
        dated = []
        for e in entries:
            try:
                dated.append((date.fromisoformat(str(e.get("analyzed_at"))[:10]), e))
            except (ValueError, TypeError):
                continue
        if not dated:
            continue
        dated.sort(key=lambda p: p[0])

        # Earnings TERAKHIR yang sudah terbit & lewat buffer — itu batas data
        # yang ingin diapit.
        earnings = _latest_earnings_before(
            resolved_by_ticker.get(ticker), today - timedelta(days=EARNINGS_BUFFER_DAYS),
        )
        if earnings is None:
            before_entry, entry_date = None, None
        else:
            # Titik banding = snapshot TERAKHIR SEBELUM earnings itu. Memakai
            # snapshot pertama seumur hidup (versi awal modul ini) membuat
            # perubahan stance yang terjadi berbulan-bulan lalu diatribusikan
            # ke earnings terakhir — AAPL tercatat "ruang_terbuka ->
            # ruang_sempit (earnings 2026-07-30)" padahal pergeserannya bisa
            # terjadi jauh sebelum laporan itu. Mengapit peristiwanya adalah
            # satu-satunya cara kalimat "sesudah earnings" itu jujur.
            kandidat = [(d, e) for d, e in dated if d <= earnings]
            before_entry, entry_date = (kandidat[-1][1], kandidat[-1][0]) if kandidat else (None, None)
        calls = (before_entry or {}).get("personal_call_set") or {}

        for m in CHECKPOINT_MODULES:
            before = calls.get(m) or {}
            now = now_bundle.get(m) or {}
            if not now:
                continue
            diperiksa[m] += 1
            if earnings is None or before_entry is None:
                # Tidak ada laporan baru sejak kita mengamati ticker ini, atau
                # kita baru mulai mengamatinya SESUDAH laporan terakhir — dua-
                # duanya berarti tidak ada apa pun untuk dibandingkan.
                tally[m]["belum_ada_data"] += 1
                continue
            v = classify(
                m, before.get("source_stance"), before.get("thesis_score"),
                now.get("stance"), now.get("thesis_score"),
            )
            tally[m][v] += 1
            if v == "mekanisme_melemah" and len(contoh[m]) < 5:
                contoh[m].append({
                    "ticker": ticker,
                    "sejak": entry_date.isoformat() if entry_date else None,
                    "earnings": earnings.isoformat(),
                    "stance": f"{before.get('source_stance')} -> {now.get('stance')}",
                    "skor": [before.get("thesis_score"), now.get("thesis_score")],
                })

    return {
        "method": "membandingkan stance & tingkat skor lensa yang sama, "
                  "dipicu earnings yang sudah terbit sejak tesis dibuat",
        "buffer_days": EARNINGS_BUFFER_DAYS,
        "modules": {
            m: {"diperiksa": diperiksa[m], **tally[m], "contoh_melemah": contoh[m]}
            for m in CHECKPOINT_MODULES
        },
        # Ditulis di dalam datanya, bukan cuma di docstring: siapa pun yang
        # membaca berkas ini tanpa membuka kode harus tahu batasnya.
        "catatan": (
            "Checkpoint menguji MEKANISME (apakah dasar sikapnya masih berdiri), "
            "BUKAN tesisnya (harga, jatuh tempo 1-5 tahun). Tidak menyentuh action "
            "maupun thesis_score, dan TIDAK membuka gerbang penyetelan mekanis — "
            "syarat gerbang itu 'tesis yang jatuh tempo', dan mekanisme yang "
            "bertahan satu kuartal tidak mengatakan apa pun tentang harga lima tahun."
        ),
    }
