"""JSON serialization that never emits NaN/Infinity.

`json.dumps` happily writes the literal tokens `NaN`/`Infinity`/`-Infinity`
for non-finite floats — valid Python, but NOT valid JSON, so any browser's
`JSON.parse` (or any other strict-JSON consumer) rejects the whole file on
the first such value (seen live: screening.json's last_price was NaN for a
ticker whose most recent price bar was a pandas-missing row, breaking the
dashboard's Screening tab entirely).

This has bitten the project twice in two different call sites already
(Layer 1's SPX MA50/MA200 was fixed with a one-off `math.isnan` check;
Screening's last_price just hit the same class of bug from a different
angle). Fixing it once at the serialization boundary, recursively, closes
the whole bug class instead of chasing the next field that happens to be
NaN some day.
"""
from __future__ import annotations

import json
import math


def _sanitize(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def dumps_safe(obj, **kwargs) -> str:
    """`json.dumps`, but NaN/inf/-inf are replaced with `null` first."""
    return json.dumps(_sanitize(obj), **kwargs)


def dump_safe(obj, fp, **kwargs) -> None:
    """`json.dump` (menulis LANGSUNG ke file), NaN/inf/-inf jadi `null`.

    Dipakai untuk file besar. `dumps_safe` merakit seluruh keluaran sebagai satu
    string di memori, lalu `Path.write_text` meng-encode-nya ke UTF-8 = satu
    salinan bytes lagi. Pada evidence.json (~1GB) dua salinan raksasa itu saja
    sudah cukup memicu MemoryError di mesin 8GB — persis yang membunuh run
    2026-07-30 tepat di titik tulis, setelah 2 jam kerja. `json.dump` menulis
    bertahap ke file handle, jadi tidak ada string/bytes raksasa yang ditahan.
    """
    json.dump(_sanitize(obj), fp, **kwargs)
