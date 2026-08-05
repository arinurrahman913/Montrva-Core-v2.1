"""Full pipeline refresh: Screening -> Evidence -> Knowledge -> Peer ->
Confidence -> Risk -> Reasoning -> Aggregator -> Historical, plus Layer 1
with a complete price_cache (so market_breadth/market_sentiment get a real
reading too). Meant to run once/day via Task Scheduler (task
"AlphaForge-FullPipeline-Refresh").

Runs every stage in-process — calls the same run_*() functions directly,
not 9 separate `python -m alphaforge.cli ...` subprocess invocations —
so there's no repeated interpreter startup and no JSON-serialize-then-
deserialize round trip between stages.

All-or-nothing: nothing is written to dashboard/data/ (or the tracked root
reference snapshots) until every stage above has succeeded. A pipeline
that fails partway through must never leave the dashboard in a state
where e.g. Aggregator references a ticker Knowledge doesn't have yet —
that's a worse failure mode than just leaving yesterday's (consistent)
data in place and trying again at the next scheduled run.

This includes price_target_history.json and catalyst_history.json (audit
2026-07-30 item C1/C10): sync_price_target_history()/sync_catalyst_history()
only COMPUTE the updated store now, in-memory, right after Evidence/Catalyst
run — the actual save_*_store() disk write happens down in the
all-succeeded block below, same as every other stage file. They used to
write immediately on call, which meant a failure in any LATER stage still
left these two files advanced to today while the rest of dashboard/data/
stayed on yesterday — /api/ticker/<t> would merge the two with no way for
anyone to detect the mismatch, and catalyst_history's internal diff-state
would get "used up" against a run that never produced a matching
catalysts.json.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alphaforge import runlock  # noqa: E402
from alphaforge.json_safe import dump_safe  # noqa: E402
from alphaforge.layer1 import historical as layer1_historical  # noqa: E402
from alphaforge.layer1.pipeline import build_market_context_package  # noqa: E402
from alphaforge.layer1.benchmark_history import sync_benchmark_history  # noqa: E402
from alphaforge.layer2.screening import run_screening  # noqa: E402
from alphaforge.layer2.evidence import run_evidence  # noqa: E402
from alphaforge.layer2.price_target import sync_price_target_history, save_price_target_store  # noqa: E402
from alphaforge.layer2.knowledge import run_knowledge  # noqa: E402
from alphaforge.layer2.catalyst import run_catalyst  # noqa: E402
from alphaforge.layer2.catalyst_history import sync_catalyst_history, save_catalyst_history_store  # noqa: E402
from alphaforge.layer2.institutional_flow import run_institutional_flow  # noqa: E402
from alphaforge.layer2.peer import run_peer_comparison  # noqa: E402
from alphaforge.layer2.confidence import run_confidence  # noqa: E402
from alphaforge.layer2.risk import run_risk_assessment  # noqa: E402
from alphaforge.layer2.reasoning import run_reasoning_pipeline  # noqa: E402
from alphaforge.layer2.aggregator import run_aggregator  # noqa: E402
from alphaforge.layer2.historical import load_historical_timeline, update_timeline  # noqa: E402
from alphaforge.layer2 import source_health  # noqa: E402

# Lapisan pribadi -- OPSIONAL secara desain (lihat alphaforge/personal/
# __init__.py docstring). Kalau folder ini dihapus (mis. rilis publik),
# pipeline publik di bawah harus tetap jalan utuh tanpa personal_calls/
# personal_history sama sekali -- itulah yang membuat pemisahannya nyata,
# bukan cuma klaim di dokumen.
try:
    from alphaforge.personal import (  # noqa: E402
        load_holdings, build_personal_call_sets,
        load_personal_history, update_personal_timeline, save_personal_history,
        evaluate_due_entries,
    )
    PERSONAL_ENABLED = True
except ImportError:
    PERSONAL_ENABLED = False

DATA_DIR = ROOT / "dashboard" / "data"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# run_screening(limit=None) scans the ENTIRE cheap-filter survivor list
# (5000+ tickers as of this writing) — this is now the default (full-market
# scan) instead of the old 60-ticker sample. Evidence/Yahoo calls are rate
# limited (see sources/yahoo_evidence.py) to survive the resulting scale.
# Override via SCREENING_LIMIT env var (int) to cap it back down for testing.
_screening_limit_raw = os.environ.get("SCREENING_LIMIT")
SCREENING_LIMIT = int(_screening_limit_raw) if _screening_limit_raw else None

# SCREENING_SECTOR: filter ke satu sektor GICS (mis. "Technology") pakai
# sector_map cache (lihat alphaforge/layer2/sources/sector_map.py +
# scripts/build_sector_map.py) — supaya screening harian tidak harus scan
# seluruh universe kalau user cuma mau fokus satu sektor lewat dashboard.
SCREENING_SECTOR = os.environ.get("SCREENING_SECTOR") or None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "refresh_full_pipeline.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("refresh_full_pipeline")


# Di atas ambang ini, file ditulis TANPA indent. `indent=2` membengkakkan
# keluaran ~30-40% (satu baris + spasi per field) — ratusan MB ekstra yang
# tidak pernah dibaca manusia (semua konsumennya JSON.parse / json.load).
# File kecil tetap di-indent supaya enak di-diff.
_COMPACT_WRITE_THRESHOLD_ITEMS = 500


def _is_big_payload(data) -> bool:
    """Apakah struktur ini cukup besar untuk ditulis tanpa indent.

    Versi pertama hanya memindai list di level atas
    (`any(isinstance(v, list) and len(v) > N for v in data.values())`) dan
    MELEWATKAN justru berkas terbesar di sistem: `historical_timeline.json`
    berbentuk `{ticker: {...}}` — ribuan nilai dict, nol list level atas —
    sehingga 469MB ditulis ber-indent. Berkas itu bertambah ~82MB tiap run,
    jadi dalam belasan run ia melewati 1.5GB dan MemoryError yang sama
    (yang perbaikan ini justru dibuat untuk mencegahnya) kembali muncul,
    hanya pindah berkas — dan tetap di tulis TERAKHIR, setelah 86 menit kerja.

    Sekarang memeriksa dict maupun list, di level atas maupun satu tingkat
    di dalamnya, jadi baik `{ticker: {...}}` maupun `{"packages": [...]}`
    sama-sama tertangkap.
    """
    if isinstance(data, (list, tuple)):
        return len(data) > _COMPACT_WRITE_THRESHOLD_ITEMS
    if not isinstance(data, dict):
        return False
    if len(data) > _COMPACT_WRITE_THRESHOLD_ITEMS:
        return True
    return any(isinstance(v, (list, tuple, dict)) and len(v) > _COMPACT_WRITE_THRESHOLD_ITEMS
               for v in data.values())


def _atomic_write(path: Path, data: dict) -> None:
    """Tulis JSON secara atomik (tmp + os.replace) dan hemat memori.

    Streaming lewat `dump_safe` (bukan merakit string raksasa lalu write_text)
    — lihat docstring `alphaforge.json_safe.dump_safe` untuk kenapa: run
    2026-07-30 mati MemoryError di titik tulis evidence.json.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            dump_safe(data, f, indent=None if _is_big_payload(data) else 2,
                      ensure_ascii=False)
    except BaseException:
        # Tanpa ini, kegagalan di tengah tulis (MemoryError, disk penuh,
        # Ctrl+C) meninggalkan .tmp ratusan MB di volume yang sama — persis
        # ruang yang dibutuhkan percobaan berikutnya.
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


