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

   REVISI 2026-08-04: keputusan ini selama ini cuma ada di dokumen. Diukur
   atas riwayat live: `excess_return_pct` null di **167 dari 167** tesis
   berarah, dan `benchmark_at_call` kosong di **4.050 dari 4.050** call di run
   terakhir — jadi pertanyaan "tesisnya yang salah atau pasarnya yang turun"
   (satu-satunya pertanyaan yang bikin riwayat ini berguna sebagai pelajaran)
   secara struktural tidak bisa dijawab. Dua penyebabnya diperbaiki di sini:

   (a) `benchmark_at_call` bergantung sepenuhnya pada `layer1_pkg.spx_history`
       yang kebetulan terisi saat call dibuat. Sekarang ada jalur kedua:
       deret penutupan indeks yang menumpuk lintas run (layer1/
       benchmark_history.py), dipakai untuk MENCARI harga indeks pada tanggal
       masuk kalau call-nya sendiri tidak menyimpannya. Ini juga yang membuat
       backfill outcome lama mungkin.

   (b) sisi keluarnya dulu memakai `benchmark_price_now` — harga indeks pada
       HARI EVALUASI, dibandingkan dengan harga saham pada `exit_date` (bar
       lengkap terakhir, sering beberapa hari lebih awal). Dua tanggal
       berbeda, jadi selisihnya bukan excess return, melainkan excess return
       plus gerak indeks di sela itu. Sekarang keduanya dibaca pada
       `exit_date` yang sama; `benchmark_price_now` cuma cadangan terakhir
       dan ditandai lewat `benchmark_source`.

   REVISI 2026-08-06 (audit D3): (b) di atas baru menyelesaikan sisi INDEKS.
   Sisi sahamnya masih memakai `price_market.last_price` — kuotasi HIDUP, bisa
   dari sesi yang belum selesai — sementara `exit_date` menunjuk bar lengkap
   terakhir, yang sejak `09e1eff` (bar tak lengkap dibuang) rutin satu sesi
   lebih awal. Jadi ketidakcocokan tanggal yang sama masih ada, cuma pindah
   sisi; dan sejak vonis v2, excess adalah PEMBILANG z, jadi kesalahan itu
   ikut mengalir ke vonis. Sekarang harga keluar diambil dari BARIS yang sama
   dengan `exit_date` (lihat `_last_complete_bar`), sehingga saham dan indeks
   benar-benar dibaca pada tanggal yang sama, dan `exit_price` yang disimpan
   memang penutupan pada `exit_date` di sebelahnya.

5. ADA `thesis_key`. Sama untuk semua entry dalam satu streak — dipakai
   frontend untuk menghitung "berapa tesis yang terbukti/meleset" (bukan
   "berapa baris"), supaya tesis yang lama dipegang tidak mendominasi
   statistik track record cuma karena snapshot-nya lebih banyak.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from ..layer1.benchmark_history import benchmark_close_on_or_before
from .personal_contracts import ACTION_CATEGORY_EXIT, ACTION_CATEGORY_MAGNITUDE
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


def _last_complete_bar(price_history: list[dict] | None) -> tuple[str | None, float | None]:
    """(tanggal, penutupan) bar lengkap terakhir — SATU pasang, dari baris yang
    sama.

    Audit 2026-08-06 (D3): sisi keluar dulu memakai `price_market.last_price`
    (kuotasi HIDUP, bisa dari sesi yang belum selesai) sementara `exit_date`
    diambil dari bar lengkap terakhir, dan harga indeks dibaca pada `exit_date`
    itu. Sejak `09e1eff` membuang bar tak lengkap dari price_history, keduanya
    rutin berbeda satu sesi. Dua akibatnya: `excess_return_pct` — yang sejak
    vonis v2 jadi PEMBILANG z, bukan lagi sekadar metrik pendukung — kembali
    memuat satu sesi gerak indeks yang bukan hasil tesis, dan record menyimpan
    `exit_price` yang bukan penutupan pada `exit_date` di sebelahnya.

    Mengambil tanggal dan harga dari baris yang sama membuat pasangan itu benar
    by construction, dan menempatkan sisi saham pada tanggal yang sama dengan
    sisi indeks. Ini deret yang sama yang dipakai window_sigma_pct, jadi
    pembilang dan penyebut z akhirnya mengukur jendela yang sama persis.
    """
    if not price_history:
        return None, None
    best_date = None
    best_close = None
    for b in price_history:
        d = b.get("date")
        close = b.get("close")
        if not d or close is None:
            continue
        d = str(d)[:10]
        if best_date is None or d > best_date:
            best_date, best_close = d, close
    return best_date, best_close


