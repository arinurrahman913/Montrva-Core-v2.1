"""Personal reasoning — turns existing ModuleOutput (public, reasoning.py)
into PersonalCall (action + horizon) per lens.

Prinsip desain (draft §10): TIDAK ADA mesin skor kedua. action/horizon tidak
menghitung ulang dari Knowledge — keduanya cuma tabel keputusan dari stance +
confidence.band yang SUDAH dihasilkan reasoning.py, plus CatalystSet untuk
Speculative. Ini menjaga P2 (horizon_basis harus merujuk field nyata)
terpenuhi by construction: field yang disebut di horizon_basis selalu diambil
dari ModuleOutput.key_metrics (yang sudah ada), bukan dikarang.

holdings.json TIDAK PERNAH masuk git (lihat .gitignore) — file ini kalau
bocor ke commit publik langsung membocorkan portofolio & harga beli riil,
beda kelas dari file pribadi lain yang "cuma" pendapat sistem.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .personal_contracts import (
    ACTION_CATEGORY_MAGNITUDE, PersonalCall, downgrade_full_exposure, validate_personal_call,
)

if TYPE_CHECKING:
    from ..layer2.reasoning_contracts import ModuleOutput
    from ..layer2.catalyst_contracts import CatalystSet
    from ..layer2.contracts import EvidencePackage

METHOD_VERSION = "1.0"

from ..layer2.reasoning_contracts import UNREADABLE_STANCE  # noqa: E402

# Batas atas (hari) tiap bucket horizon — dipakai buat horizon_status
# (§11: "sudah lewat horizon perkiraan atau belum"), BUKAN buat menentukan
# action (yang tetap 100% dari stance+faktor, lihat catatan di
# compute_horizon_status di bawah — larangan eksplisit dari draft).
HORIZON_UPPER_DAYS = {
    "mingguan": 28,
    "bulanan": 180,
    "enam_bulan": 365,
    "satu_dua_tahun": 730,
    "lima_tahun": 1825,
}

# --- Tabel keputusan `action` (draft §10) — lookup murni dari stance yang
# sudah dihasilkan reasoning.py (threshold score sudah terjadi di sana) +
# TINGKAT (kolom high/medium/low). Kolom ini SEBELUMNYA diisi confidence.band
# -- diganti ke _thesis_score_tier(thesis_score) per keputusan pengguna
# 2026-07-29 karena confidence.band=='high' terbukti tidak pernah tercapai
# di seluruh universe (lihat docstring _thesis_score_tier), yang diam-diam
# membuat kolom "high" di sini (mulai_posisi/akumulasi/masuk_spekulatif)
# MUSTAHIL dipilih walau stance-nya qualify. Baris "*_tak_terbaca" sengaja
# disamakan dengan PASSIVE_ACTION (P3).
#
# P4 (tidak ada action ACTION_FULL_EXPOSURE saat confidence.band "low")
# dulu juga lolos by construction lewat teks tabel ini: kolom "low" tidak
# pernah berisi action eksposur penuh. Sejak sel mati diisi apa adanya
# (2026-08-06, opsi A — lihat blok di atas ACTION_TABLE), kolom "low" pita
# atas memuat `akumulasi`/`tambah`, jadi sifat itu tidak lagi terbaca dari
# teksnya. Yang menegakkan P4 sekarang dua hal yang keduanya nyata:
# `downgrade_full_exposure` dipanggil di build_personal_call (penegakan
# runtime, sejak 2026-07-31), dan check_action_table.py memeriksa sifat lama
# itu pada sel yang BENAR-BENAR bisa menyala. validate_personal_call() tetap
# jaring pengaman regresi, bukan jalur utama.
# SEL YANG TIDAK BISA TERCAPAI DIISI DENGAN APA YANG BENAR-BENAR TERJADI DI
# PITA ITU, bukan dengan action yang lebih hati-hati yang tidak akan pernah
# dipilih (keputusan pengguna 2026-08-06, opsi A atas temuan audit D4).
#
# Duduk perkaranya: `stance` dan kolom tingkat dihitung dari BILANGAN YANG SAMA
# (`thesis_score` == skor lensa). Ambang stance kuat 75 ada DI ATAS gerbang
# tier "high" 70, jadi `score >= 75` selalu berarti tier "high" — sel
# `[pita atas][medium]` dan `[pita atas][low]` mustahil, apa pun isinya.
# Menggeser ambangnya tidak menolong, cuma memindahkan sel matinya ke pita
# tengah. Selama satu angka menyetir dua sumbu, akan selalu ada sel kosong.
#
# Sebelum ini sel-sel itu berisi versi yang lebih hati-hati (`tahan`,
# `cicil_bertahap`, `akumulasi_saat_koreksi`), sehingga tabel ini TERBACA
# seolah tingkat memodulasi pita atas — padahal tidak, dan tidak pernah.
# Sekarang isinya sama dengan sel hidup di barisnya, jadi yang dibaca orang
# dari tabel ini sama dengan yang dihasilkan sistem. Perilakunya NOL berubah:
# tidak satu pun sel yang diubah pernah bisa terpilih.
#
# Konsekuensi yang harus disadari, dan sengaja diterima: holding berstance
# teratas selalu menerima action penambah eksposur, dan yang menahannya cuma
# P4 (`band == "low"` -> downgrade_full_exposure), bukan kekuatan tesisnya.
# Memberi pita atas rem kedua yang independen dari skor adalah keputusan
# kalibrasi sekelas A7/A8, dan tidak diambil di sini.
#
# `scripts/check_action_table.py` menegakkan tiga hal sekaligus: (1) tiap sel
# mati isinya harus salah satu action yang benar-benar tercapai di barisnya —
# tabel tidak boleh menjanjikan action yang aljabar skornya tidak bisa berikan;
# (2) tidak ada action yang hilang total; (3) tidak ada sel HIDUP di kolom
# "low" yang berisi action eksposur penuh (P4 by construction, kini diperiksa
# pada sel yang benar-benar bisa menyala, bukan pada teks tabelnya).
ACTION_TABLE: dict[str, dict[str, dict[str, dict[str, str]]]] = {
    "no_holding": {
        "multibagger": {
            # medium/low mustahil (skor >=75 selalu tier high) — lihat blok di atas
            "ruang_terbuka": {"high": "mulai_posisi", "medium": "mulai_posisi", "low": "mulai_posisi"},
            "ruang_sempit": {"high": "cicil_bertahap", "medium": "pantau", "low": "pantau"},
            # high/medium mustahil (skor <45 selalu tier low)
            "ruang_tertutup": {"high": "lewati", "medium": "lewati", "low": "lewati"},
            "ruang_tak_terbaca": {"high": "pantau", "medium": "pantau", "low": "pantau"},
        },
        "quality_compound": {
            "compounding_kuat": {"high": "akumulasi", "medium": "akumulasi", "low": "akumulasi"},
            "compounding_rapuh": {"high": "akumulasi_saat_koreksi", "medium": "pantau", "low": "pantau"},
            "bukan_compounder": {"high": "lewati", "medium": "lewati", "low": "lewati"},
            "mesin_tak_terbaca": {"high": "pantau", "medium": "pantau", "low": "pantau"},
        },
        "speculative": {
            # medium diketatkan ke "tunggu_katalis" (bukan "masuk_spekulatif")
            # per keputusan pengguna 2026-07-29, opsi A: cuma confidence HIGH
            # yang boleh dapat action entry penuh -- konsisten dengan
            # multibagger/quality_compound di bawah, yang keduanya SUDAH
            # begitu sejak awal (medium selalu dapat versi bertahap/kondisional,
            # bukan versi penuh). Ini SATU-SATUNYA sel di seluruh ACTION_TABLE
            # yang sebelumnya ngasih action penuh di band selain "high" --
            # audit 2026-07-27/29 menemukan ini yang bikin 22% universe (895-
            # 1087 ticker) lolos jadi kandidat masuk_spekulatif tiap hari,
            # mayoritas cuma modal "earnings terjadwal" (katalis paling umum)
            # + confidence medium, bukan keyakinan tinggi beneran.
            # "masuk_spekulatif" -> "siaga_gerakan" (2026-08-05). Alasan lengkap
            # + angkanya ada di atas ACTION_CATEGORY_MAGNITUDE di
            # personal_contracts.py. Ringkasnya: lensa ini terbukti menemukan
            # saham yang akan bergerak besar (+15,5pp di atas acak, di luar
            # derau) tapi TIDAK terbukti bisa menebak arahnya (+4,2pp, di dalam
            # derau) -- jadi action-nya berhenti menjanjikan arah dan mulai
            # menyatakan apa yang benar-benar terukur.
            "asimetri_berkatalis": {"high": "siaga_gerakan", "medium": "tunggu_katalis", "low": "tunggu_katalis"},
            "asimetri_tanpa_katalis": {"high": "tunggu_katalis", "medium": "tunggu_katalis", "low": "tunggu_katalis"},
            "tanpa_asimetri": {"high": "lewati", "medium": "lewati", "low": "lewati"},
            "asimetri_tak_terbaca": {"high": "tunggu_katalis", "medium": "tunggu_katalis", "low": "tunggu_katalis"},
        },
    },
    "holding": {
        "multibagger": {
            "ruang_terbuka": {"high": "tambah_bertahap", "medium": "tambah_bertahap", "low": "tambah_bertahap"},
            "ruang_sempit": {"high": "tahan", "medium": "tahan", "low": "kurangi"},
            # `kurangi` di kolom high dulu tidak pernah menyala (skor <45 selalu
            # tier low), jadi stance ini selalu berakhir `jual`. `kurangi` tetap
            # hidup lewat ruang_sempit/low di atas.
            "ruang_tertutup": {"high": "jual", "medium": "jual", "low": "jual"},
            "ruang_tak_terbaca": {"high": "tahan", "medium": "tahan", "low": "tahan"},
        },
        "quality_compound": {
            "compounding_kuat": {"high": "tambah", "medium": "tambah", "low": "tambah"},
            "compounding_rapuh": {"high": "tahan", "medium": "tahan", "low": "kurangi"},
            "bukan_compounder": {"high": "jual", "medium": "jual", "low": "jual"},
            "mesin_tak_terbaca": {"high": "tahan", "medium": "tahan", "low": "tahan"},
        },
        "speculative": {
            "asimetri_berkatalis": {"high": "tahan_sampai_katalis", "medium": "tahan_sampai_katalis", "low": "tahan_sampai_katalis"},
            "asimetri_tanpa_katalis": {"high": "jual", "medium": "jual", "low": "jual"},
            "tanpa_asimetri": {"high": "jual", "medium": "jual", "low": "jual"},
            "asimetri_tak_terbaca": {"high": "tahan_sampai_katalis", "medium": "tahan_sampai_katalis", "low": "tahan_sampai_katalis"},
        },
    },
}

# Klasifikasi key_metrics per modul buat horizon (§10): "structural"/
# "fundamental" -> horizon lebih panjang, "momentum"/"valuation" -> lebih
# pendek. Key-key ini persis yang sudah ditulis reasoning.py ke key_metrics,
# BUKAN nama baru -- itulah yang bikin horizon_basis otomatis lolos P2.
# Dari 3 key struktural ini, hanya `acceleration_signal` yang benar-benar bisa
# terisi hari ini -- tam_estimate & segment_growth belum punya sumber data sama
# sekali (None di 4056/4056, lihat catatan di reasoning.py:_multibagger_lens).
# Sampai 2026-07-31 ketiganya tidak pernah muncul karena reasoning.py mencari
# kata "positive" sementara Knowledge menulis "accelerating" -- membuat horizon
# "lima_tahun" mustahil tercapai (0 dari 4056 call). Sesudah kata kuncinya
# diperbaiki: 1297 ticker punya key struktural, 45 di antaranya struktural SAJA
# (tanpa key momentum) sehingga benar-benar dapat "lima_tahun".
_MULTIBAGGER_STRUCTURAL_KEYS = ("tam_estimate", "segment_growth", "acceleration_signal")
_MULTIBAGGER_MOMENTUM_KEYS = ("return_1y", "analyst_upside_pct", "analyst_recommendation", "price_target_trend_3m", "eps_beat_streak")
_QUALITY_FUNDAMENTAL_KEYS = ("net_margin_q4", "current_ratio", "debt_to_equity")
_QUALITY_VALUATION_KEYS = ("pe_ratio", "pb_ratio", "pe_peer_percentile", "analyst_upside_pct", "analyst_recommendation", "price_target_trend_3m")


def load_holdings(path: str | Path) -> dict[str, dict]:
    """Baca holdings.json -> {ticker: {avg_cost, buy_date, quantity?}}.
    File tidak wajib ada (default: tidak ada holding sama sekali)."""
    p = Path(path)
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {h["ticker"].upper(): h for h in data.get("holdings", [])}


def compute_position(
    ticker: str, holdings: dict[str, dict], current_price: float | None,
) -> tuple[str, str | None, float | None]:
    """Status portofolio untuk satu ticker -- properti portofolio, bukan
    properti lens, jadi dihitung sekali dan dipakai ketiga lens (lihat
    personal_aggregator.build_personal_call_set)."""
    h = holdings.get(ticker.upper())
    if not h:
        return "no_holding", None, None
    avg_cost = h.get("avg_cost")
    buy_date = h.get("buy_date")
    unrealized = None
    if current_price is not None and avg_cost:
        unrealized = (current_price - avg_cost) / avg_cost * 100.0
    return "holding", buy_date, unrealized


def _compute_horizon_status(position_status: str, holding_since: str | None, horizon: str, today: date) -> str:
    """MURNI INFORMASIONAL (draft §11, keputusan final) -- fungsi ini TIDAK
    PERNAH dipanggil dari jalur yang menentukan `action`. Kalau nanti ada
    yang menggabungkannya "biar praktis", itu melanggar keputusan yang sudah
    diambil sadar: telat dari jadwal bukan alasan otomatis jual selama
    stance-nya sendiri belum berubah."""
    if position_status != "holding" or not holding_since:
        return "tidak_berlaku"
    try:
        since = date.fromisoformat(holding_since)
    except ValueError:
        return "tidak_berlaku"
    age_days = (today - since).days
    return "horizon_terlewati" if age_days > HORIZON_UPPER_DAYS[horizon] else "dalam_horizon"


def _not_past(expected_at: str | None, today: date) -> bool:
    """False kalau tanggalnya sudah lewat. Tanggal tak terbaca dianggap masih
    berlaku (konservatif: lebih baik horizon terlalu panjang daripada sebuah
    katalis nyata terbuang cuma karena formatnya aneh)."""
    if not expected_at:
        return False
    try:
        return date.fromisoformat(expected_at) >= today
    except ValueError:
        return True


def _speculative_horizon(
    catalyst: "CatalystSet | None", today: date,
) -> tuple[str, str, str | None]:
    """Return (horizon, horizon_basis, horizon_anchor). Satu-satunya lens
    dengan jangkar tanggal nyata (CatalystSet.catalysts[].expected_at)."""
    if catalyst is not None and catalyst.has_upcoming:
        # Buang katalis yang tanggalnya sudah LEWAT — tanpa ini `min()` bisa
        # memilih katalis basi (mis. kalau pipeline telat jalan berhari-hari),
        # lalu `max(days, 0)` di bawah menyamarkannya jadi "sekitar 0 hari lagi"
        # dan horizon dilabeli "mingguan" seolah-olah masih akan datang.
        upcoming = [
            c for c in catalyst.catalysts
            if c.certainty in ("scheduled", "expected") and _not_past(c.expected_at, today)
        ]
    else:
        upcoming = []
    if upcoming:
        nearest = min(upcoming, key=lambda c: c.expected_at)
        try:
            days = (date.fromisoformat(nearest.expected_at) - today).days
        except ValueError:
            days = 90  # fallback konservatif kalau format tanggal tak terduga
        if days <= 28:
            horizon = "mingguan"
        elif days <= 180:
            horizon = "bulanan"
        else:
            # PLAFON DATA, bukan bug: catalyst.py cuma bisa menurunkan 2 jenis
            # katalis dari Yahoo `.info` -- earnings (3128) & dividen (656),
            # dua-duanya kuartalan. Jarak terjauh yang pernah muncul di 4056
            # ticker cuma 90 hari (audit 2026-07-31), jadi cabang >180 hari ini
            # tidak pernah dieksekusi: 0 call ber-horizon "enam_bulan".
            # SENGAJA TIDAK dihapus (dan "enam_bulan" tetap di HORIZON_ALLOWED):
            # jenis katalis berjangka panjang (FDA/regulatory/investor day)
            # sudah tercatat sebagai fitur yang ditunda di catalyst.py -- begitu
            # itu masuk, bucket ini hidup sendiri tanpa perubahan di sini.
            horizon = "enam_bulan"
        basis = (
            f"Katalis {nearest.kind} diperkirakan {nearest.expected_at} ({nearest.certainty}) "
            f"— sekitar {max(days, 0)} hari lagi."
        )
        return horizon, basis, nearest.expected_at
    # Tidak ada katalis kalender -- horizon di sini "kapan cek ulang", bukan
    # estimasi resolusi tesis (draft §10, catatan eksplisit).
    return "bulanan", (
        "Tidak ada katalis kalender terjadwal saat ini — horizon ini menandai kapan cek ulang, "
        "bukan estimasi resolusi tesis."
    ), None


def _structural_vs_momentum_horizon(
    key_metrics: dict, structural_keys: tuple[str, ...], momentum_keys: tuple[str, ...],
    structural_horizon: str, momentum_horizon: str, default_horizon: str,
    structural_note: str, momentum_note: str, default_note: str,
) -> tuple[str, str]:
    present_structural = [k for k in structural_keys if k in key_metrics]
    present_momentum = [k for k in momentum_keys if k in key_metrics]
    if present_structural and not present_momentum:
        return structural_horizon, f"Didorong {structural_note} ({', '.join(present_structural)})."
    if present_momentum and not present_structural:
        return momentum_horizon, f"Didorong {momentum_note} ({', '.join(present_momentum)})."
    if present_structural and present_momentum:
        # Campuran -- default modul yang menang (§10: Multibagger -> tengah,
        # Quality -> yang lebih panjang), bukan salah satu faktor dipaksa menang.
        return default_horizon, (
            f"Faktor campuran: struktural ({', '.join(present_structural)}) dan "
            f"momentum ({', '.join(present_momentum)}). {default_note}"
        )
    return default_horizon, default_note


def _multibagger_horizon(key_metrics: dict) -> tuple[str, str]:
    return _structural_vs_momentum_horizon(
        key_metrics, _MULTIBAGGER_STRUCTURAL_KEYS, _MULTIBAGGER_MOMENTUM_KEYS,
        structural_horizon="lima_tahun", momentum_horizon="enam_bulan", default_horizon="satu_dua_tahun",
        # Dulu berbunyi "(TAM/segment growth)" -- menyesatkan sejak cabang ini
        # benar-benar hidup (2026-07-31): dua field itu justru satu-satunya yang
        # TIDAK pernah terisi, jadi 45 call yang dapat "lima_tahun" semuanya
        # dipicu acceleration_signal, bukan TAM. Nama field yang sesungguhnya
        # sudah otomatis ditempel di belakang kalimat ini oleh
        # _structural_vs_momentum_horizon, jadi prosanya cukup menyebut
        # KATEGORI-nya -- tetap benar jika TAM/segment_growth suatu saat terisi.
        structural_note="faktor struktural (bukan momentum harga) — ruang tumbuh butuh waktu tercermin penuh di harga",
        momentum_note="momentum terukur — tesis sudah mulai kelihatan, tinggal dikonfirmasi kuartal berikutnya",
        default_note="Horizon default jangka menengah karena tidak ada faktor dominan jelas.",
    )


def _quality_horizon(key_metrics: dict) -> tuple[str, str]:
    return _structural_vs_momentum_horizon(
        key_metrics, _QUALITY_FUNDAMENTAL_KEYS, _QUALITY_VALUATION_KEYS,
        structural_horizon="lima_tahun", momentum_horizon="satu_dua_tahun", default_horizon="lima_tahun",
        structural_note="fundamental murni (margin/current ratio/leverage) — compounding perlu beberapa siklus laporan",
        momentum_note="valuasi/analyst — repricing bisa lebih cepat begitu pasar sadar",
        default_note="Default ke horizon lebih panjang karena tesis lens ini memang jangka panjang.",
    )


def summarize_risk(risk) -> tuple[int, int, list[str]]:
    """(jumlah high, jumlah medium, daftar tipe unik) dari RiskAssessment.
    Cuma MERINGKAS apa yang sudah dihitung risk.py -- tidak menilai ulang dan
    tidak pernah mengubah `action` (action tetap murni dari stance+band, lihat
    ACTION_TABLE). Dipakai untuk peringkat & badge di kartu top pick, supaya
    'cek Risk Flags dulu' tidak lagi jadi beban manual pengguna."""
    if risk is None:
        return 0, 0, []
    red_flags = getattr(risk, "red_flags", None) or []
    high = sum(1 for f in red_flags if getattr(f, "severity", None) == "high")
    medium = sum(1 for f in red_flags if getattr(f, "severity", None) == "medium")
    types: list[str] = []
    for f in red_flags:
        t = getattr(f, "flag_type", None)
        if t and t not in types:
            types.append(t)
    # Flag spesifikasi (severity tinggi/ekstrem) yang benar-benar triggered
    # ikut dihitung sebagai high -- beda daftar, sama artinya buat pengguna.
    for f in getattr(risk, "flags", None) or []:
        if getattr(f, "status", None) == "triggered":
            high += 1
            fid = getattr(f, "flag_id", None)
            if fid and fid not in types:
                types.append(fid)
    return high, medium, types


def _thesis_score_tier(thesis_score: float) -> str:
    """Ganti gerbang ACTION_TABLE per keputusan pengguna 2026-07-29: dulu
    dikunci ke confidence.band, tapi audit menemukan confidence.band=='high'
    TIDAK PERNAH tercapai di seluruh universe (skor overall confidence
    tertinggi yang pernah tercatat: 56.9 dari 4055 ticker, semua kena
    penalti struktural "data belum lengkap" di kategori yang sama --
    competitive_structure/competitive_momentum/ownership/governance).
    Efeknya diam-diam: action penuh (mulai_posisi/akumulasi) untuk
    Multibagger & Quality/Compound SELALU mustahil (0 dari 352/445 kandidat
    yang qualify stance-nya pernah lolos), bukan karena sahamnya kurang
    bagus. thesis_score (kekuatan ARGUMEN, bukan kelengkapan DATA) tidak
    kena batas struktural yang sama -- sudah terbukti tembus 88 di data
    live. confidence.band tetap dipakai terpisah di P4 (validate_personal_
    call) sebagai penjaga "data terlalu buruk buat dipercaya", bukan lagi
    sebagai penentu tingkat action."""
    if thesis_score >= 70:
        return "high"
    if thesis_score >= 50:
        return "medium"
    return "low"


def build_personal_call(
    module_output: "ModuleOutput",
    position_status: str,
    holding_since: str | None,
    unrealized_return_pct: float | None,
    catalyst: "CatalystSet | None" = None,
    today: date | None = None,
    current_price: float | None = None,
    benchmark_price: float | None = None,
    risk=None,
) -> PersonalCall:
    """Bangun satu PersonalCall dari satu ModuleOutput (satu lens). Tidak
    membaca Knowledge/Evidence langsung -- semua yang dibutuhkan sudah ada
    di ModuleOutput (stance, confidence, key_metrics)."""
    today = today or datetime.now(timezone.utc).date()
    module = module_output.module
    band = module_output.confidence.band  # tetap dipakai di P4 + tampilan, BUKAN lagi buat lookup action
    score_tier = _thesis_score_tier(module_output.thesis_score)
    is_unreadable = module_output.stance == UNREADABLE_STANCE[module]

    action = ACTION_TABLE[position_status][module][module_output.stance][score_tier]

    # P4 DITEGAKKAN di sini, bukan cuma dilaporkan belakangan. Sampai
    # 2026-07-29 aturan ini mustahil dilanggar by construction: ACTION_TABLE
    # dulu dikunci ke confidence.band, dan kolom "low" memang tidak pernah
    # berisi action eksposur penuh. Begitu gerbangnya pindah ke thesis_score,
    # band dan kolom tabel jadi terpisah — dan P4 mulai benar-benar dilanggar
    # (44 call pada 2026-07-31) tanpa ada yang tahu, karena `violations` tidak
    # pernah di-log maupun ditampilkan di mana pun. Ini bukan pembatalan
    # keputusan 2026-07-29: docstring _thesis_score_tier sudah menyatakan band
    # tetap dipakai TERPISAH di P4 sebagai penjaga "data terlalu buruk buat
    # dipercaya", bukan lagi penentu tingkat action. Dampaknya kecil & bedah
    # (44 dari 2636 call eksposur penuh) justru karena band "low" jarang
    # berbarengan dengan skor tesis tinggi.
    action_downgraded_from = None
    if band == "low":
        graded = downgrade_full_exposure(action, position_status, module)
        if graded is not None:
            action_downgraded_from = action
            action = graded

    if module == "speculative":
        horizon, horizon_basis, horizon_anchor = _speculative_horizon(catalyst, today)
    elif module == "multibagger":
        horizon, horizon_basis = _multibagger_horizon(module_output.key_metrics)
        horizon_anchor = None
    else:
        horizon, horizon_basis = _quality_horizon(module_output.key_metrics)
        horizon_anchor = None

    horizon_status = _compute_horizon_status(position_status, holding_since, horizon, today)
    risk_high, risk_medium, risk_types = summarize_risk(risk)

    call = PersonalCall(
        module=module,
        ticker=module_output.ticker,
        exchange=module_output.exchange,
        method_version=METHOD_VERSION,
        action=action,
        action_rationale=(
            f"Stance '{module_output.stance}' (skor tesis {module_output.thesis_score:.0f}, "
            f"tingkat {score_tier}): {module_output.stance_rationale}"
            # Klaim amplitudo WAJIB menyatakan batasnya sendiri di rationale.
            # Tanpa kalimat ini, pembaca akan membaca "siaga" sebagai "beli" —
            # persis kekeliruan yang membuat action ini diganti.
            + (" — klaimnya AMPLITUDO, bukan arah: lensa ini terukur bisa menemukan saham "
               "yang akan bergerak melebihi derunya sendiri, tapi tidak terbukti bisa "
               "menebak ke arah mana."
               if action in ACTION_CATEGORY_MAGNITUDE else "")
            + (f" — diturunkan dari '{action_downgraded_from}' karena confidence lensa ini "
               f"'low' (P4: data terlalu tipis untuk eksposur penuh)."
               if action_downgraded_from else "")
        ),
        horizon=horizon,
        horizon_basis=horizon_basis,
        horizon_anchor=horizon_anchor,
        action_downgraded_from=action_downgraded_from,
        source_stance=module_output.stance,
        source_confidence=module_output.confidence.score,
        thesis_score=getattr(module_output, "thesis_score", 50.0),
        price_at_call=current_price,
        benchmark_at_call=benchmark_price,
        risk_flags_high=risk_high,
        risk_flags_medium=risk_medium,
        risk_flag_types=risk_types,
        position_status=position_status,
        holding_since=holding_since,
        unrealized_return_pct=unrealized_return_pct,
        horizon_status=horizon_status,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    call.violations = validate_personal_call(call, is_unreadable, band)
    return call


def current_price_from_evidence(evidence: "EvidencePackage | None") -> float | None:
    """EvidencePackage.price_market.last_price -- dipilih daripada
    KnowledgeProfile.valuation.price_target.current_price karena yang kedua
    cuma terisi kalau evidence.analyst_estimates ada (minoritas ticker saat
    ini, lihat catatan sesi 2026-07-25), sedangkan last_price difetch untuk
    setiap ticker yang lolos Screening."""
    if evidence is None:
        return None
    return evidence.price_market.last_price