def main() -> int:
    # Kunci eksklusif: run ini menulis 10 file tahap di AKHIR, jadi run kedua
    # yang tumpang-tindih bisa meninggalkan campuran dua sesi di
    # dashboard/data/ -- persis yang dideteksi /api/consistency. Retry di
    # cache.py tidak menolong untuk itu, karena kedua penulisan sama-sama
    # "berhasil". Beda dari refresh_layer1.py, di sini exit NON-NOL: run penuh
    # selalu dipicu sengaja (manual atau tombol Generate), jadi kalau ditolak
    # pemanggilnya memang perlu tahu.
    force = "--force-unlock" in sys.argv
    try:
        runlock.acquire("refresh_full_pipeline.py", force=force)
    except runlock.AlreadyRunning as exc:
        log.error(str(exc))
        return 2

    try:
        return _run()
    finally:
        runlock.release()


def _run() -> int:
    started = datetime.now(timezone.utc)
    session_id = f"session-{started.strftime('%Y%m%dT%H%M%S')}"
    log.info(f"Full pipeline refresh started (session_id={session_id})")

    try:
        screening_result, price_cache = run_screening(limit=SCREENING_LIMIT, sector=SCREENING_SECTOR)
        sector_note = f" (sektor: {SCREENING_SECTOR})" if SCREENING_SECTOR else ""
        log.info(f"Screening{sector_note}: {len(screening_result.passed)} passed / {screening_result.universe_scanned} scanned")

        # Layer 1 dipindah ke sini (sebelumnya dihitung paling akhir) supaya
        # Confidence bisa membaca komponen mana yang degraded/missing saat
        # profil-profil ini disusun (ConfidenceReport.context_penalty, Data
        # Contracts §5c) — build_market_context_package cuma butuh
        # price_cache dari Screening, jadi tidak ada dependency baru yang
        # dibutuhkan lebih awal dari ini.
        layer1_pkg = build_market_context_package(price_cache=price_cache)
        n_ok = sum(1 for c in layer1_pkg.components.values() if c.status == "ok")
        log.info(f"Layer 1: {n_ok}/{len(layer1_pkg.components)} ok")
        components_degraded = [
            name for name, c in layer1_pkg.components.items() if c.status != "ok"
        ]

        # Deret penutupan indeks yang menumpuk lintas run. DI LUAR gerbang
        # PERSONAL_ENABLED dengan sengaja: isinya data publik (^GSPC) dan
        # gunanya justru menumpuk jangka panjang, jadi rilis publik pun harus
        # tetap mengumpulkannya. Nol fetch baru — bar-nya dari spx_history
        # yang sudah dihitung build_market_context_package di atas.
        benchmark_series = sync_benchmark_history(
            getattr(layer1_pkg, "spx_history", None), DATA_DIR / "benchmark_history.json",
        )
        log.info(
            f"Benchmark: deret indeks {len(benchmark_series)} bar"
            + (f" ({min(benchmark_series)} s/d {max(benchmark_series)})" if benchmark_series else " — KOSONG")
        )

        evidence_packages = run_evidence(screening_result)
        log.info(f"Evidence: {len(evidence_packages)} packages")

        # Yahoo has no free historical price-target time series — this
        # computes today's snapshot/ticker against the on-disk store (in
        # memory only, see save_price_target_store() call further down) and
        # attaches the accumulated series back onto each package, so
        # Knowledge (next) sees the up-to-date history for its 3-month trend
        # calc.
        pt_store = sync_price_target_history(evidence_packages, DATA_DIR / "price_target_history.json")
        n_pt = sum(1 for p in evidence_packages if p.analyst_estimates and p.analyst_estimates.target_mean is not None)
        log.info(f"Price target: {n_pt} tickers snapshotted ({len(pt_store)} tracked total)")

        profiles = run_knowledge(evidence_packages, screening_result.passed)
        log.info(f"Knowledge: {len(profiles)} profiles")

        catalysts = run_catalyst([p.ticker for p in profiles])
        # Satu snapshot tidak cukup buat tahu delayed/completed/cancelled —
        # bandingkan ke state kemarin (lihat catalyst_history.py docstring).
        evidence_by_ticker = {p.ticker: p for p in evidence_packages}
        ch_store = sync_catalyst_history(catalysts, DATA_DIR / "catalyst_history.json", evidence_by_ticker)
        n_resolved = sum(len(v) for v in ch_store["resolved"].values())
        catalyst_map = {c.ticker: c for c in catalysts}
        n_cat = sum(1 for c in catalysts if c.has_upcoming)
        log.info(f"Catalyst: {len(catalysts)} sets ({n_cat} with upcoming catalyst, {n_resolved} resolved in history)")

        # Agregasi populasi atas top_holders yang sudah ada di evidence_packages
        # -- nol panggilan jaringan, nol perhitungan ulang dari Knowledge.
        # Dijalankan setelah Knowledge karena butuh peta ticker->sektor untuk
        # roll-up sektornya (Evidence sendiri tidak menyimpan sektor seragam).
        inst_flow = run_institutional_flow(evidence_packages, profiles)
        log.info(
            f"Institutional flow: {inst_flow.tickers_with_basis}/{inst_flow.tickers_with_holders} "
            f"saham punya arah, net ${inst_flow.net_value_usd / 1e9:,.1f}B "
            f"di {len(inst_flow.sectors)} sektor"
        )

        comparisons = run_peer_comparison(profiles, screening_result.passed)
        log.info(f"Peer: {len(comparisons)} comparisons")

        confidences = run_confidence(profiles, comparisons, components_degraded)
        log.info(f"Confidence: {len(confidences)} scores")

        risks = run_risk_assessment(profiles)
        log.info(f"Risk: {len(risks)} assessments")

        confidence_map = {c.ticker: c for c in confidences}
        risk_map = {r.ticker: r for r in risks}
        peer_map = {c.ticker: c for c in comparisons}
        # Severity=ekstrem triggered -> halted -> tidak diteruskan ke 3 modul
        # reasoning sama sekali (04_RISK_REDFLAG_CHECK.md "Cara Kerja"),
        # bukan cuma ditandai setelah reasoning tetap jalan. Selalu False
        # hari ini (tidak ada check yang bisa capai ekstrem, lihat Fase 3)
        # tapi jalur ini harus tetap benar untuk saat data-nya ada nanti.
        reasonings = [
            run_reasoning_pipeline(
                p, confidence_map.get(p.ticker), risk_map.get(p.ticker),
                peer_map.get(p.ticker), catalyst_map.get(p.ticker), layer1_pkg,
            )
            for p in profiles
            if not (risk_map.get(p.ticker) and risk_map[p.ticker].halted)
        ]
        log.info(f"Reasoning: {len(reasonings)} outputs")

        recommendations = run_aggregator(
            profiles, comparisons, confidences, risks, reasonings, catalysts, session_id,
        )
        log.info(f"Aggregator: {len(recommendations)} recommendations")

        timelines = load_historical_timeline(str(DATA_DIR / "historical_timeline.json"))
        timelines = update_timeline(timelines, recommendations)
        log.info(f"Historical: {len(timelines)} tickers tracked")

        # Lapisan pribadi (opsional) -- baca reasonings+catalyst_map+evidence
        # yang SUDAH dihitung di atas, tidak menghitung ulang apa pun dari
        # Knowledge (draft §10: "tidak ada mesin skor kedua"). Kegagalan di
        # sini TIDAK boleh menggagalkan pipeline publik -- dicoba, dicatat,
        # dilanjut, bukan try/except di level Exception luar yang sama.
        personal_call_sets = []
        personal_timelines = {}
        if PERSONAL_ENABLED:
            try:
                holdings = load_holdings(DATA_DIR / "personal" / "holdings.json")
                # Titik banding indeks: harga S&P 500 terakhir dari Layer 1
                # (spx_history sudah dihitung di build_market_context_package
                # jauh di atas — tidak ada fetch baru di sini). Disimpan per
                # call supaya outcome nanti bisa dibandingkan dengan "kalau
                # uangnya ditaruh di indeks saja" pada jendela yang sama.
                spx_hist = getattr(layer1_pkg, "spx_history", None) or []
                benchmark_price = spx_hist[-1].get("price") if spx_hist else None
                if benchmark_price is None:
                    # Kegagalan ini pernah DIAM selama berminggu-minggu:
                    # benchmark_at_call kosong di 4.050/4.050 call (audit
                    # 2026-08-04) karena spx_history kebetulan kosong saat run
                    # itu, dan tidak ada satu baris log pun yang menyebutnya.
                    # Akibatnya excess_return_pct null di SELURUH riwayat.
                    log.warning(
                        "Personal: spx_history kosong -> benchmark_at_call tidak terisi run ini; "
                        "excess return tesis yang lahir hari ini akan bergantung pada "
                        "benchmark_history.json saat dievaluasi nanti"
                    )
                personal_call_sets = build_personal_call_sets(
                    reasonings, holdings, catalyst_map, evidence_by_ticker,
                    risk_map=risk_map, benchmark_price=benchmark_price,
                )
                log.info(f"Personal: {len(personal_call_sets)} call sets ({len(holdings)} holdings loaded)")

                # P1-P5 sekarang benar-benar dilaporkan. Sebelumnya
                # PersonalCall.violations dihitung tiap run, ikut ditulis ke
                # personal_calls.json, lalu TIDAK PERNAH dibaca siapa pun --
                # tidak di-log di sini, tidak dirender di frontend. Akibatnya 44
                # pelanggaran P4 berjalan diam-diam berhari-hari (audit
                # 2026-07-31). Sesudah P4 ditegakkan di build_personal_call,
                # angka ini SEHARUSNYA 0; kalau muncul lagi berarti regresi
                # nyata -- persis fungsi "pengaman regresi" yang dijanjikan
                # docstring validate_personal_call. Pola log-tapi-tidak-halt
                # sama dengan validate_module_output di reasoning.py.
                _p_violations = [
                    (cs.ticker, m, getattr(cs, m).violations)
                    for cs in personal_call_sets
                    for m in ("multibagger", "quality_compound", "speculative")
                    if getattr(cs, m).violations
                ]
                _n_downgraded = sum(
                    1 for cs in personal_call_sets
                    for m in ("multibagger", "quality_compound", "speculative")
                    if getattr(cs, m).action_downgraded_from
                )
                if _n_downgraded:
                    log.info(f"Personal: {_n_downgraded} action diturunkan ke versi bertahap (P4, confidence lensa 'low')")
                if _p_violations:
                    log.warning(f"Personal: {len(_p_violations)} pelanggaran P1-P5 TERSISA setelah penegakan -- ini regresi, bukan kondisi normal")
                    for _t, _m, _v in _p_violations[:10]:
                        log.warning(f"  {_t}/{_m}: {_v}")

                personal_timelines = load_personal_history(DATA_DIR / "personal" / "personal_history.json")
                personal_timelines = update_personal_timeline(personal_timelines, personal_call_sets)

                # Evaluasi outcome untuk entry lama yang sudah jatuh tempo --
                # disetujui pengguna 2026-07-27, BEDA dari Historical publik
                # yang outcome-nya sengaja None selamanya (v2.1). Pakai
                # evidence_by_ticker yang SUDAH dihitung di atas, bukan fetch baru.
                n_evaluated = evaluate_due_entries(
                    personal_timelines, evidence_by_ticker,
                    benchmark_price_now=benchmark_price,
                    benchmark_series=benchmark_series,
                )
                if n_evaluated:
                    log.info(f"Personal: {n_evaluated} entry lama dievaluasi outcome-nya")
            except Exception:
                log.exception("Personal layer failed -- pipeline publik tetap lanjut tanpanya")
                personal_call_sets = []
                personal_timelines = {}
    except Exception:
        log.exception("Pipeline failed — dashboard/data left untouched")
        return 1

    # Every stage succeeded — build the JSON payloads (same shape cli.py's
    # per-stage commands already produce, so the dashboard doesn't need to
    # know or care whether a file came from the CLI or this orchestrator).
    # `session_id` (satu variabel kanonik, dihitung sekali di awal main())
    # ditulis ke SETIAP file stage di bawah -- audit item C2/C9: 10 file ini
    # ditulis atomik satu-satu, bukan satu transaksi, jadi kill eksternal di
    # TENGAH blok tulis di bawah bisa menyisakan sebagian file dari run ini
    # (session_id baru) dan sebagian dari run kemarin (session_id lama) yang
    # belum sempat tertimpa. Marker seragam ini tidak MENCEGAH itu (perbaikan
    # penuh butuh staging+swap transaksional, di luar cakupan yang dipilih),
    # tapi membuatnya BISA DIDETEKSI -- lihat backend/app.py::get_consistency.
    # historical_timeline.json sengaja TIDAK ikut (bentuknya {ticker: {...}}
    # tanpa wrapper level-atas; menambah key "session_id" di situ akan
    # mencemari namespace ticker).
    screening_data = screening_result.to_dict()
    screening_data["session_id"] = session_id
    evidence_data = {
        "screening_universe": screening_result.universe_raw,
        "screening_passed": len(screening_result.passed),
        "evidence_generated": len(evidence_packages),
        "generated_at": evidence_packages[0].generated_at if evidence_packages else None,
        "session_id": session_id,
        "packages": [p.to_dict() for p in evidence_packages],
    }
    knowledge_data = {
        "evidence_count": len(evidence_packages),
        "knowledge_generated": len(profiles),
        "generated_at": profiles[0].metadata.evidence_date if profiles else None,
        "session_id": session_id,
        "profiles": [p.to_dict() for p in profiles],
    }
    catalyst_data = {
        "knowledge_count": len(profiles),
        "catalyst_sets_generated": len(catalysts),
        "with_upcoming": sum(1 for c in catalysts if c.has_upcoming),
        "session_id": session_id,
        "catalyst_sets": [c.to_dict() for c in catalysts],
    }
    institutional_flow_data = inst_flow.to_dict()
    institutional_flow_data["session_id"] = session_id
    peer_data = {
        "knowledge_count": len(profiles),
        "peer_comparisons_generated": len(comparisons),
        "generated_at": comparisons[0].generated_at if comparisons else None,
        "session_id": session_id,
        "comparisons": [c.to_dict() for c in comparisons],
    }
    confidence_data = {
        "knowledge_count": len(profiles),
        "confidence_scores_generated": len(confidences),
        "generated_at": confidences[0].assessed_at if confidences else None,
        "session_id": session_id,
        "scores": [s.to_dict() for s in confidences],
    }
    risk_data = {
        "knowledge_count": len(profiles),
        "risk_assessments_generated": len(risks),
        "generated_at": risks[0].assessed_at if risks else None,
        "session_id": session_id,
        "assessments": [a.to_dict() for a in risks],
    }
    reasoning_data = {
        "knowledge_count": len(profiles),
        "reasoning_outputs_generated": len(reasonings),
        "generated_at": reasonings[0].generated_at if reasonings else None,
        "session_id": session_id,
        "reasoning_outputs": [r.to_dict() for r in reasonings],
    }
    aggregator_data = {
        "profiles_count": len(profiles),
        "session_id": session_id,
        "outputs_generated": len(recommendations),
        "generated_at": recommendations[0].generated_at if recommendations else None,
        # Key tetap "recommendations" (bukan direname ke "aggregator_outputs")
        # untuk sekarang -- backend/app.py & frontend masih baca key ini,
        # rename filename/key penuh itu scope Fase 7 (frontend), bukan di sini.
        "recommendations": [r.to_dict() for r in recommendations],
    }
    timeline_data = {ticker: t.to_dict() for ticker, t in timelines.items()}
    layer1_data = layer1_pkg.to_dict()

    _atomic_write(DATA_DIR / "screening.json", screening_data)
    _atomic_write(DATA_DIR / "evidence.json", evidence_data)
    _atomic_write(DATA_DIR / "knowledge.json", knowledge_data)
    _atomic_write(DATA_DIR / "catalysts.json", catalyst_data)
    _atomic_write(DATA_DIR / "institutional_flow.json", institutional_flow_data)
    _atomic_write(DATA_DIR / "peer_results.json", peer_data)
    _atomic_write(DATA_DIR / "confidence_scores.json", confidence_data)
    _atomic_write(DATA_DIR / "risk_assessments.json", risk_data)
    _atomic_write(DATA_DIR / "reasoning_outputs.json", reasoning_data)
    _atomic_write(DATA_DIR / "final_recommendations.json", aggregator_data)
    _atomic_write(DATA_DIR / "historical_timeline.json", timeline_data)
    # pt_store/ch_store dihitung jauh di atas (segera setelah Evidence/Catalyst
    # run), tapi tulis disknya sengaja ditunda sampai sini -- lihat docstring
    # modul tentang C1/C10. Sebelum ini, sync_price_target_history()/
    # sync_catalyst_history() menulis sendiri saat dipanggil, di luar gerbang
    # all-or-nothing.
    save_price_target_store(pt_store, DATA_DIR / "price_target_history.json")
    save_catalyst_history_store(ch_store, DATA_DIR / "catalyst_history.json")

    # Lapisan pribadi -- file terpisah, folder terpisah (dashboard/data/
    # personal/), TIDAK ikut menyentuh file publik di atas sama sekali.
    # Ditulis best-effort: kegagalan di sini tidak boleh membuat pipeline
    # publik yang sudah selesai di atas jadi ikut gagal/rollback.
    if PERSONAL_ENABLED and personal_call_sets:
        personal_dir = DATA_DIR / "personal"
        personal_dir.mkdir(exist_ok=True)
        personal_data = {
            "profiles_count": len(profiles),
            "call_sets_generated": len(personal_call_sets),
            "generated_at": personal_call_sets[0].generated_at if personal_call_sets else None,
            "call_sets": [c.to_dict() for c in personal_call_sets],
        }
        _atomic_write(personal_dir / "personal_calls.json", personal_data)
        save_personal_history(personal_timelines, personal_dir / "personal_history.json")
        log.info(f"Personal: wrote {len(personal_call_sets)} call sets + history for {len(personal_timelines)} tickers")

        # Rapor kalibrasi — agregat kecil (beberapa KB) atas riwayat yang
        # sudah di memori, jadi nol biaya baca ulang. Dipisah dari
        # personal_history.json dengan sengaja: halaman kalibrasi tidak boleh
        # ikut menunggu unduhan 127 MB seperti halaman Riwayat.
        # Best-effort seperti sisa blok pribadi ini: gagal di sini tidak boleh
        # membuang riwayat & call sets yang sudah ditulis di atas.
        try:
            calibration = build_calibration(personal_timelines)
            _atomic_write(personal_dir / "calibration.json", calibration)
            _cal = calibration["overall"]
            log.info(
                f"Personal: kalibrasi {calibration['theses_directional']} tesis berarah, "
                f"hit {_cal['hit_rate_pct']}%, {_cal['entry_dates']} tanggal masuk, "
                f"evaluable={_cal['evaluable']}"
            )
        except Exception:
            log.exception("Personal: gagal membangun rapor kalibrasi -- sisa lapisan pribadi tetap tertulis")
    _atomic_write(DATA_DIR / "layer1_context.json", layer1_data)
    layer1_historical.append_entry(DATA_DIR / "layer1_history.json", layer1_pkg)
    source_health.append_entry(DATA_DIR / "source_health_history.json", evidence_packages)

    # Root reference snapshots — tracked in git, matches the convention
    # already established for evidence.json/knowledge.json/peer_results.json/
    # screening.json at the repo root.
    _atomic_write(ROOT / "screening.json", screening_data)
    _atomic_write(ROOT / "evidence.json", evidence_data)
    _atomic_write(ROOT / "knowledge.json", knowledge_data)
    _atomic_write(ROOT / "peer_results.json", peer_data)

    finished = datetime.now(timezone.utc)
    log.info(f"Full pipeline refresh complete in {(finished - started).total_seconds():.0f}s")
    return 0