def _classify(action: str, horizon: str, return_pct: float | None) -> str | None:
    # Vonis v1 menilai ARAH terhadap target absolut. `siaga_gerakan` tidak
    # mengklaim arah sama sekali, jadi di sini ia "tidak_berlaku" — bukan
    # kegagalan, melainkan pertanyaan yang tidak ia jawab. Penilaiannya ada di
    # classify_v2 (|z| >= 1). Membiarkannya dinilai v1 akan menuduh action ini
    # meleset karena tidak memenuhi janji yang tidak pernah ia buat.
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


# Sebab meleset — kosakata tertutup, diturunkan MEKANIS dari angka yang sudah
# tersimpan (return, threshold, excess). Nol data baru, nol fetch, nol
# penilaian bebas.
#
# Kenapa perlu: "MELESET" saja menyatukan tiga kegagalan yang tuntutan
# perbaikannya berlawanan. Tesis yang naik 2,4% terhadap target 3% (arah
# benar, target ketinggian) menuntut peninjauan THRESHOLD; tesis yang jatuh
# 8% sementara indeks naik menuntut peninjauan SELEKSI. Digabung jadi satu
# angka "hit 29,9%", keduanya tidak bisa dibedakan — dan itu persis yang
# bikin riwayat ini tidak bisa dipakai belajar.
#
# Urutan pemeriksaan penting dan sengaja begini: arah dulu (r >= 0), BARU
# pembanding indeks. Kalau dibalik, tesis yang naik 2% saat indeks naik 1%
# akan dilabeli "unggul indeks" padahal yang gagal di situ jelas targetnya,
# bukan pemilihan sahamnya.
MISS_REASONS = ("target_ketinggian", "turun_bareng_pasar", "arah_salah", "harga_justru_naik")


def diagnose_miss(
    action: str, classification: str | None, return_pct: float | None,
    threshold_pct: float | None, excess_pct: float | None,
) -> str | None:
    """Sebab satu tesis meleset. None untuk yang tidak meleset (terbukti/
    ambigu/tidak_berlaku) — field ini bukan label untuk semua outcome, cuma
    untuk yang gagal."""
    if classification != "meleset" or return_pct is None:
        return None
    if action in ACTION_CATEGORY_EXIT:
        # Klaim "harga tidak akan naik" yang dilanggar. Tidak dipecah lebih
        # jauh: naik melewati threshold sudah satu-satunya cara action keluar
        # bisa meleset (lihat _classify), jadi memecahnya cuma akan bikin
        # kategori yang tidak pernah terisi.
        return "harga_justru_naik"
    if return_pct >= 0:
        # Untuk action masuk, "meleset" dengan return POSITIF cuma punya satu
        # arti: naik, tapi tidak sampai threshold (lihat _classify). Sengaja
        # tidak ikut menuntut `threshold_pct is not None` — kalau threshold-nya
        # hilang dari record lama, tesis yang jelas-jelas naik akan jatuh ke
        # cabang terakhir dan dilabeli "arah salah", yang justru berlawanan
        # dengan angkanya sendiri.
        return "target_ketinggian"
    if excess_pct is not None and excess_pct >= 0:
        # Turun, tapi tetap kalah lebih sedikit dari indeks. Bukan pembelaan
        # otomatis — cuma pemisahan antara "salah pilih saham" dan "salah ada
        # di pasar itu sama sekali".
        return "turun_bareng_pasar"
    return "arah_salah"


