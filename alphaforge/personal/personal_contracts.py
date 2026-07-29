"""Personal layer contracts — kosakata action/horizon per lens + validasi P1-P5.

Ini BUKAN bagian dari kontrak publik (04_DATA_CONTRACTS.md) — spec lengkapnya
hidup di dokumen privat, bukan di alphaforge-v2-main. Modul di sini boleh
mengimpor dari alphaforge.layer1/layer2, tapi TIDAK ADA modul publik yang
boleh mengimpor dari alphaforge.personal — arahnya satu arah, supaya folder
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
        "speculative": ("masuk_spekulatif", "tunggu_katalis", "lewati"),
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

    # P4: confidence low -> tidak boleh action menambah eksposur penuh/langsung.
    if confidence_band == "low" and call.action in ACTION_FULL_EXPOSURE:
        violations.append(f"P4: confidence.band=low tapi action='{call.action}' (menambah eksposur penuh)")

    # P5: action wajib dari kosakata yang cocok dengan position_status.
    if call.action not in vocab:
        violations.append(
            f"P5: action '{call.action}' bukan bagian kosakata {call.module}/{call.position_status} ({vocab})"
        )

    return violations
