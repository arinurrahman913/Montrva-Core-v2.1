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


def _json_key_str(k):
    """Ubah key dict jadi representasi STRING JSON, meniru koersi kunci
    bawaan `json` module (str dipertahankan; int/float/bool/None diubah ke
    representasi string standarnya). `json.dump(k, fp)` SALAH dipakai
    langsung untuk key non-string di `_dump_streaming` -- itu akan menulis
    representasi k sebagai NILAI (mis. `1` -> token `1`), bukan sebagai
    KUNCI string yang wajib dikutip (mis. `1` -> `"1"`)."""
    if isinstance(k, str):
        return k
    if k is True:
        return "true"
    if k is False:
        return "false"
    if k is None:
        return "null"
    if isinstance(k, int):
        return str(k)
    if isinstance(k, float):
        return float.__repr__(k)
    raise TypeError(f"keys must be str, int, float, bool or None, not {type(k).__name__}")


def _dump_streaming(value, fp, **kwargs) -> None:
    """Tulis `value` sebagai JSON ke `fp`, sanitasi NaN/inf terjadi PER-LEAF
    saat emisi -- rekursi turun ke container (dict/list) dengan menulis
    delimiter langsung ke `fp`, dan hanya memanggil `json.dump` per LEAF
    (skalar/float, murah). Tidak ada titik mana pun yang menahan salinan
    penuh (sebagian besar atau seluruh) struktur di memori sekaligus --
    beda dari `json.dump(_sanitize(obj), fp)` yang memateralisasi SATU
    salinan rekursif penuh lewat `_sanitize` sebelum satu byte pun ditulis.

    Format keluaran KOMPAK (tidak ada indent/newline) terlepas dari kwargs
    `indent` -- dipakai `dump_safe` HANYA saat `indent is None` (lihat di
    bawah), jadi ini tidak masalah dalam praktiknya."""
    if isinstance(value, dict):
        fp.write("{")
        first = True
        for k, v in value.items():
            if not first:
                fp.write(",")
            first = False
            json.dump(_json_key_str(k), fp, **kwargs)
            fp.write(":")
            _dump_streaming(v, fp, **kwargs)
        fp.write("}")
    elif isinstance(value, (list, tuple)):
        fp.write("[")
        first = True
        for item in value:
            if not first:
                fp.write(",")
            first = False
            _dump_streaming(item, fp, **kwargs)
        fp.write("]")
    elif isinstance(value, float):
        json.dump(value if math.isfinite(value) else None, fp, **kwargs)
    else:
        json.dump(value, fp, **kwargs)


def dump_safe(obj, fp, **kwargs) -> None:
    """`json.dump` (menulis LANGSUNG ke file), NaN/inf/-inf jadi `null`.

    Dipakai untuk file besar. `dumps_safe` merakit seluruh keluaran sebagai satu
    string di memori, lalu `Path.write_text` meng-encode-nya ke UTF-8 = satu
    salinan bytes lagi. Pada evidence.json (~1GB) dua salinan raksasa itu saja
    sudah cukup memicu MemoryError di mesin 8GB — persis yang membunuh run
    2026-07-30 tepat di titik tulis, setelah 2 jam kerja. `json.dump` menulis
    bertahap ke file handle, jadi tidak ada string/bytes raksasa yang ditahan.

    Audit 2026-07-30 item A5: klaim di atas cuma separuh benar. Versi
    sebelumnya masih `json.dump(_sanitize(obj), fp, **kwargs)` --
    `_sanitize(obj)` memateralisasi SATU SALINAN REKURSIF PENUH dict-of-
    dicts/list-of-lists di memori SEBELUM satu byte pun ditulis. 2 dari 4
    salinan hilang, bukan 3 -- kalau universe tumbuh atau downsampling
    dimatikan, MemoryError yang sama kembali di baris yang sama.

    Perbaikan: kalau `indent` tidak diminta (satu-satunya pemanggil produksi,
    `refresh_full_pipeline.py::_atomic_write`, memakai `indent=None` justru
    untuk payload BESAR -- lihat `_is_big_payload`), pakai `_dump_streaming`
    yang sanitasi per-leaf saat emisi, tanpa salinan paralel dari sebagian
    besar/seluruh struktur. Kalau `indent` diminta (payload KECIL, dipakai
    supaya enak di-diff -- payload besar tidak pernah lewat jalur ini di
    codebase ini), tetap pakai jalur lama: file-nya kecil by construction
    (gerbang `_is_big_payload` di pemanggil), jadi satu salinan penuh bukan
    risiko, dan mereplikasi format indent persis `json.dump` tanpa
    menuliskan ulang seluruh logika indentasinya tidak sepadan risikonya.
    """
    if kwargs.get("indent") is None:
        _dump_streaming(obj, fp, **kwargs)
    else:
        json.dump(_sanitize(obj), fp, **kwargs)


def write_text_atomic(path, text: str) -> None:
    """Tulis teks ke file secara atomik: tmp UNIK-per-penulis lalu os.replace.

    Empat pemanggil (layer1/historical, layer2/ai_narrative,
    layer2/catalyst_history, layer2/price_target) dulu memakai nama tmp TETAP
    `<nama>.tmp`. cache.py sudah meninggalkan pola itu setelah audit: dua
    penulis konkuren yang berbagi nama tmp bisa saling menimpa file TMP satu
    sama lain sebelum salah satunya sempat rename -- torn-write cuma pindah
    dari file final ke file tmp, tidak hilang. Keempat tempat ini terlewat
    saat itu.

    Kegagalan di tengah juga membersihkan tmp-nya. Tanpa itu, run yang mati
    (Flask restart membunuh anak proses, MemoryError, Ctrl+C) meninggalkan
    .tmp yatim -- terjadi nyata 2026-08-01, menyisakan peer_results.json.tmp
    23,8 MB berisi JSON separuh.
    """
    import os
    import tempfile
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=p.parent, prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, p)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
