"""Data-source health snapshot — per-source fetch status across all Evidence
packages, tracked over time so the dashboard can show a coverage trend
(sparkline) per source instead of just today's snapshot.

Each of the 6 EvidencePackage sections already carries its own
SourceMetadata.status ("ok"/"missing"/"degraded") set by its own fetcher
(see contracts.py + sources/*.py) — this module just aggregates that
existing field across all tickers and appends one entry/day, mirroring the
pattern already used for LayerScore history (layer1/historical.py) and
per-ticker AggregatorOutput history (layer2/historical.py).

Note on institutional_activity: sources/sec_form4.py sets
status="ok" only when Form 4 filings were actually found in the 30-day
window (status="degraded" when none were found — see that module). So
its ok-count here reads as "tickers with recent insider filing activity",
not a fetch-failure count like the other 5 sources — that is intentional,
not a bug in this module.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..json_safe import dumps_safe

if TYPE_CHECKING:
    from .contracts import EvidencePackage

MAX_ENTRIES = 730  # ~2 tahun kalender kalau satu entry/hari

# (field name on EvidencePackage, display label)
SOURCES = [
    ("price_market", "Price/Market"),
    ("fundamental", "Fundamental"),
    ("institutional_ownership", "Institutional Own"),
    ("institutional_activity", "Insider Activity"),
    ("news", "News"),
    ("sec_filings", "SEC Filings"),
]


def _status(pkg: "EvidencePackage", field_name: str) -> str:
    section = getattr(pkg, field_name, None)
    meta = getattr(section, "metadata", None)
    return meta.status if meta is not None else "missing"


def compute_source_health(packages: list["EvidencePackage"]) -> dict:
    """Aggregate per-source ok/degraded/missing counts across `packages`."""
    total = len(packages)
    sources = {}
    for field_name, label in SOURCES:
        statuses = [_status(p, field_name) for p in packages]
        ok = sum(1 for s in statuses if s == "ok")
        degraded = sum(1 for s in statuses if s == "degraded")
        missing = sum(1 for s in statuses if s == "missing")
        sources[field_name] = {
            "label": label,
            "ok": ok,
            "degraded": degraded,
            "missing": missing,
            "total": total,
            "pct": round(100 * ok / total, 1) if total else 0.0,
        }
    return {"total": total, "sources": sources}


def load_history(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def append_entry(
    path: str | Path, packages: list["EvidencePackage"], max_entries: int = MAX_ENTRIES
) -> list[dict]:
    """Append today's source-health snapshot, atomic write. Satu entry per
    HARI KALENDER (UTC) — re-run di hari yang sama menimpa entry hari itu,
    bukan menambah duplikat (konvensi sama dengan layer1/historical.py &
    layer2/historical.py)."""
    now = datetime.now(timezone.utc)
    entry = {"date": now.date().isoformat(), "generated_at": now.isoformat(), **compute_source_health(packages)}

    history = load_history(path)
    if history and history[-1].get("date") == entry["date"]:
        history[-1] = entry
    else:
        history.append(entry)
    history = history[-max_entries:]

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(dumps_safe(history, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return history
