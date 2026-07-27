"""Personal aggregator — 3 PersonalCall -> PersonalCallSet.

Cuma menyusun berdampingan, TIDAK menghitung skor/ranking/verdict gabungan
(D-04 tetap berlaku di lapisan pribadi juga, lihat PersonalCallSet docstring).
Sejajar dengan alphaforge.layer2.aggregator.run_aggregator, tapi jauh lebih
tipis karena tidak ada Synthesis (agreements/divergences) di sini -- itu
fitur yang sengaja belum diminta untuk lapisan pribadi.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from .personal_contracts import PersonalCallSet
from .personal_reasoning import build_personal_call, compute_position, current_price_from_evidence

if TYPE_CHECKING:
    from ..layer2.reasoning_contracts import ReasoningBundle
    from ..layer2.catalyst_contracts import CatalystSet
    from ..layer2.contracts import EvidencePackage


def build_personal_call_set(
    bundle: "ReasoningBundle",
    holdings: dict[str, dict],
    catalyst: "CatalystSet | None" = None,
    evidence: "EvidencePackage | None" = None,
    today: date | None = None,
) -> PersonalCallSet:
    """3 PersonalCall untuk satu ticker. position_status/holding_since/
    unrealized_return_pct dihitung SEKALI (properti portofolio, bukan per
    lens) lalu dipakai ketiga lens; horizon_status tetap dihitung per lens
    karena bucket horizon-nya beda-beda per modul."""
    current_price = current_price_from_evidence(evidence)
    position_status, holding_since, unrealized = compute_position(bundle.ticker, holdings, current_price)

    multibagger = build_personal_call(bundle.multibagger, position_status, holding_since, unrealized, today=today)
    quality = build_personal_call(bundle.quality_compound, position_status, holding_since, unrealized, today=today)
    speculative = build_personal_call(
        bundle.speculative, position_status, holding_since, unrealized, catalyst=catalyst, today=today,
    )

    return PersonalCallSet(
        ticker=bundle.ticker,
        exchange=bundle.exchange,
        multibagger=multibagger,
        quality_compound=quality,
        speculative=speculative,
        method_versions={
            "multibagger": multibagger.method_version,
            "quality_compound": quality.method_version,
            "speculative": speculative.method_version,
        },
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def build_personal_call_sets(
    bundles: list["ReasoningBundle"],
    holdings: dict[str, dict],
    catalyst_map: dict[str, "CatalystSet"],
    evidence_map: dict[str, "EvidencePackage"],
) -> list[PersonalCallSet]:
    """Batch version dipakai refresh_full_pipeline.py -- satu holdings dict
    di-load sekali, dipakai untuk semua ticker dalam satu run."""
    return [
        build_personal_call_set(
            bundle, holdings,
            catalyst=catalyst_map.get(bundle.ticker),
            evidence=evidence_map.get(bundle.ticker),
        )
        for bundle in bundles
    ]