# --- VONIS v2: gerakan berarti, bukan gerakan biasa -------------------------
#
# Kenapa vonis lama tidak cukup, diukur bukan dikira (2026-08-05):
#
#   - Threshold-nya tetap 3% untuk seluruh ticker, sementara gerakan harga khas
#     di jendela 4-9 hari itu +-5,16%. Targetnya berada DI BAWAH derau, jadi
#     lolos-tidaknya lebih ditentukan ke arah mana derau jatuh daripada oleh
#     benar-salahnya tesis.
#   - Diuji lawan pembanding nol (scripts/measure_baseline.py): hit rate
#     pilihan sistem 32,7% vs saham sembarangan di jendela yang sama 35,7% --
#     selisih -3,1pp dengan selang kepercayaan 95% -8,6..+2,5pp. Nol ada di
#     dalam selang: seleksinya tidak bisa dibedakan dari kebetulan. Angka
#     "hit rate 34,7%" ternyata bukan sinyal, itu base rate.
#   - Threshold tetap juga memperlakukan saham yang bergerak 2%/minggu sama
#     dengan yang bergerak 12%/minggu. Untuk yang kedua, naik 3% bukan
#     prestasi -- itu hari Selasa biasa.
#
# Aturan v2 menormalkan keduanya sekaligus:
#
#     z = (return_saham - return_indeks) / (sigma_harian * sqrt(hari_bursa))
#
# Pembilangnya excess (jadi pasar yang naik tidak lagi dihitung sebagai
# keberhasilan tesis), penyebutnya volatilitas saham ITU SENDIRI (jadi barnya
# ikut naik untuk saham liar). z >= +1 berarti "bergerak naik melebihi derunya
# sendiri, setelah pasar diperhitungkan".
#
# TIGA HAL YANG SENGAJA:
#
# 1. `classification` LAMA TIDAK DIUBAH. v2 ditaruh di sebelahnya. Vonis lama
#    adalah yang dijanjikan sistem saat call itu dibuat; menggantinya di
#    tempat berarti menulis ulang sejarah, dan tidak ada lagi cara memeriksa
#    apakah perubahan aturan ini membantu atau tidak.
# 2. SIGMA DIHITUNG DARI BAR SEBELUM TANGGAL MASUK, tidak pernah dari jendela
#    tesisnya sendiri. Memakai jendela sendiri = mengintip masa depan: saham
#    yang kebetulan bergolak besar di jendela itu akan otomatis dapat bar yang
#    lebih tinggi justru karena gejolaknya.
# 3. ATURAN BARUNYA LEBIH SULIT, BUKAN LEBIH GAMPANG (base rate z>=1 sekitar
#    16%, bukan 36%). Ini penting karena aturannya diubah SETELAH melihat
#    hasil yang jelek -- kalau perubahannya bikin angka membaik, itu menjahit
#    hasil ke masa lalu. Yang diperbaiki ketajaman alat ukurnya, bukan nilainya.
Z_TERBUKTI = 1.0
Z_MELESET = -1.0
SIGMA_LOOKBACK_BARS = 60
SIGMA_MIN_BARS = 20


def _business_days(a: date, b: date) -> int:
    """Hari bursa (Sen-Jum) dari `a` sampai `b`, tidak menghitung `a` sendiri.

    Libur bursa diabaikan (~9 hari/tahun, <4%) — presisi segitu tidak
    menggerakkan vonis |z| >= 1, dan menambah kalender libur berarti dependensi
    baru untuk ketelitian yang tidak dipakai. Dipakai HANYA di wilayah bar
    bulanan; di wilayah harian jumlah bar yang sebenarnya lebih tepat dan
    itu yang dipakai (lihat window_sigma_pct).
    """
    if b <= a:
        return 0
    full_weeks, rem = divmod((b - a).days, 7)
    n = full_weeks * 5
    wd = a.weekday()
    for i in range(1, rem + 1):
        if (wd + i) % 7 < 5:
            n += 1
    return n


def _bar_gap_days(d1: str, d2: str) -> int:
    """Jarak dua bar berurutan dalam hari bursa, minimal 1."""
    try:
        return max(1, _business_days(date.fromisoformat(d1), date.fromisoformat(d2)))
    except ValueError:
        return 1


