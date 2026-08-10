"""Catalyst lifecycle tracking — diff today's CatalystSet against the last
known state per (ticker, kind) to derive `lifecycle_status`.

A single snapshot can't tell you "delayed"/"completed"/"cancelled" — those
only exist by comparing to a PREVIOUS run. This module keeps a small
on-disk store of the last known state per (ticker, kind) plus an append-only
resolved-history log, same day-dedup convention as price_target.py/historical.py.

Matching key is `{ticker}:{kind}`, NOT `catalyst_id` — catalyst_id embeds the
expected date (`earnings_2026-08-14`), so it changes when a date moves,
making it useless for detecting a delay. Known limitation: this assumes at
most one ACTIVE catalyst per (ticker, kind) in the tracked window. If a
ticker genuinely has two of the same kind active at once (e.g. two
ex-dividend dates inside a 90-day horizon for a fast-paying issuer), only
the one that happens to land in `fresh_by_kind` last is diffed accurately —
not fixed here, documented rather than silently wrong.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .catalyst_contracts import ResolvedCatalyst
from ..json_safe import dumps_safe, write_text_atomic

if TYPE_CHECKING:
    from .catalyst_contracts import CatalystSet
    from .contracts import EvidencePackage

MAX_RESOLVED_PER_TICKER = 100


def load_catalyst_history_store(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"active": {}, "resolved": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("active", {})
        data.setdefault("resolved", {})
        return data
    except Exception:
        return {"active": {}, "resolved": {}}


def save_catalyst_history_store(store: dict, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(p, dumps_safe(store, indent=2, ensure_ascii=False))


def _find_earnings_outcome(ticker_evidence: "EvidencePackage | None", expected_at: str) -> str | None:
    """Best-effort: cari EpsSurprise yang tanggalnya dekat (+/- 10 hari) sama
    expected_at yang baru completed. Tidak fatal kalau tidak ketemu — earnings
    yang baru completed hari ini seringkali belum sempat masuk
    eps_surprise_history sampai run berikutnya (data itu sendiri juga lag)."""
    if not ticker_evidence or not ticker_evidence.analyst_estimates:
        return None
    try:
        target = datetime.fromisoformat(expected_at).date()
    except ValueError:
        return None
    best = None
    best_delta = None
    for e in ticker_evidence.analyst_estimates.eps_surprise_history:
        if not e.quarter or e.eps_actual is None or e.eps_estimate is None:
            continue
        try:
            qd = datetime.fromisoformat(e.quarter).date()
        except ValueError:
            continue
        delta = abs((qd - target).days)
        if delta <= 10 and (best_delta is None or delta < best_delta):
            best, best_delta = e, delta
    if best is None:
        return None
    direction = "beat" if (best.surprise_pct or 0) > 0 else "miss" if (best.surprise_pct or 0) < 0 else "in-line"
    pct = f" ({best.surprise_pct:+.1f}%)" if best.surprise_pct is not None else ""
    return f"EPS aktual ${best.eps_actual:.2f} vs estimasi ${best.eps_estimate:.2f} — {direction}{pct}"


def sync_catalyst_history(
    catalyst_sets: list["CatalystSet"],
    path: str | Path,
    evidence_by_ticker: dict[str, "EvidencePackage"] | None = None,
    max_resolved: int = MAX_RESOLVED_PER_TICKER,
) -> dict:
    """Bandingkan tiap CatalystSet.catalysts hari ini vs state tersimpan,
    set `lifecycle_status`/`previous_expected_at` di tempat (mutate), dan
    append ke resolved_history begitu ada yang completed/cancelled. Panggil
    sekali per run, tepat setelah `run_catalyst()`.

    TIDAK menulis `path` ke disk (audit 2026-07-30 item C1/C10) -- caller
    harus memanggil `save_catalyst_history_store(store, path)` sendiri,
    ditunda sampai sisa pipeline run ini juga sukses. Sebelumnya menulis
    segera di sini, yang berarti run yang GAGAL di tahap sesudahnya tetap
    memajukan state "active"/"resolved" (dipakai buat diff run BERIKUTNYA)
    tanpa pernah menghasilkan catalysts.json yang sepadan -- run sukses
    berikutnya lalu membandingkan terhadap state yang sudah "terpakai",
    dan satu hari transisi katalis hilang tanpa jejak (C10)."""
    store = load_catalyst_history_store(path)
    today = datetime.now(timezone.utc).date().isoformat()
    today_date = datetime.now(timezone.utc).date()
    evidence_by_ticker = evidence_by_ticker or {}

    for cs in catalyst_sets:
        active_prev: dict = store["active"].get(cs.ticker, {})
        resolved: list = store["resolved"].get(cs.ticker, [])

        if cs.status == "missing":
            # Audit 2026-07-30 item B6: `_fetch_yahoo_info` gagal (atau field
            # earnings kosong) hari ini -> `cs.catalysts` nyaris/kosong tanpa
            # itu berarti katalisnya benar-benar hilang. Tanpa guard ini, loop
            # diff di bawah membaca "kind yang ada di active_prev tapi hilang
            # dari fresh hari ini" dan menyimpulkan completed/cancelled --
            # kegagalan Yahoo SESAAT jadi menulis katalis yang tanggalnya
            # masih di masa depan sebagai `lifecycle_status="cancelled"`
            # PERMANEN di resolved_history, sementara katalisnya masih ada
            # dan cuma muncul lagi besok sebagai entri "scheduled" baru tanpa
            # kesinambungan. Biarkan state tersimpan apa adanya -- jangan
            # diff, jangan advance active/resolved -- dan coba lagi run
            # berikutnya saat fetch-nya sukses.
            cs.resolved_history = [
                ResolvedCatalyst(
                    kind=r["kind"], expected_at=r["expected_at"], lifecycle_status=r["lifecycle_status"],
                    resolved_on=r["resolved_on"], outcome_note=r.get("outcome_note"),
                )
                for r in resolved
            ]
            continue

        fresh_by_kind: dict = {}
        for c in cs.catalysts:
            fresh_by_kind[c.kind] = c  # kind terakhir menang kalau ada duplikat — lihat catatan modul

        new_active: dict = {}
        for kind, c in fresh_by_kind.items():
            prev = active_prev.get(kind)
            base_status = "scheduled" if c.certainty == "scheduled" else "monitoring"
            if prev is None:
                c.lifecycle_status = base_status
            elif prev.get("expected_at") == c.expected_at:
                c.lifecycle_status = base_status
            elif c.expected_at > prev.get("expected_at", c.expected_at):
                c.lifecycle_status = "delayed"
                c.previous_expected_at = prev.get("expected_at")
            else:
                # Tanggal maju lebih cepat dari perkiraan sebelumnya — bukan
                # salah satu dari 5 status yang diminta spec, diperlakukan
                # sebagai status dasar (bukan "delayed").
                c.lifecycle_status = base_status
            new_active[kind] = {
                "expected_at": c.expected_at,
                "certainty": c.certainty,
                "last_seen": today,
            }

        # Kind yang ada di active_prev tapi hilang dari fresh hari ini.
        for kind, prev in active_prev.items():
            if kind in fresh_by_kind:
                continue
            try:
                prev_date = datetime.fromisoformat(prev.get("expected_at")).date()
            except (TypeError, ValueError):
                prev_date = today_date
            terminal_status = "completed" if prev_date <= today_date else "cancelled"
            outcome_note = None
            if terminal_status == "completed" and kind == "earnings":
                outcome_note = _find_earnings_outcome(evidence_by_ticker.get(cs.ticker), prev.get("expected_at"))
            resolved.append({
                "kind": kind,
                "expected_at": prev.get("expected_at"),
                "lifecycle_status": terminal_status,
                "resolved_on": today,
                "outcome_note": outcome_note,
            })

        resolved = resolved[-max_resolved:]
        store["active"][cs.ticker] = new_active
        store["resolved"][cs.ticker] = resolved

        cs.resolved_history = [
            ResolvedCatalyst(
                kind=r["kind"], expected_at=r["expected_at"], lifecycle_status=r["lifecycle_status"],
                resolved_on=r["resolved_on"], outcome_note=r.get("outcome_note"),
            )
            for r in resolved
        ]

    return store
