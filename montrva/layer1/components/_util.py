from __future__ import annotations

from typing import Any

from ..contracts import ComponentReading, Evidence, Source, Threshold, now_iso


def missing(name: str, kind, note: str, method_version: str | None = None, inputs=None) -> ComponentReading:
    return ComponentReading(
        name=name,
        value=None,
        status="missing",
        kind=kind,
        method_version=method_version,
        inputs=inputs or [],
        sources=[],
        note=note,
    )


def source(provider: str) -> Source:
    return Source(provider=provider, fetched_at=now_iso())


def ev(field: str, value: Any, as_of: str, source_label: str) -> Evidence:
    """Shorthand buat satu titik Evidence — dipakai berkali-kali per komponen."""
    return Evidence(field=field, value=value, as_of=as_of, source=source_label)


def th(label: str, operator: str, value: float) -> Threshold:
    """Shorthand buat satu Threshold."""
    return Threshold(label=label, operator=operator, value=value)


def percentile_rank(values: list[float], current: float) -> float:
    """Fraksi nilai historis (termasuk current) yang <= current — 0 = current
    adalah nilai terendah dalam window, 1 = tertinggi. Dipakai komponen yang
    ingin menskalakan sinyal relatif terhadap distribusi historisnya sendiri
    (percentile), bukan angka ambang tetap yang maknanya bisa melenceng
    antar rezim (mis. suku bunga, inflasi)."""
    if not values:
        return 0.5
    return sum(1 for v in values if v <= current) / len(values)
