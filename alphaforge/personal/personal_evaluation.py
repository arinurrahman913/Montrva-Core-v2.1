"""Evaluasi outcome PersonalCall — lanjutan draft §12, disetujui pengguna
2026-07-27 secara eksplisit: berbeda dari Historical publik (outcome sengaja
None selamanya sampai v2.1), lapisan pribadi ini MEMANG dirancang untuk satu
orang yang menerima risiko salah menilai sendiri — jadi outcome dihitung
mekanis begitu call jatuh tempo, bukan ditunda tanpa batas.

Cuma action berklaim arah yang dievaluasi (masuk = klaim harga naik, keluar
= klaim harga turun/flat). Action tanpa klaim arah (pantau/tahan/tunggu_
katalis/lewati) diberi outcome "tidak_berlaku", bukan dipaksa punya
nilai terbukti/meleset yang sebenarnya tidak berarti apa-apa untuk mereka.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from .personal_contracts import ACTION_CATEGORY_EXIT
from .personal_historical import due_for_review

if TYPE_CHECKING:
    from ..layer2.contracts import EvidencePackage

MODULES = ("multibagger", "quality_compound", "speculative")

# Semua action "masuk" (penuh maupun bertahap) -- klaim arahnya sama: harga
# naik. Lihat ACTION_CATEGORY_EXIT di personal_contracts.py untuk sisi lain.
ACTION_CATEGORY_ENTRY = frozenset({
    "mulai_posisi", "cicil_bertahap", "akumulasi", "akumulasi_saat_koreksi",
    "masuk_spekulatif", "tambah", "tambah_bertahap",
})

# Disetujui pengguna 2026-07-27: makin panjang horizon, makin besar target
# return-nya -- horizon pendek tidak boleh "menang" cuma karena noise harian.
HORIZON_OUTCOME_THRESHOLD_PCT = {
    "mingguan": 3.0,
    "bulanan": 5.0,
    "enam_bulan": 10.0,
    "satu_dua_tahun": 15.0,
    "lima_tahun": 30.0,
}


def _price_return_pct(price_history: list[dict] | None, since_date: str, current_price: float | None) -> float | None:
    if not price_history or current_price is None:
        return None
    bars = [b for b in price_history if b.get("date", "") >= since_date]
    if not bars:
        return None
    start_price = bars[0].get("close")
    if not start_price:
        return None
    return (current_price - start_price) / start_price * 100.0


def _classify(action: str, horizon: str, return_pct: float | None) -> str | None:
    if action not in ACTION_CATEGORY_ENTRY and action not in ACTION_CATEGORY_EXIT:
        return "tidak_berlaku"  # action tanpa klaim arah -- tidak dievaluasi
    if return_pct is None:
        return None  # data harga belum cukup, coba lagi run berikutnya
    threshold = HORIZON_OUTCOME_THRESHOLD_PCT.get(horizon)
    if threshold is None:
        return None
    if action in ACTION_CATEGORY_ENTRY:
        return "terbukti" if return_pct >= threshold else "meleset"
    # ACTION_CATEGORY_EXIT: klaim "hindari turun" -- naik kecil (0..threshold)
    # belum cukup jelas buat divonis, biarkan "ambigu" daripada dipaksa biner.
    if return_pct <= 0:
        return "terbukti"
    return "meleset" if return_pct >= threshold else "ambigu"


def _first_seen_at(entries: list[dict], module: str, action: str) -> str | None:
    """Jalan mundur dari entry PALING BARU selama action modul ini sama --
    re-derive titik mulai streak action ini, sama logikanya dengan
    firstSeenAt() di PersonalAggregatorView.jsx (frontend)."""
    sorted_entries = sorted(entries, key=lambda e: e.get("analyzed_at", ""))
    first_match = None
    for e in reversed(sorted_entries):
        call = (e.get("personal_call_set") or {}).get(module)
        if not call or call.get("action") != action:
            break
        first_match = e.get("analyzed_at")
    return first_match


def evaluate_due_entries(
    timelines: dict[str, dict],
    evidence_by_ticker: dict[str, "EvidencePackage"],
    today: date | None = None,
) -> int:
    """Isi entry["outcome"] untuk entry yang sudah due_for_review dan belum
    dievaluasi (outcome masih None). Mengubah `timelines` in-place (dict
    plain, sama konvensi dengan personal_historical.py). Return jumlah
    entry yang baru dievaluasi pass ini, buat logging."""
    today = today or datetime.now(timezone.utc).date()
    evaluated_count = 0

    for ticker, timeline in timelines.items():
        entries = timeline.get("entries", [])
        evidence = evidence_by_ticker.get(ticker)
        price_history = evidence.price_market.price_history if evidence else None
        current_price = evidence.price_market.last_price if evidence else None

        for entry in entries:
            if entry.get("outcome") is not None:
                continue
            if not due_for_review(entry, today):
                continue

            call_set = entry.get("personal_call_set") or {}
            outcome = {}
            for module in MODULES:
                call = call_set.get(module)
                if not call:
                    continue
                since = _first_seen_at(entries, module, call["action"]) or entry["analyzed_at"]
                return_pct = _price_return_pct(price_history, since[:10], current_price)
                classification = _classify(call["action"], call["horizon"], return_pct)
                outcome[module] = {
                    "classification": classification,
                    "return_pct": round(return_pct, 2) if return_pct is not None else None,
                    "threshold_pct": HORIZON_OUTCOME_THRESHOLD_PCT.get(call["horizon"]),
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                }
            if outcome:
                entry["outcome"] = outcome
                evaluated_count += 1

    return evaluated_count