def window_sigma_pct(
    price_history: list[dict] | None, entry_date: str | None, exit_date: str | None,
) -> float | None:
    """Deru khas saham ini SEPANJANG JENDELA tesis, dalam persen.

    sigma harian dihitung dari maksimal SIGMA_LOOKBACK_BARS bar terakhir yang
    tanggalnya <= entry_date (tidak pernah menyentuh bar di dalam jendela --
    lihat catatan #2 di atas), lalu diskalakan akar-waktu ke panjang jendela.

    JARAK ANTAR-BAR TIDAK BOLEH DIANGGAP SERAGAM (audit 2026-08-06, D5).
    `price_history` di evidence.json harian hanya ~1 tahun terakhir; tahun ke-2
    s/d ke-5 satu bar per BULAN (`_downsample_price_history`). Versi pertama
    fungsi ini memperlakukan seluruh deret sebagai bar harian, jadi untuk tesis
    berumur > 1 tahun: `prior` seluruhnya bar bulanan sehingga "sigma harian"
    yang dihitung sebenarnya sigma bulanan (~sqrt(21) kali terlalu besar),
    sementara jendelanya dihitung dalam bar HARIAN (~252/tahun). Untuk tesis
    Multibagger 730 hari itu melebihkan sigma sekitar 3x, mengecilkan z 3x, dan
    mendaratkan hampir semua vonis di "ambigu" — aktif tepat pada vonis PERTAMA
    lensa jangka panjang (2027), saat tidak ada lagi pembanding untuk
    menyadarinya.

    Dua penawarnya, keduanya menjaga jalur harian tetap PERSIS seperti semula
    (bar berurutan berjarak 1 hari bursa -> pembagi sqrt(1) -> tidak berubah,
    jadi vonis v2 yang sudah tertulis tidak bergeser):

      1. tiap return dinormalkan ke setara-harian dengan membaginya sqrt(jarak
         bar dalam hari bursa). Bar bulanan menghasilkan sigma bulanan/sqrt(21),
         yang memang penaksir sigma harian.
      2. panjang jendela memakai jumlah bar kalau deret di dalamnya masih
         harian (jumlah bar itu ukuran yang PERSIS, libur bursa terhitung
         benar), dan jatuh ke hitungan hari bursa kalau tidak — di wilayah
         bulanan satu bar tidak lagi mewakili satu hari.

    None kalau bar historisnya kurang dari SIGMA_MIN_BARS -- pemanggil
    menuliskannya sebagai vonis v2 yang kosong, bukan menebak.
    """
    if not price_history or not entry_date or not exit_date:
        return None
    bars = sorted(
        ((str(b.get("date"))[:10], b.get("close")) for b in price_history
         if b.get("date") and b.get("close")),
        key=lambda x: x[0],
    )
    if not bars:
        return None

    entry, exit_ = entry_date[:10], exit_date[:10]
    prior = [(d, c) for d, c in bars if d <= entry][-SIGMA_LOOKBACK_BARS:]
    if len(prior) < SIGMA_MIN_BARS:
        return None
    rets = [
        (prior[i][1] - prior[i - 1][1]) / prior[i - 1][1] * 100.0
        / (_bar_gap_days(prior[i - 1][0], prior[i][0]) ** 0.5)
        for i in range(1, len(prior)) if prior[i - 1][1]
    ]
    if len(rets) < SIGMA_MIN_BARS - 1:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sigma_daily = var ** 0.5
    if sigma_daily <= 0:
        return None

    n_bars = sum(1 for d, _ in bars if entry < d <= exit_)
    try:
        n_business = _business_days(date.fromisoformat(entry), date.fromisoformat(exit_))
    except ValueError:
        n_business = n_bars
    # Deret di dalam jendela masih harian kalau jumlah barnya mendekati jumlah
    # hari bursa; kalau jauh lebih sedikit, jendelanya jatuh di wilayah bulanan.
    n = n_bars if n_bars >= n_business * 0.8 else n_business
    if n < 1:
        return None
    return sigma_daily * (n ** 0.5)


def claim_type(action: str) -> str:
    """Apa yang sebenarnya DIKLAIM action ini — penentu bagaimana ia dinilai.

    Wajib disimpan berdampingan dengan outcome: sesudah `siaga_gerakan` ada,
    `classification_v2` menjawab "klaimnya terbukti atau tidak", dan klaimnya
    BEDA per action. Tanpa penanda ini, rapor kalibrasi akan menjumlahkan
    "tesis arah yang benar" dengan "tesis amplitudo yang benar" jadi satu
    angka yang tidak berarti apa-apa.
    """
    if action in ACTION_CATEGORY_MAGNITUDE:
        return "amplitudo"
    if action in ACTION_CATEGORY_ENTRY or action in ACTION_CATEGORY_EXIT:
        return "arah"
    return "tanpa_klaim"


