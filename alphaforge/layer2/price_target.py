"""Price Target Tracking — persist analyst price target consensus over time.

Yahoo's `.info` only ever gives the CURRENT consensus snapshot (target_low/
mean/median/high) — there is no free historical time series for it. To show
a real trend (not just today's single point), this module appends one
snapshot/ticker/HARI KALENDER (UTC) to a small on-disk store each time
Evidence runs, and hands the accumulated series back to each
EvidencePackage.analyst_estimates.price_target_history. Re-running the
pipeline the same day overwrites that day's entry rather than duplicating
it — same convention as layer2/historical.py and layer2/source_health.py.

A brand-new ticker (or the very first run after this module was added)
will only have 1 data point until the pipeline has run on a few different
calendar days — the chart fills in over time, it cannot be backfilled.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .contracts import PriceTargetSnapshot
from ..json_safe import dumps_safe, write_text_atomic

if TYPE_CHECKING:
    from .contracts import EvidencePackage

MAX_SNAPSHOTS_PER_TICKER = 400  # ~13 months of daily entries headroom


def load_price_target_store(path: str | Path) -> dict[str, list[dict]]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_price_target_store(store: dict[str, list[dict]], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(p, dumps_safe(store, indent=2, ensure_ascii=False))


def sync_price_target_history(
    evidence_packages: list["EvidencePackage"],
    path: str | Path,
    max_entries: int = MAX_SNAPSHOTS_PER_TICKER,
) -> dict[str, list[dict]]:
    """Compute today's snapshot per ticker and attach the accumulated
    history back onto each package's analyst_estimates. Mutates
    `evidence_packages` in place. Call this once per pipeline run, right
    after `run_evidence()`, so Knowledge (which reads price_target_history
    for the 3-month trend) sees the up-to-date series.

    Does NOT write `path` to disk (audit 2026-07-30 item C1) — the caller
    must call `save_price_target_store(store, path)` itself, deferred until
    the rest of the pipeline run has also succeeded. This used to write
    immediately, which broke the all-or-nothing invariant every other stage
    file honors: if a later stage failed, this file alone had already
    advanced to today while evidence.json/knowledge.json/etc stayed on
    yesterday's data, and `/api/ticker/<t>` would merge the two, handing the
    frontend a response that mixes two different pipeline runs with no way
    to detect it.
    """
    store = load_price_target_store(path)
    today = datetime.now(timezone.utc).date().isoformat()

    for pkg in evidence_packages:
        ae = pkg.analyst_estimates
        if not ae or ae.target_mean is None:
            continue

        entry = {
            "date": today,
            "target_mean": ae.target_mean,
            "target_median": ae.target_median,
            "target_low": ae.target_low,
            "target_high": ae.target_high,
            "num_analysts": ae.num_analyst_opinions,
        }

        ticker_history = store.get(pkg.ticker, [])
        if ticker_history and ticker_history[-1].get("date") == today:
            ticker_history[-1] = entry
        else:
            ticker_history.append(entry)
        ticker_history = ticker_history[-max_entries:]
        store[pkg.ticker] = ticker_history

        ae.price_target_history = [
            PriceTargetSnapshot(
                date=snap.get("date"),
                target_mean=snap.get("target_mean"),
                target_median=snap.get("target_median"),
                target_low=snap.get("target_low"),
                target_high=snap.get("target_high"),
                num_analysts=snap.get("num_analysts"),
            )
            for snap in ticker_history
        ]

    return store