if __name__ == "__main__":
    # Audit item C4: alphaforge/layer2/sources/_retry.py menjalankan tiap
    # panggilan network lewat ThreadPoolExecutor global supaya bisa dibatasi
    # timeout -- tapi Python tidak punya cara membunuh thread yang sedang
    # jalan, jadi panggilan yang macet (pernah teramati: yfinance menggantung
    # 40+ menit tanpa exception) meninggalkan thread yang hidup SELAMANYA.
    # `ThreadPoolExecutor` mendaftarkan atexit hook yang JOIN semua thread
    # workernya sebelum interpreter benar-benar keluar -- kalau ada thread
    # yang macet permanen, `sys.exit()` (yang cuma raise SystemExit, proses
    # exit normal masih menunggu atexit) bisa membuat proses ini TIDAK
    # PERNAH keluar walau semua pekerjaan (termasuk seluruh tulis file) sudah
    # tuntas. backend/app.py menjalankan script ini lewat subprocess dengan
    # timeout 6 jam sebagai jaring pengaman, tapi itu berarti satu run yang
    # "sukses" bisa menggantung sampai 6 jam TAMBAHAN sebelum akhirnya
    # dibunuh paksa dari luar.
    #
    # Tidak ada API publik untuk membuat worker ThreadPoolExecutor jadi
    # daemon thread (pembuatan thread ada di _adjust_thread_count, internal
    # privat concurrent.futures.thread, rawan berubah antar versi Python).
    # os._exit() di sini mem-bypass SEMUA cleanup interpreter (atexit
    # termasuk) dan keluar seketika di level OS -- aman dipakai justru
    # karena titik ini SUDAH di ujung main(): setiap tulis file yang berarti
    # sudah selesai lewat os.replace() (durable terlepas dari apa yang
    # terjadi ke proses sesudahnya), tidak ada cleanup lain yang tersisa.
    code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    for handler in logging.getLogger().handlers:
        handler.flush()
    os._exit(code)