def classify_v2(
    action: str, excess_pct: float | None, sigma_window_pct: float | None,
) -> tuple[str | None, float | None]:
    """(classification_v2, z). None kalau tidak bisa dihitung -- BUKAN 'ambigu',
    karena 'tidak tahu' dan 'gerakannya tanggung' dua hal berbeda."""
    kind = claim_type(action)
    if kind == "tanpa_klaim":
        return "tidak_berlaku", None
    if excess_pct is None or not sigma_window_pct:
        return None, None
    z = excess_pct / sigma_window_pct

    if kind == "amplitudo":
        # Klaimnya "akan bergerak melebihi derunya sendiri", tanpa arah. Jadi
        # yang dinilai |z|, dan hasilnya BINER -- tidak ada "ambigu", karena
        # klaimnya sendiri tidak punya wilayah abu-abu: gerakannya melewati
        # ambang atau tidak. (Untuk klaim arah, 'ambigu' ada karena gerakan
        # kecil memang tidak membuktikan maupun membantah arah apa pun.)
        return ("terbukti" if abs(z) >= Z_TERBUKTI else "meleset"), round(z, 3)

    # Action keluar mengklaim harga TIDAK naik -- arah pembuktiannya terbalik,
    # jadi tandanya dibalik dan ambang yang sama dipakai apa adanya.
    zz = z if action in ACTION_CATEGORY_ENTRY else -z
    if zz >= Z_TERBUKTI:
        return "terbukti", round(z, 3)
    if zz <= Z_MELESET:
        return "meleset", round(z, 3)
    return "ambigu", round(z, 3)


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


def resolve_benchmark_pair(
    benchmark_series: dict[str, float] | None,
    stored_at_call: float | None,
    entry_date: str | None,
    exit_date: str | None,
    benchmark_price_now: float | None,
) -> tuple[float | None, float | None, str]:
    """(indeks_saat_masuk, indeks_saat_keluar, sumber).

    Dipisah jadi fungsi sendiri supaya bisa diuji langsung DAN dipakai ulang
    apa adanya oleh scripts/backfill_benchmark.py — backfill yang memakai
    aritmetika sendiri gampang menyimpang dari jalur produksi tanpa ketahuan.

    `sumber` menyebut dari mana tiap sisi datang, bukan sekadar "ada/tidak":
    tanpa itu, deret hasil backfill dan hasil evaluasi langsung terlihat
    identik di JSON padahal ketelitiannya beda.
    """
    series = benchmark_series or {}
    start = stored_at_call
    start_src = "call" if start is not None else None
    if start is None:
        start, used = benchmark_close_on_or_before(series, entry_date)
        start_src = f"deret@{used}" if used else None

    end, used_end = benchmark_close_on_or_before(series, exit_date)
    end_src = f"deret@{used_end}" if used_end else None
    if end is None and benchmark_price_now is not None:
        # Cadangan terakhir: harga indeks HARI INI, bukan pada exit_date.
        # Sengaja tetap dipakai (lebih baik ada pembanding kasar daripada
        # tidak sama sekali) tapi ditandai eksplisit -- inilah persis
        # ketidakcocokan tanggal yang jadi alasan revisi 2026-08-04.
        end, end_src = benchmark_price_now, "harga_hari_ini"

    if start is None or end is None:
        return start, end, "tidak_tersedia"
    return start, end, f"{start_src} -> {end_src}"


