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

import json
import statistics
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .personal_contracts import ACTION_ALIASES
from .personal_evaluation import MODULES
from .personal_historical import call_due_date, run_dates

# Gerbang bukti. Angkanya sengaja tidak dipakai untuk MENYARING apa pun,
# cuma untuk MELABELI — supaya menaikkannya nanti tidak pernah bisa
# menghilangkan data dari layar.
MIN_THESES = 30
MIN_ENTRY_DATES = 5

DIRECTIONAL = ("terbukti", "meleset", "ambigu")


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
                out.append({
                    "ticker": ticker,
                    "module": module,
                    "classification": rec.get("classification"),
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
    setengah benar) dan bernilai None kalau penyebutnya nol — bukan 0."""
    terbukti = sum(1 for r in rows if r["classification"] == "terbukti")
    meleset = sum(1 for r in rows if r["classification"] == "meleset")
    ambigu = sum(1 for r in rows if r["classification"] == "ambigu")
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

    return {
        "n": n,
        "terbukti": terbukti,
        "meleset": meleset,
        "ambigu": ambigu,
        "hit_rate_pct": round(terbukti / base * 100, 1) if base else None,
        "entry_dates": n_dates,
        "entry_date_list": sorted(dates)[:10],
        "median_return_pct": round(statistics.median(returns), 2) if returns else None,
        "median_excess_pct": round(statistics.median(excess), 2) if excess else None,
        "beat_index": sum(1 for x in excess if x > 0),
        "with_excess": len(excess),
        # Dua gerbang, dua alasan terpisah — supaya yang membaca tahu MANA
        # yang kurang, bukan sekadar "belum cukup".
        "evaluable": n >= MIN_THESES and n_dates >= MIN_ENTRY_DATES,
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


# Berapa ticker yang namanya ikut disebut per daftar. Ada batasnya karena
# rotasi awal bisa ratusan (30 Jul: 567 nama baru sekaligus) dan berkas ini
# dikirim utuh ke browser tiap halaman Agregator Pribadi dibuka. Jumlah
# penuhnya tetap dibawa terpisah, jadi yang hilang cuma nama-namanya.
ROTATION_NAME_CAP = 60


def _rotation(timelines: dict[str, dict]) -> dict | None:
    """Apa yang BERGANTI di daftar pick antara run terakhir dan run sebelumnya.

    Ada karena lensa Spekulatif terukur sangat lengket — 85–98% pick tiap run
    sama dengan run sebelumnya — jadi "312 disiagakan hari ini" nyaris tidak
    membawa informasi baru dibanding kemarin, sementara "27 nama baru" membawa
    seluruhnya. Umur per call (streak_runs) menjawab ini satu kartu pada satu
    waktu; blok ini menjawabnya untuk seluruh populasi sekaligus.

    Bukan bagian dari rapor kalibrasi, dan sengaja tidak berpura-pura jadi
    bagiannya. Ikut menumpang di calibration.json karena sumber datanya sama
    persis (seluruh riwayat) dan halaman yang memakainya sudah mengambil berkas
    ini — berkas + endpoint + skrip bangun sendiri untuk tujuh angka dan dua
    daftar cuma menambah tempat untuk lupa.

    None kalau riwayatnya belum punya dua run untuk dibandingkan.
    """
    per_date: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for ticker, timeline in timelines.items():
        for entry in timeline.get("entries", []):
            at = (entry.get("analyzed_at") or "")[:10]
            if not at:
                continue
            call_set = entry.get("personal_call_set") or {}
            for module in MODULES:
                action = (call_set.get(module) or {}).get("action")
                if action and action in ACTION_ALIASES.get(module, frozenset()):
                    per_date[at][module].add(ticker)

    dates = sorted(per_date)
    if len(dates) < 2:
        return None
    latest, previous = dates[-1], dates[-2]

    modules = {}
    for module in MODULES:
        now, before = per_date[latest][module], per_date[previous][module]
        new, dropped = sorted(now - before), sorted(before - now)
        modules[module] = {
            "picks": len(now),
            "new": len(new),
            "continuing": len(now & before),
            "dropped": len(dropped),
            # Bagian dari pick hari ini yang sudah ada kemarin. Angka inilah yang
            # membuat "banyaknya pick" tidak terbaca sebagai "banyaknya temuan".
            "carryover_pct": round(len(now & before) / len(now) * 100, 1) if now else None,
            "new_tickers": new[:ROTATION_NAME_CAP],
            "dropped_tickers": dropped[:ROTATION_NAME_CAP],
            "names_truncated": len(new) > ROTATION_NAME_CAP or len(dropped) > ROTATION_NAME_CAP,
        }

    return {"latest_run": latest, "previous_run": previous, "modules": modules}


def _not_yet_evaluable(timelines: dict[str, dict], evaluated: list[dict], today: date) -> list[dict]:
    """Per modul: berapa tesis sudah divonis, berapa call masih berjalan, dan
    KAPAN paling cepat vonis pertama bisa ada.

    Tanpa blok ini rapor akan terbaca seolah sistem sudah terkalibrasi,
    padahal yang terukur baru satu lensa dari tiga — dua sisanya bukan
    "belum sempat", tapi secara struktural belum mungkin.
    """
    done = defaultdict(int)
    for r in evaluated:
        if r["classification"] in DIRECTIONAL:
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
    """
    earliest = {n["module"]: n.get("earliest_possible_verdict") for n in not_yet}
    out: dict[str, dict] = {}
    for module in MODULES:
        mrows = [r for r in rows if r["module"] == module]
        by_tier: dict[str, dict] = {}
        for tier in {r["score_tier"] for r in mrows}:
            by_tier[tier] = _summarize([r for r in mrows if r["score_tier"] == tier])
        out[module] = {
            **_summarize(mrows),
            "earliest_possible_verdict": earliest.get(module),
            "by_score_tier": by_tier,
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


def _baselines(baselines_path: Path | None) -> dict | None:
    """Pembanding dari `scripts/build_baselines.py`, dibaca apa adanya.

    Kenapa dibaca dari berkas alih-alih dihitung di sini: pengukurannya harus
    membuka bar harga ribuan ticker (~100 berkas/menit di mesin ini), belasan
    menit yang tidak boleh menempel ke tiap run pipeline 3 jam. Konsekuensinya
    angka ini bisa BASI relatif terhadap sisa rapor -- karena itu
    `generated_at`-nya dibawa apa adanya dan `stale_days` dihitung, supaya
    kebasiannya terlihat pembaca alih-alih diam-diam dipercaya.

    None kalau berkasnya belum pernah dibangun. Itu keadaan yang sah dan
    sengaja TIDAK diisi angka pengganti: rapor tanpa pembanding lebih jujur
    daripada rapor dengan pembanding karangan.
    """
    if baselines_path is None or not baselines_path.exists():
        return None
    try:
        payload = json.loads(baselines_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    gen = payload.get("generated_at")
    stale = None
    if gen:
        try:
            stale = (datetime.now(timezone.utc) - datetime.fromisoformat(gen)).days
        except ValueError:
            stale = None
    return {
        "generated_at": gen,
        "stale_days": stale,
        "metric": payload.get("metric"),
        "naive_max_days": payload.get("naive_max_days"),
        "windows": payload.get("windows"),
        "arms": payload.get("arms"),
        "comparisons": payload.get("comparisons"),
        "note": payload.get("note"),
    }


def build_calibration(
    timelines: dict[str, dict],
    today: date | None = None,
    baselines_path: Path | None = None,
) -> dict:
    today = today or datetime.now(timezone.utc).date()
    all_rows = collect_theses(timelines)
    rows = [r for r in all_rows if r["classification"] in DIRECTIONAL]

    miss_reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["miss_reason"]:
            miss_reasons[r["miss_reason"]] += 1

    slices = (
        _slice_by(rows, "modul", lambda r: r["module"])
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
        "miss_reasons": dict(sorted(miss_reasons.items(), key=lambda kv: -kv[1])),
        "slices": slices,
        "not_yet_evaluable": not_yet,
        # "Dibandingkan APA?" -- pertanyaan yang selama ini tidak dijawab rapor
        # ini sama sekali. Hit rate 34% tidak berarti apa pun tanpa titik nol,
        # dan pembanding ACAK saja ternyata terlalu lemah: lensa Spekulatif
        # memilih 93% ticker berkatalis <=7 hari sementara universe cuma 26%,
        # jadi keunggulannya atas acak bisa jadi cuma "memilih perusahaan yang
        # lapor lusa" -- sesuatu yang ditiru satu baris pencarian kalender.
        # None kalau build_baselines.py belum pernah dijalankan.
        "baselines": _baselines(baselines_path),
        # Dipakai kartu tesis hidup (ThesisProof), bukan halaman rapor.
        "base_rates": _base_rates(rows, not_yet),
        "run_dates": run_dates(timelines),
        "rotation": _rotation(timelines),
        "mechanical_tuning": _mechanical_tuning(rows, slices, overall),
    }
