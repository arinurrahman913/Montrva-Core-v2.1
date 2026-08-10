"""Personal layer contracts — kosakata action/horizon per lens + validasi P1-P5.

Ini BUKAN bagian dari kontrak publik (04_DATA_CONTRACTS.md) — spec lengkapnya
hidup di dokumen privat, bukan di montrva-v2-main. Modul di sini boleh
mengimpor dari montrva.layer1/layer2, tapi TIDAK ADA modul publik yang
boleh mengimpor dari montrva.personal — arahnya satu arah, supaya folder
ini bisa dihapus utuh tanpa merusak apa pun di luar dirinya sendiri.

PersonalCall MEMBACA ModuleOutput (reasoning_contracts.py), tidak pernah
menggantikannya. reasoning_outputs.json (publik) tidak pernah menyimpan
action/horizon — kalau field itu ditaruh di ModuleOutput, siapa pun yang baca
file publik itu langsung melihat rekomendasi pribadi, dan seluruh pemisahan
di package ini jadi percuma.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Module = Literal["multibagger", "quality_compound", "speculative"]
PositionStatus = Literal["no_holding", "holding"]
HorizonStatus = Literal["dalam_horizon", "horizon_terlewati", "tidak_berlaku"]
HorizonLabel = Literal["estimasi_tesis", "cek_ulang", "sarankan_keluar"]

# --- Kosakata `action` — per modul (Opsi B, D-09-style), dua set tergantung
# position_status. Sama seperti STANCE_VOCAB di reasoning_contracts.py: TIDAK
# sebanding lintas modul, cuma sebanding di dalam satu modul.
ACTION_VOCAB: dict[PositionStatus, dict[Module, tuple[str, ...]]] = {
    "no_holding": {
        "multibagger": ("mulai_posisi", "cicil_bertahap", "pantau", "lewati"),
        "quality_compound": ("akumulasi", "akumulasi_saat_koreksi", "pantau", "lewati"),
        # "masuk_spekulatif" DIGANTI "siaga_gerakan" (2026-08-05) — lihat blok
        # penjelasan di atas ACTION_CATEGORY_MAGNITUDE di bawah. Nama lama
        # TIDAK dihapus dari kategori/evaluasi karena 309 tesis lama memakainya
        # dan harus tetap dinilai dengan aturan yang berlaku saat dibuat; yang
        # berubah cuma apa yang DIPRODUKSI mulai sekarang.
        "speculative": ("siaga_gerakan", "tunggu_katalis", "lewati"),
    },
    "holding": {
        "multibagger": ("tambah_bertahap", "tahan", "kurangi", "jual"),
        "quality_compound": ("tambah", "tahan", "kurangi", "jual"),
        "speculative": ("tahan_sampai_katalis", "jual"),
    },
}

# P4: action yang menambah eksposur secara PENUH/LANGSUNG — diblokir kalau
# confidence.band == "low". Versi bertahap/bersyarat (cicil_bertahap,
# akumulasi_saat_koreksi, tambah_bertahap) SENGAJA TIDAK masuk sini — itu
# justru pengganti mekanis untuk tindakan penuh saat confidence turun.
ACTION_FULL_EXPOSURE = frozenset({"mulai_posisi", "akumulasi", "masuk_spekulatif", "tambah"})

# Pengganti bertahap saat P4 menolak action penuh — DIPETAKAN PER SEL
# (position_status, module), BUKAN peta datar action->action. Peta datar
# kelihatannya cukup ("tambah" -> "tambah_bertahap") tapi salah: dua nama itu
# hidup di kosakata yang BERBEDA — "tambah" cuma ada di holding/quality_compound
# sedangkan "tambah_bertahap" cuma ada di holding/multibagger (lihat
# ACTION_VOCAB di atas). Memakai peta datar berarti memberi Quality sebuah
# action di luar kosakatanya sendiri: memperbaiki P4 sambil MELANGGAR P5.
# Karena itu holding/quality_compound turun ke "tahan" (satu-satunya alternatif
# non-penuh di kosakatanya), bukan ke versi "bertahap" yang tidak dimilikinya.
#
# Sel kosong = tidak ada yang perlu diturunkan: holding/multibagger sudah
# memakai "tambah_bertahap" (bukan anggota ACTION_FULL_EXPOSURE), dan
# holding/speculative tidak punya action penambah eksposur sama sekali.
FULL_TO_GRADED_ACTION: dict[PositionStatus, dict[Module, dict[str, str]]] = {
    "no_holding": {
        "multibagger": {"mulai_posisi": "cicil_bertahap"},
        "quality_compound": {"akumulasi": "akumulasi_saat_koreksi"},
        # Kedua nama dipetakan: yang lama supaya rekonstruksi call historis
        # tetap berperilaku sama, yang baru untuk yang diproduksi mulai kini.
        "speculative": {"masuk_spekulatif": "tunggu_katalis", "siaga_gerakan": "tunggu_katalis"},
    },
    "holding": {
        "multibagger": {},
        "quality_compound": {"tambah": "tahan"},
        "speculative": {},
    },
}


def downgrade_full_exposure(action: str, position_status: str, module: str) -> str | None:
    """Versi bertahap dari `action` untuk sel ini, atau None kalau tidak ada
    yang perlu diturunkan. Dipakai personal_reasoning.build_personal_call
    untuk MENEGAKKAN P4, bukan sekadar melaporkannya."""
    return FULL_TO_GRADED_ACTION.get(position_status, {}).get(module, {}).get(action)

# P3: action paling pasif per position_status — dipakai kalau stance
# *_tak_terbaca (lens sendiri bilang datanya tak terbaca, tidak boleh
# menganjurkan tindakan aktif).
PASSIVE_ACTION: dict[PositionStatus, dict[Module, str]] = {
    "no_holding": {"multibagger": "pantau", "quality_compound": "pantau", "speculative": "tunggu_katalis"},
    "holding": {"multibagger": "tahan", "quality_compound": "tahan", "speculative": "tahan_sampai_katalis"},
}

# Kategori action buat label horizon kontekstual (§10 draft) — bukan bagian
# kontrak PersonalCall (horizon tetap satu field), murni dipakai personal_
# reasoning.py buat memilih horizon_label, dan frontend buat memilih teks.
ACTION_CATEGORY_REVIEW = frozenset({"pantau", "lewati", "tunggu_katalis"})
ACTION_CATEGORY_EXIT = frozenset({"kurangi", "jual"})

# --- Action berklaim AMPLITUDO, bukan arah (2026-08-05) ---------------------
#
# Diukur, bukan dikira. Lensa Spekulatif diuji lawan pembanding nol (saham
# yang TIDAK dipilih, pada jendela tanggal yang sama persis, aturan yang sama
# persis — scripts/measure_baseline.py):
#
#     pilihan sistem   gerakan besar 48,9%  | kalau besar, naik 25,8%
#     saham acak       gerakan besar 33,4%  | kalau besar, naik 21,7%
#     selisih          +15,5pp (SK95% +10,0..+21,0)  |  +4,2pp (SK95% -2,8..+11,1)
#                      DI LUAR derau                 |  di dalam derau
#
# Lensa ini BISA menemukan saham yang akan bergerak besar — masuk akal secara
# mekanis, dia memang memilih ticker berkatalis dan 80% katalisnya earnings.
# Yang TIDAK terdeteksi sama sekali: kemampuan menebak ARAH gerakan itu.
#
# `masuk_spekulatif` mengklaim arah, jadi ia menjanjikan hal yang tidak
# dikuasai lensanya, sekaligus menyembunyikan hal yang dikuasai. `siaga_gerakan`
# mengklaim persis yang terukur: "akan bergerak melebihi derunya sendiri, arah
# tidak diklaim".
#
# JEBAKAN YANG HAMPIR MELOLOSKAN KESIMPULAN SALAH: selisih 'terbukti' di bawah
# aturan v2 adalah +5,4pp dan tampak signifikan. Dibaca sendirian itu terlihat
# seperti "sistem terbukti unggul meramal". Padahal pilihan sistem punya lebih
# banyak terbukti DAN lebih banyak meleset sekaligus (ambigu 51% vs 67%) — itu
# tanda amplitudo, bukan arah. Aturan umumnya: kalau sebuah metrik naik
# "signifikan", periksa dulu apakah metrik kebalikannya ikut naik.
ACTION_CATEGORY_MAGNITUDE = frozenset({"siaga_gerakan"})

# P4 berlaku untuk action ini juga: data yang terlalu tipis tidak boleh dipakai
# mengklaim apa pun, termasuk klaim amplitudo. Tapi `siaga_gerakan` BUKAN
# tindakan eksposur, jadi ia sengaja tidak dimasukkan ke ACTION_FULL_EXPOSURE —
# memasukkannya akan membuat nama konstanta itu berbohong. Gerbangnya dipisah.
ACTION_GATED_BY_CONFIDENCE = ACTION_FULL_EXPOSURE | ACTION_CATEGORY_MAGNITUDE

# Kelompok action yang merujuk panggilan YANG SAMA meski kosakatanya berganti di
# tengah rentang waktu yang dibandingkan. Perbandingan string mentah tidak cukup
# begitu ada penggantian nama: `masuk_spekulatif` -> `siaga_gerakan` (2026-08-05)
# membuat SETIAP hitungan "sudah berapa run bertahan" terbaca 1 di run pertama
# sesudahnya, padahal panggilannya tidak berubah sama sekali (AAOI: 9 run
# berturut-turut terbaca 1).
#
# KEMBAR dengan BEST_ACTION_ALIASES di frontend/src/format.js — dua-duanya harus
# berubah bersama, sama seperti ACTION_CATEGORY_MAGNITUDE dengan MAGNITUDE_ACTIONS.
ACTION_ALIASES: dict[Module, frozenset[str]] = {
    "multibagger": frozenset({"mulai_posisi"}),
    "quality_compound": frozenset({"akumulasi"}),
    "speculative": frozenset({"siaga_gerakan", "masuk_spekulatif"}),
}


def same_action(module: str, a: str | None, b: str | None) -> bool:
    """Apakah dua action merujuk panggilan yang sama (lihat ACTION_ALIASES)."""
    if a == b:
        return a is not None
    if not a or not b:
        return False
    aliases = ACTION_ALIASES.get(module)
    return bool(aliases) and a in aliases and b in aliases


def horizon_label(action: str) -> HorizonLabel:
    if action in ACTION_CATEGORY_REVIEW:
        return "cek_ulang"
    if action in ACTION_CATEGORY_EXIT:
        return "sarankan_keluar"
    return "estimasi_tesis"


# --- Kosakata `horizon` — 5 bucket, dipakai bersama (beda dari action:
# horizon itu satuan waktu, bukan penilaian, jadi menyamakannya lintas modul
# tidak menciptakan risiko verdict tunggal seperti action).
HORIZON_VALUES = ("mingguan", "bulanan", "enam_bulan", "satu_dua_tahun", "lima_tahun")

# P1: tiap lens cuma boleh pakai sebagian bucket — tanpa batasan ini horizon
# jadi hiasan (lihat draft §6 buat alasan tiap batasan).
HORIZON_ALLOWED: dict[Module, tuple[str, ...]] = {
    "multibagger": ("enam_bulan", "satu_dua_tahun", "lima_tahun"),
    "quality_compound": ("satu_dua_tahun", "lima_tahun"),
    "speculative": ("mingguan", "bulanan", "enam_bulan"),
}


@dataclass
class PersonalCall:
    """Satu rekomendasi pribadi — satu lens, satu ticker. Membaca ModuleOutput,
    tidak menggantikannya (Data Contracts §6/D-04 tetap berlaku di sana)."""
    module: Module
    ticker: str
    exchange: str
    method_version: str

    action: str  # dari ACTION_VOCAB[position_status][module]
    action_rationale: str

    horizon: str  # dari HORIZON_ALLOWED[module]
    horizon_basis: str
    horizon_anchor: str | None = None

    # Terisi kalau P4 menurunkan action penuh jadi versi bertahap (band lensa
    # ini "low" = datanya terlalu tipis buat dipercaya penuh). Disimpan supaya
    # UI bisa menjelaskan kenapa ticker berskor tinggi muncul dengan action
    # bertahap — tanpa ini penurunannya terlihat seperti inkonsistensi.
    action_downgraded_from: str | None = None

    source_stance: str = ""  # ModuleOutput.stance — jejak audit balik ke Reasoning Umum
    source_confidence: float = 0.0  # ModuleOutput.confidence.score saat itu

    # KEKUATAN TESIS (ModuleOutput.thesis_score, 0-100 netral di 50) — BEDA dari
    # source_confidence di atas, yang sebetulnya turunan kualitas DATA
    # (reasoning.py:_module_confidence memulai dari ConfidenceReport.overall.score
    # lalu cuma dikurangi per field yang hilang). Dua-duanya disimpan karena
    # menjawab pertanyaan berbeda: "seberapa kuat tesisnya" vs "seberapa lengkap
    # datanya". Peringkat top pick memakai yang PERTAMA.
    thesis_score: float = 50.0

    # Harga saat call ini dibuat — disimpan supaya evaluasi outcome nanti tidak
    # perlu merekonstruksi harga entry dari price_history (yang cuma menyimpan 1
    # tahun; call ber-horizon lima_tahun akan salah baseline tanpa field ini).
    # benchmark_at_call = harga S&P 500 pada saat yang sama, supaya hasilnya bisa
    # dibandingkan dengan "kalau uangnya ditaruh di indeks saja".
    price_at_call: float | None = None
    benchmark_at_call: float | None = None

    # Ringkasan risiko SAAT call dibuat (dari RiskAssessment) — lapisan pribadi
    # sebelumnya tidak pernah membaca Risk sama sekali dan hanya menitipkan
    # pesan "cek Risk Flags sendiri" di UI.
    risk_flags_high: int = 0
    risk_flags_medium: int = 0
    risk_flag_types: list[str] = field(default_factory=list)

    position_status: PositionStatus = "no_holding"
    holding_since: str | None = None
    unrealized_return_pct: float | None = None
    horizon_status: HorizonStatus = "tidak_berlaku"

    # Sudah berapa run pipeline BERTURUT-TURUT action ini bertahan, dan sejak
    # kapan. Diisi belakangan (annotate_action_streaks) karena butuh riwayat,
    # yang baru dimuat sesudah call set dibangun. None = riwayat tidak tersedia
    # saat call ini dibuat, BUKAN "baru pertama kali" — 1 yang berarti itu.
    #
    # Ada di sini, bukan cuma dihitung frontend, karena frontend hanya memegang
    # riwayat ticker yang kartunya dibuka: pertanyaan "dari 312 pick hari ini
    # mana yang baru" tidak bisa dijawab satu kartu pada satu waktu.
    streak_runs: int | None = None
    streak_since: str | None = None
    # Run yang terjadi di dalam rentang streak tapi ticker ini tidak ikut
    # diskrining. Tidak memutus streak — absen dari screening itu ketiadaan
    # data, bukan pembalikan sinyal.
    streak_runs_missing: int = 0

    violations: list[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class PersonalCallSet:
    """3 PersonalCall berdampingan untuk satu ticker. TIDAK ADA field skor
    gabungan/ranking/verdict tunggal — larangan D-04 tetap berlaku di sini,
    ini cuma lapisan preskriptif di atas Multi-Lens (Prinsip #3), bukan
    pengganti."""
    ticker: str
    exchange: str
    multibagger: PersonalCall
    quality_compound: PersonalCall
    speculative: PersonalCall
    method_versions: dict[str, str] = field(default_factory=dict)
    generated_at: str = ""

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def validate_personal_call(
    call: PersonalCall,
    is_unreadable_stance: bool,
    confidence_band: str,
) -> list[str]:
    """P1-P5 (lihat draft §7). Mengembalikan list pelanggaran — kosong berarti
    lolos. Dipanggil sebagai pengaman regresi (non-halting, cuma di-log),
    sama seperti validate_module_output di reasoning_contracts.py."""
    violations = []
    vocab = ACTION_VOCAB[call.position_status][call.module]

    # P1: horizon harus di subset modul itu.
    if call.horizon not in HORIZON_ALLOWED[call.module]:
        violations.append(
            f"P1: horizon '{call.horizon}' bukan bagian bucket yang diizinkan untuk {call.module} "
            f"({HORIZON_ALLOWED[call.module]})"
        )

    # P2: horizon_basis wajib tidak kosong — pengecekan "menyebut field nyata"
    # yang sebenarnya dilakukan di personal_reasoning.py saat horizon_basis
    # DIBANGUN (template selalu menyertakan nama field), ini cuma pengaman
    # terakhir kalau sampai lolos kosong.
    if not call.horizon_basis or not call.horizon_basis.strip():
        violations.append("P2 (unfounded_horizon): horizon_basis kosong")

    # P3: stance tak_terbaca -> action wajib yang paling pasif.
    if is_unreadable_stance:
        expected = PASSIVE_ACTION[call.position_status][call.module]
        if call.action != expected:
            violations.append(f"P3: stance tak_terbaca tapi action='{call.action}' (harus '{expected}')")

    # P4: confidence low -> tidak boleh action menambah eksposur penuh/langsung,
    # DAN tidak boleh action berklaim amplitudo (`siaga_gerakan`): data yang
    # terlalu tipis tidak layak dipakai mengklaim apa pun. Gerbangnya memakai
    # ACTION_GATED_BY_CONFIDENCE, bukan ACTION_FULL_EXPOSURE, supaya nama
    # konstanta yang kedua tetap jujur — siaga_gerakan bukan tindakan eksposur.
    if confidence_band == "low" and call.action in ACTION_GATED_BY_CONFIDENCE:
        kind = "berklaim amplitudo" if call.action in ACTION_CATEGORY_MAGNITUDE else "menambah eksposur penuh"
        violations.append(f"P4: confidence.band=low tapi action='{call.action}' ({kind})")

    # P5: action wajib dari kosakata yang cocok dengan position_status.
    if call.action not in vocab:
        violations.append(
            f"P5: action '{call.action}' bukan bagian kosakata {call.module}/{call.position_status} ({vocab})"
        )

    return violations
