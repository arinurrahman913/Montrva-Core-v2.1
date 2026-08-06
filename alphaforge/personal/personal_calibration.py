"""Rapor kalibrasi — mengubah riwayat vonis jadi bahan belajar.

Isinya BUKAN mesin skor kedua dan tidak menyentuh satu pun keputusan: modul
ini cuma membaca outcome yang sudah ditulis personal_evaluation.py, lalu
mengiris hit rate menurut dimensi yang benar-benar dikendalikan sistem
(modul, stance, action, horizon, tingkat skor tesis, tipe risk flag). Yang
mengubah action tetap cuma ACTION_TABLE.

DUA KEPUTUSAN YANG MEMBENTUK SELURUH MODUL INI:

1. GERBANG BUKTI, DAN GERBANGNYA ADA DUA. Sebuah irisan cuma dinyatakan
   `evaluable` kalau punya >= MIN_THESES tesis DAN >= MIN_ENTRY_DATES tanggal
   masuk yang berbeda. Syarat kedua yang jarang dipasang orang justru yang
   mengikat di sini: diukur atas riwayat live, 167 tesis berarah ternyata
   lahir cuma di 2 tanggal (27 & 29 Jul 2026) — 2 taruhan yang disebar ke
   ~85 ticker, bukan 167 sampel independen. Satu jendela pasar yang buruk
   membuat semuanya meleset bersamaan, dan hit rate 29,9% jadi mengukur dua
   hari pasar, bukan mutu metodenya. Irisan yang tidak lolos tetap
   ditampilkan lengkap dengan angkanya, tapi ditandai belum layak dipakai
   menyetel apa pun — bukan disembunyikan (menyembunyikan bukti lemah bikin
   orang mengira belum ada apa-apa) dan bukan pula dipoles jadi persen yang
   terlihat berwibawa.

2. YANG BELUM BISA DINILAI IKUT DILAPORKAN. Multibagger & Quality/Compound
   punya NOL tesis terevaluasi dan memang belum mungkin punya: horizonnya
   730 & 1825 hari. Rapor yang cuma memuat baris berisi angka akan terbaca
   seolah-olah sistem sudah terkalibrasi, padahal yang terukur baru satu
   lensa dari tiga. `not_yet_evaluable` menyebut tanggal paling awal sebuah
   vonis bisa ada, dihitung dari call yang sedang berjalan.

Dipanggil dari refresh_full_pipeline.py (timelines sudah di memori di sana,
jadi nol biaya baca) dan dari scripts/build_calibration.py untuk membangun
ulang tanpa menjalankan pipeline 3-4 jam.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timezone

from .personal_evaluation import MODULES, claim_type as claim_type_of
from .personal_historical import call_due_date

# Gerbang bukti. Angkanya sengaja tidak dipakai untuk MENYARING apa pun,
# cuma untuk MELABELI — supaya menaikkannya nanti tidak pernah bisa
# menghilangkan data dari layar.
MIN_THESES = 30
MIN_ENTRY_DATES = 5

# Vonis yang benar-benar menjawab klaim sebuah tesis. Namanya dulu DIRECTIONAL
# karena cuma ada satu jenis klaim; sejak `siaga_gerakan` ada, kosakatanya sama
# tapi aturan yang menghasilkannya berbeda per jenis klaim (lihat
# `operative_verdict`). Alias lama dipertahankan di bawah supaya pemanggil luar
# tidak putus.
VERDICTS = ("terbukti", "meleset", "ambigu")
DIRECTIONAL = VERDICTS

CLAIM_TYPES = ("arah", "amplitudo")

# Label yang dipakai saat sebuah irisan tidak menyebut jenis klaimnya.
CLAIM_LABEL = {
    "arah": "klaim arah (vonis v1, target absolut)",
    "amplitudo": "klaim amplitudo (vonis v2, |z| >= 1)",
}


def operative_verdict(rec: dict, action: str | None) -> tuple[str | None, str]:
    """(vonis yang berlaku, jenis klaim) untuk satu record outcome.

    INI PERBAIKAN INTI AUDIT 2026-08-06 (D1). Modul ini dulu membaca
    `classification` (vonis v1) untuk SEMUA tesis. Sejak `0f3f337` lensa
    Spekulatif mengeluarkan `siaga_gerakan`, yang mengklaim AMPLITUDO, dan v1
    dengan benar memberinya "tidak_berlaku" — v1 menilai arah terhadap target
    absolut, pertanyaan yang action ini tidak pernah jawab. Tapi
    "tidak_berlaku" tidak ada di VERDICTS, jadi seluruh tesis Spekulatif baru
    terbuang sebelum diiris apa pun, sementara vonis yang sebenarnya
    (`classification_v2`) tidak dibaca satu baris pun di seluruh repo.

    Akibatnya rapor ini membeku: Spekulatif adalah satu-satunya lensa yang bisa
    jatuh tempo tahun ini (Multibagger 730 hari, Quality 1825 hari), jadi
    bukti berhenti bertambah sama sekali — sambil rapornya tetap terlihat
    terisi karena angka lamanya masih ada.

    Yang dikembalikan di sini adalah vonis yang menjawab klaim tesis ITU:
    klaim arah dinilai v1 (itu yang dijanjikan sistem saat call dibuat), klaim
    amplitudo dinilai v2. Keduanya TIDAK PERNAH dijumlahkan jadi satu angka —
    `claim_type` ikut dikembalikan supaya pemanggil wajib memisahkannya, persis
    peringatan di docstring `personal_evaluation.claim_type`.

    `claim_type` dibaca dari record kalau ada, dan diturunkan dari action kalau
    tidak — record hasil backfill dan record yang ditulis sebelum field itu ada
    tidak menyimpannya.
    """
    kind = rec.get("claim_type") or (claim_type_of(action) if action else "arah")
    if kind == "amplitudo":
        return rec.get("classification_v2"), kind
    if kind == "tanpa_klaim":
        return rec.get("classification"), kind
    return rec.get("classification"), "arah"


def _score_tier(score: float | None) -> str:
    """Sama persis dengan gerbang di personal_reasoning._thesis_score_tier
    (>=70 high, >=50 medium, sisanya low). Irisan ini yang menjawab
    pertanyaan paling langsung tentang gerbang itu: apakah tesis 'high'
    benar-benar lebih sering terbukti daripada 'medium'? Kalau tidak,
    gerbangnya tidak memisahkan apa-apa."""
    if score is None:
        return "skor tidak tersimpan"
    if score >= 70:
        return "high (skor >=70)"
    if score >= 50:
        return "medium (skor 50-69)"
    return "low (skor <50)"


def collect_theses(timelines: dict[str, dict]) -> list[dict]:
    """Satu baris per TESIS unik, bukan per snapshot harian.

    Kunci dedupe WAJIB diawali ticker: `thesis_key` yang ditulis
    personal_evaluation.py berbentuk "{module}:{tanggal mulai}" — unik di
    dalam satu timeline, TIDAK unik lintas ticker. Memakainya mentah membuat
    seluruh tesis Spekulatif yang mulai di tanggal sama pada ribuan ticker
    runtuh jadi satu baris (bug nyata yang pernah membuat kartu "Track
    Record" di frontend dihitung dari 2 sampel).
    """
    seen: set[str] = set()
    out: list[dict] = []
    for ticker, tl in timelines.items():
        for entry in (tl.get("entries", []) if isinstance(tl, dict) else []):
            outcome = entry.get("outcome") or {}
            calls = entry.get("personal_call_set") or {}
            for module, rec in outcome.items():
                if not isinstance(rec, dict):
                    continue
                fallback = f"{module}:{entry.get('analyzed_at')}"
                key = f"{ticker}:{rec.get('thesis_key') or fallback}"
                if key in seen:
                    continue
                seen.add(key)
                call = calls.get(module) or {}
                score = call.get("thesis_score")
                if isinstance(score, dict):  # bentuk lama/berbreakdown
                    score = score.get("score")
                verdict, kind = operative_verdict(rec, call.get("action"))
                out.append({
                    "ticker": ticker,
                    "module": module,
                    # `verdict` yang dipakai seluruh agregasi di bawah;
                    # `classification`/`classification_v2` dibawa apa adanya
                    # supaya vonis mentah tetap bisa diperiksa.
                    "verdict": verdict,
                    "claim_type": kind,
                    "classification": rec.get("classification"),
                    "classification_v2": rec.get("classification_v2"),
                    "z_excess": rec.get("z_excess"),
                    "miss_reason": rec.get("miss_reason"),
                    "return_pct": rec.get("return_pct"),
                    "excess_pct": rec.get("excess_return_pct"),
                    "entry_date": rec.get("entry_date"),
                    "horizon": call.get("horizon"),
                    "action": call.get("action"),
                    "stance": call.get("source_stance"),
                    "score_tier": _score_tier(score),
                    "risk_flag_types": call.get("risk_flag_types") or [],
                })
    return out


def _summarize(rows: list[dict]) -> dict:
    """Ringkasan satu irisan. `hit_rate_pct` sengaja dihitung atas
    terbukti+meleset saja (ambigu dikeluarkan dari penyebut, bukan dihitung
    setengah benar) dan bernilai None kalau penyebutnya nol — bukan 0.

    Membaca `verdict` (vonis yang menjawab klaim tesis itu), bukan
    `classification` mentah — lihat `operative_verdict`. `claim_types` ikut
    dilaporkan supaya irisan yang tanpa sengaja mencampur dua jenis klaim
    KETAHUAN dari datanya sendiri, bukan cuma dilarang lewat komentar.
    """
    terbukti = sum(1 for r in rows if r["verdict"] == "terbukti")
    meleset = sum(1 for r in rows if r["verdict"] == "meleset")
    ambigu = sum(1 for r in rows if r["verdict"] == "ambigu")
    base = terbukti + meleset
    dates = {r["entry_date"] for r in rows if r["entry_date"]}
    returns = [r["return_pct"] for r in rows if r["return_pct"] is not None]
    excess = [r["excess_pct"] for r in rows if r["excess_pct"] is not None]

    n = len(rows)
    n_dates = len(dates)
    limiters = []
    if n < MIN_THESES:
        limiters.append(f"cuma {n} tesis (butuh {MIN_THESES})")
    if n_dates < MIN_ENTRY_DATES:
        limiters.append(f"cuma {n_dates} tanggal masuk (butuh {MIN_ENTRY_DATES})")

    claim_types = sorted({r["claim_type"] for r in rows if r.get("claim_type")})
    if len(claim_types) > 1:
        limiters.append(
            "mencampur " + " + ".join(CLAIM_LABEL.get(c, c) for c in claim_types)
            + " — hit rate gabungannya tidak berarti"
        )

    return {
        "n": n,
        "terbukti": terbukti,
        "meleset": meleset,
        "ambigu": ambigu,
        "claim_types": claim_types,
        "hit_rate_pct": round(terbukti / base * 100, 1) if base else None,
        "entry_dates": n_dates,
        "entry_date_list": sorted(dates)[:10],
        "median_return_pct": round(statistics.median(returns), 2) if returns else None,
        "median_excess_pct": round(statistics.median(excess), 2) if excess else None,
        "beat_index": sum(1 for x in excess if x > 0),
        "with_excess": len(excess),
        # Dua gerbang, dua alasan terpisah — supaya yang membaca tahu MANA
        # yang kurang, bukan sekadar "belum cukup".
        "evaluable": n >= MIN_THESES and n_dates >= MIN_ENTRY_DATES and len(claim_types) <= 1,
        "limiters": limiters,
    }


def _slice_by(rows: list[dict], dimension: str, keyfn) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        k = keyfn(r)
        if k is None:
            continue
        if isinstance(k, list):
            for kk in k:
                buckets[str(kk)].append(r)
        else:
            buckets[str(k)].append(r)
    out = []
    for k, rs in buckets.items():
        out.append({"dimension": dimension, "key": k, **_summarize(rs)})
    return sorted(out, key=lambda d: -d["n"])


def _not_yet_evaluable(timelines: dict[str, dict], evaluated: list[dict], today: date) -> list[dict]:
    """Per modul: berapa tesis sudah divonis, berapa call masih berjalan, dan
    KAPAN paling cepat vonis pertama bisa ada.

    Tanpa blok ini rapor akan terbaca seolah sistem sudah terkalibrasi,
    padahal yang terukur baru satu lensa dari tiga — dua sisanya bukan
    "belum sempat", tapi secara struktural belum mungkin.
    """
    done = defaultdict(int)
    for r in evaluated:
        if r["verdict"] in VERDICTS:
            done[r["module"]] += 1

    active = defaultdict(int)
    earliest: dict[str, date] = {}
    for tl in timelines.values():
        entries = tl.get("entries", []) if isinstance(tl, dict) else []
        if not entries:
            continue
        last = entries[-1]
        analyzed = last.get("analyzed_at")
        for module, call in (last.get("personal_call_set") or {}).items():
            if not isinstance(call, dict):
                continue
            active[module] += 1
            due = call_due_date(call, analyzed)
            if due and due > today and (module not in earliest or due < earliest[module]):
                earliest[module] = due

    return [
        {
            "module": m,
            "evaluated": done.get(m, 0),
            "active_calls": active.get(m, 0),
            "earliest_possible_verdict": earliest[m].isoformat() if m in earliest else None,
        }
        for m in MODULES
    ]


def _base_rates(rows: list[dict], not_yet: list[dict]) -> dict:
    """Angka yang ditempel ke KARTU TESIS HIDUP, bukan ke halaman rapor.

    Irisan silang modul x tingkat skor — sengaja tidak diambil dari `slices`
    (yang tiap dimensinya berdiri sendiri): kartu tesis punya modul DAN skor
    sekaligus, dan "Spekulatif 29,9%" jauh kurang berguna daripada
    "Spekulatif berskor >=70: 25%". Frontend memakai yang paling spesifik
    yang punya isi, lalu jatuh ke modul.

    Modul tanpa satu pun tesis terevaluasi tetap dapat entri berisi
    `earliest_possible_verdict` — untuk Multibagger & Quality/Compound itu
    justru pesan yang paling berguna di kartu ("belum ada yang pernah jatuh
    tempo, paling cepat 2027-07-27"), bukan kekosongan yang dibiarkan
    terbaca seolah tidak ada informasi.

    DIPECAH PER JENIS KLAIM (audit 2026-08-06, D2). Kartu tesis menempelkan
    angka ini tepat di bawah action-nya, dan sejak `siaga_gerakan` ada, action
    di kartu bisa mengklaim AMPLITUDO sementara seluruh base rate yang tersedia
    berasal dari tesis ARAH lama (`masuk_spekulatif`, target absolut 3%). Dua
    pertanyaan berbeda, dan yang lama kebetulan berangka jauh lebih besar
    (~30% vs base rate |z| >= 1 sekitar 16%) — jadi kartu yang tidak pernah
    menjanjikan arah tetap memamerkan hit rate tesis berarah. Frontend
    sekarang WAJIB memilih lewat `by_claim_type`, dan kalau jenis klaim kartu
    itu belum punya tesis jatuh tempo, yang benar adalah mengatakannya, bukan
    jatuh ke angka jenis klaim lain.
    """
    earliest = {n["module"]: n.get("earliest_possible_verdict") for n in not_yet}
    out: dict[str, dict] = {}

    def _with_tiers(subset: list[dict]) -> dict:
        return {
            **_summarize(subset),
            "by_score_tier": {
                tier: _summarize([r for r in subset if r["score_tier"] == tier])
                for tier in {r["score_tier"] for r in subset}
            },
        }

    for module in MODULES:
        mrows = [r for r in rows if r["module"] == module]
        out[module] = {
            **_with_tiers(mrows),
            "earliest_possible_verdict": earliest.get(module),
            "by_claim_type": {
                kind: _with_tiers([r for r in mrows if r["claim_type"] == kind])
                for kind in CLAIM_TYPES
            },
        }
    return out


def _mechanical_tuning(rows: list[dict], slices: list[dict], overall: dict) -> dict:
    """Kapan angka di rapor ini boleh MENGUBAH sistem, bukan cuma menandai.

    Ditulis sebagai kode yang memeriksa dirinya sendiri, bukan sebagai janji
    di dokumen. Alasannya praktis: syarat yang cuma hidup di kepala akan
    dilanggar diam-diam saat angkanya kebetulan terlihat meyakinkan, dan
    proyek ini sudah beberapa kali kena kelas kesalahan itu (penalti konstan
    yang tidak membedakan ticker; arah mean dibaca sebagai arah hitungan
    gerbang). Kalau `allowed` masih False, tidak ada threshold/bobot/gerbang
    yang boleh disetel dari sini — dan alasannya bisa dibaca per syarat.

    Syarat ketiga sengaja menuntut lensa NON-spekulatif: seluruh bukti yang
    ada hari ini berasal dari satu lensa berhorizon 7 hari. Menyetel gerbang
    skor yang dipakai bertiga dari bukti satu lensa berarti memindahkan
    pelajaran jangka pendek ke keputusan 5 tahunan.
    """
    non_spec = [r for r in rows if r["module"] != "speculative"]
    evaluable_slices = [s for s in slices if s["evaluable"]]
    conditions = [
        {
            "label": f"minimal satu irisan lolos gerbang bukti (n>={MIN_THESES} & >={MIN_ENTRY_DATES} tanggal masuk)",
            "met": bool(evaluable_slices),
            "detail": f"{len(evaluable_slices)} dari {len(slices)} irisan lolos",
        },
        {
            "label": f"minimal {MIN_ENTRY_DATES} tanggal masuk berbeda di seluruh riwayat",
            "met": overall["entry_dates"] >= MIN_ENTRY_DATES,
            "detail": f"{overall['entry_dates']} tanggal masuk",
        },
        {
            "label": "minimal satu lensa non-spekulatif sudah punya tesis yang jatuh tempo",
            "met": bool(non_spec),
            "detail": f"{len(non_spec)} tesis dari Multibagger + Quality/Compound",
        },
    ]
    return {"allowed": all(c["met"] for c in conditions), "conditions": conditions}


def build_calibration(timelines: dict[str, dict], today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    all_rows = collect_theses(timelines)
    rows = [r for r in all_rows if r["verdict"] in VERDICTS]

    miss_reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["miss_reason"]:
            miss_reasons[r["miss_reason"]] += 1

    slices = (
        # Jenis klaim didahulukan: ini satu-satunya dimensi yang irisannya
        # TIDAK boleh dijumlahkan dengan irisan lain di dimensi yang sama
        # (lihat operative_verdict), jadi ia harus terbaca lebih dulu.
        _slice_by(rows, "jenis klaim", lambda r: CLAIM_LABEL.get(r["claim_type"], r["claim_type"]))
        + _slice_by(rows, "modul", lambda r: r["module"])
        + _slice_by(rows, "horizon", lambda r: r["horizon"])
        + _slice_by(rows, "action", lambda r: r["action"])
        + _slice_by(rows, "stance", lambda r: r["stance"])
        + _slice_by(rows, "tingkat skor tesis", lambda r: r["score_tier"])
        + _slice_by(rows, "tanggal masuk", lambda r: r["entry_date"])
        # Satu tesis bisa membawa beberapa flag, jadi baris-baris di dimensi
        # ini TIDAK saling lepas dan jumlahnya boleh melebihi total tesis.
        # "(tanpa flag)" dijadikan bucket eksplisit supaya ada pembanding —
        # tanpa itu "high_debt 16%" tidak bisa dibaca sebagai baik/buruk.
        + _slice_by(rows, "tipe risk flag", lambda r: r["risk_flag_types"] or ["(tanpa flag)"])
    )

    not_yet = _not_yet_evaluable(timelines, all_rows, today)
    overall = _summarize(rows)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_gate": {"min_theses": MIN_THESES, "min_entry_dates": MIN_ENTRY_DATES},
        "theses_total": len(all_rows),
        "theses_directional": len(rows),
        "overall": overall,
        # `overall` di atas menjumlahkan dua jenis klaim, jadi hit rate-nya
        # sengaja ditandai tidak layak pakai lewat `limiters`/`evaluable`
        # (lihat _summarize). Angka yang boleh dibaca ada di sini, terpisah.
        "overall_by_claim_type": {
            kind: _summarize([r for r in rows if r["claim_type"] == kind])
            for kind in CLAIM_TYPES
        },
        "miss_reasons": dict(sorted(miss_reasons.items(), key=lambda kv: -kv[1])),
        "slices": slices,
        "not_yet_evaluable": not_yet,
        # Dipakai kartu tesis hidup (ThesisProof), bukan halaman rapor.
        "base_rates": _base_rates(rows, not_yet),
        "mechanical_tuning": _mechanical_tuning(rows, slices, overall),
    }
