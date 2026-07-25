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

from alphaforge.json_safe import dumps_safe  # noqa: E402
from alphaforge.layer1 import historical as layer1_historical  # noqa: E402
from alphaforge.layer1.pipeline import build_market_context_package  # noqa: E402
from alphaforge.layer2.screening import run_screening  # noqa: E402
from alphaforge.layer2.evidence import run_evidence  # noqa: E402
from alphaforge.layer2.price_target import sync_price_target_history  # noqa: E402
from alphaforge.layer2.knowledge import run_knowledge  # noqa: E402
from alphaforge.layer2.catalyst import run_catalyst  # noqa: E402
from alphaforge.layer2.peer import run_peer_comparison  # noqa: E402
from alphaforge.layer2.confidence import run_confidence  # noqa: E402
from alphaforge.layer2.risk import run_risk_assessment  # noqa: E402
from alphaforge.layer2.reasoning import run_reasoning_pipeline  # noqa: E402
from alphaforge.layer2.aggregator import run_aggregator  # noqa: E402
from alphaforge.layer2.historical import load_historical_timeline, update_timeline  # noqa: E402
from alphaforge.layer2 import source_health  # noqa: E402

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


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps_safe(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
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

        evidence_packages = run_evidence(screening_result)
        log.info(f"Evidence: {len(evidence_packages)} packages")

        # Yahoo has no free historical price-target time series — this
        # appends today's snapshot/ticker to an on-disk store and attaches
        # the accumulated series back onto each package, so Knowledge (next)
        # sees the up-to-date history for its 3-month trend calc.
        pt_store = sync_price_target_history(evidence_packages, DATA_DIR / "price_target_history.json")
        n_pt = sum(1 for p in evidence_packages if p.analyst_estimates and p.analyst_estimates.target_mean is not None)
        log.info(f"Price target: {n_pt} tickers snapshotted ({len(pt_store)} tracked total)")

        profiles = run_knowledge(evidence_packages, screening_result.passed)
        log.info(f"Knowledge: {len(profiles)} profiles")

        catalysts = run_catalyst([p.ticker for p in profiles])
        catalyst_map = {c.ticker: c for c in catalysts}
        n_cat = sum(1 for c in catalysts if c.has_upcoming)
        log.info(f"Catalyst: {len(catalysts)} sets ({n_cat} with upcoming catalyst)")

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
                peer_map.get(p.ticker), catalyst_map.get(p.ticker),
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
    except Exception:
        log.exception("Pipeline failed — dashboard/data left untouched")
        return 1

    # Every stage succeeded — build the JSON payloads (same shape cli.py's
    # per-stage commands already produce, so the dashboard doesn't need to
    # know or care whether a file came from the CLI or this orchestrator).
    screening_data = screening_result.to_dict()
    evidence_data = {
        "screening_universe": screening_result.universe_raw,
        "screening_passed": len(screening_result.passed),
        "evidence_generated": len(evidence_packages),
        "generated_at": evidence_packages[0].generated_at if evidence_packages else None,
        "packages": [p.to_dict() for p in evidence_packages],
    }
    knowledge_data = {
        "evidence_count": len(evidence_packages),
        "knowledge_generated": len(profiles),
        "generated_at": profiles[0].metadata.evidence_date if profiles else None,
        "profiles": [p.to_dict() for p in profiles],
    }
    catalyst_data = {
        "knowledge_count": len(profiles),
        "catalyst_sets_generated": len(catalysts),
        "with_upcoming": sum(1 for c in catalysts if c.has_upcoming),
        "catalyst_sets": [c.to_dict() for c in catalysts],
    }
    peer_data = {
        "knowledge_count": len(profiles),
        "peer_comparisons_generated": len(comparisons),
        "generated_at": comparisons[0].generated_at if comparisons else None,
        "comparisons": [c.to_dict() for c in comparisons],
    }
    confidence_data = {
        "knowledge_count": len(profiles),
        "confidence_scores_generated": len(confidences),
        "generated_at": confidences[0].assessed_at if confidences else None,
        "scores": [s.to_dict() for s in confidences],
    }
    risk_data = {
        "knowledge_count": len(profiles),
        "risk_assessments_generated": len(risks),
        "generated_at": risks[0].assessed_at if risks else None,
        "assessments": [a.to_dict() for a in risks],
    }
    reasoning_data = {
        "knowledge_count": len(profiles),
        "reasoning_outputs_generated": len(reasonings),
        "generated_at": reasonings[0].generated_at if reasonings else None,
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
    _atomic_write(DATA_DIR / "peer_results.json", peer_data)
    _atomic_write(DATA_DIR / "confidence_scores.json", confidence_data)
    _atomic_write(DATA_DIR / "risk_assessments.json", risk_data)
    _atomic_write(DATA_DIR / "reasoning_outputs.json", reasoning_data)
    _atomic_write(DATA_DIR / "final_recommendations.json", aggregator_data)
    _atomic_write(DATA_DIR / "historical_timeline.json", timeline_data)
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
    sys.exit(main())