def evaluate_due_entries(
    timelines: dict[str, dict],
    evidence_by_ticker: dict[str, "EvidencePackage"],
    today: date | None = None,
    benchmark_price_now: float | None = None,
    benchmark_series: dict[str, float] | None = None,
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
                # sudah divonis -- lihat docstring _find_streaks).
                #
                # Audit 2026-08-06 (D7): dulu ini `continue` begitu saja, dan
                # itu melanggar janji keputusan #2 di docstring modul ("baris
                # Riwayat mana pun ... menunjukkan verdict yang sama"). Sebuah
                # streak TERUS TUMBUH selama action-nya tidak berubah, jadi
                # entry harian yang lahir SESUDAH vonis ikut bergabung ke
                # streak yang sudah dilewati ini dan tidak pernah terisi —
                # tesis yang sama tampil "terbukti" di baris lama dan "menunggu
                # evaluasi" di baris baru. Vonisnya tidak dihitung ulang (itu
                # yang bikin nilainya bergeser tiap hari); yang sudah ada
                # disalin apa adanya ke entry yang belum punya.
                done = next(
                    (rec for e in streak if (rec := (e.get("outcome") or {}).get(module))), None
                )
                if done is not None:
                    for e in streak:
                        outcome = e.get("outcome") or {}
                        if not outcome.get(module):
                            outcome[module] = done
                            e["outcome"] = outcome
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

                # Harga saham keluar dibaca pada bar lengkap terakhir, DAN
                # harganya diambil dari baris yang sama dengan tanggalnya
                # (lihat _last_complete_bar) -- harga indeks lalu dibaca pada
                # tanggal itu juga, bukan hari ini. `last_price` cuma cadangan
                # kalau tidak ada satu pun bar; `exit_price_source` menandainya
                # supaya jendela yang tidak sejajar tetap bisa dikenali.
                exit_date, exit_close = _last_complete_bar(price_history)
                if exit_close is not None:
                    exit_price, exit_price_source = exit_close, "bar_lengkap"
                else:
                    exit_price, exit_price_source = current_price, "kuotasi_terakhir"

                return_pct = _price_return_pct(start_price, exit_price)
                bench_start, bench_end, bench_source = resolve_benchmark_pair(
                    benchmark_series,
                    start_call.get("benchmark_at_call"),
                    since[:10],
                    exit_date,
                    benchmark_price_now,
                )
                bench_return = _price_return_pct(bench_start, bench_end)
                excess = None
                if return_pct is not None and bench_return is not None:
                    excess = return_pct - bench_return

                classification = _classify(start_call["action"], start_call["horizon"], return_pct)
                if classification is None:
                    continue  # harga belum bisa dibaca — biarkan pending, coba lagi run berikutnya

                threshold_pct = HORIZON_OUTCOME_THRESHOLD_PCT.get(start_call["horizon"])

                # Vonis v2 -- berdampingan dengan yang lama, tidak menggantikan.
                sigma_w = window_sigma_pct(price_history, since[:10], exit_date)
                cls_v2, z_excess = classify_v2(
                    start_call["action"],
                    round(excess, 2) if excess is not None else None,
                    sigma_w,
                )

                outcome_record = {
                    "classification": classification,
                    # Sebab, bukan cuma vonis (lihat diagnose_miss). None untuk
                    # yang tidak meleset.
                    "miss_reason": diagnose_miss(
                        start_call["action"], classification, return_pct,
                        threshold_pct, round(excess, 2) if excess is not None else None,
                    ),
                    "return_pct": round(return_pct, 2) if return_pct is not None else None,
                    "benchmark_return_pct": round(bench_return, 2) if bench_return is not None else None,
                    "excess_return_pct": round(excess, 2) if excess is not None else None,
                    # Kedua sisi indeks disimpan mentah, bukan cuma persennya:
                    # tanpa ini pengguna tidak bisa memeriksa sendiri apakah
                    # pembandingnya masuk akal, persis keluhan yang bikin
                    # entry_price/exit_price ditambahkan (2026-08-03).
                    "benchmark_at_entry": round(bench_start, 4) if bench_start is not None else None,
                    "benchmark_at_exit": round(bench_end, 4) if bench_end is not None else None,
                    "benchmark_source": bench_source,
                    # --- vonis v2 (lihat blok komentar di atas classify_v2) ---
                    "classification_v2": cls_v2,
                    "claim_type": claim_type(start_call["action"]),
                    "z_excess": z_excess,
                    "sigma_window_pct": round(sigma_w, 3) if sigma_w is not None else None,
                    "z_threshold": Z_TERBUKTI,
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
                    "exit_price": round(exit_price, 4) if exit_price is not None else None,
                    # Dari mana `exit_price` berasal. "bar_lengkap" = penutupan
                    # PADA `exit_date` di bawah, jadi sejajar dengan harga
                    # indeks; "kuotasi_terakhir" = tidak ada bar sama sekali,
                    # jadi tanggalnya tidak sejajar dan excess-nya kasar.
                    "exit_price_source": exit_price_source,
                    # Tanggal bar terakhir yang tersedia saat evaluasi -- BUKAN
                    # due_at. Keduanya sering beda dan itu bukan bug: BE jatuh
                    # tempo Minggu 2 Agu, harga yang dipakai tutupan Jumat 31
                    # Jul. Menulisnya sebagai "close 2 Agu" akan jadi klaim
                    # palsu, jadi tanggalnya dibawa apa adanya. Tanggal INI
                    # juga yang dipakai membaca harga indeks di atas.
                    "exit_date": exit_date,
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
