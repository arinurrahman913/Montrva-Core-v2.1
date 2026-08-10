"""Deret penutupan S&P 500 yang MENUMPUK lintas run — jangkar pembanding
indeks untuk evaluasi outcome.

Kenapa file sendiri, bukan pakai `MarketContextPackage.spx_history` langsung:
spx_history sengaja cuma 90 bar terakhir (dia memang dibuat untuk chart
validasi Layer 1) dan **ditulis ulang tiap run**, jadi jendelanya bergulir.
Evaluasi outcome butuh harga indeks pada tanggal MASUK sebuah tesis — dan
tesis Multibagger/Quality jatuh temponya 730/1825 hari. Begitu tanggal
masuknya keluar dari jendela 90 hari, pembandingnya hilang selamanya, tanpa
ada yang tahu. File ini menyimpan tiap bar yang pernah terlihat, jadi
jendelanya cuma tumbuh.

Diletakkan di layer1 (bukan personal/) karena isinya murni data publik —
penutupan ^GSPC, nol informasi portofolio — dan arah impor yang dibolehkan
memang personal -> layer1, tidak sebaliknya. Kalau folder personal/ dihapus
untuk rilis publik, deret ini tetap terkumpul dan siap dipakai evaluasi
Historical publik (yang outcome-nya masih ditunda ke v2.1).

Sumber bar-nya SAMA dengan spx_history (`_get_spx_validation_history`), jadi
sinkronisasi ini **nol fetch jaringan tambahan**.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from ..json_safe import dumps_safe, write_text_atomic

# Berapa jauh mundur boleh dicari kalau tanggal yang diminta bukan hari bursa.
# 7 hari menutup akhir pekan + libur berturut (mis. Natal + Tahun Baru yang
# jatuh berdekatan). Lebih dari itu berarti deretnya memang bolong, dan lebih
# baik mengembalikan None daripada memakai harga indeks seminggu lebih lama
# sebagai "harga pada tanggal itu".
LOOKUP_TOLERANCE_DAYS = 7


def load_benchmark_history(path: str | Path) -> dict[str, float]:
    """{"YYYY-MM-DD": close}. Berkas belum ada = deret kosong, bukan error."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    series = data.get("series") if isinstance(data, dict) else None
    if not isinstance(series, dict):
        return {}
    return {
        str(d)[:10]: float(v)
        for d, v in series.items()
        if v is not None and isinstance(v, (int, float))
    }


def save_benchmark_history(series: dict[str, float], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": "^GSPC",
        "note": (
            "Penutupan harian S&P 500, menumpuk lintas run (tidak pernah menyusut). "
            "Dipakai sebagai pembanding indeks saat mengevaluasi outcome tesis."
        ),
        "first_date": min(series) if series else None,
        "last_date": max(series) if series else None,
        "bars": len(series),
        "series": dict(sorted(series.items())),
    }
    write_text_atomic(p, dumps_safe(payload, indent=2, ensure_ascii=False))


def sync_benchmark_history(spx_history: list[dict] | None, path: str | Path) -> dict[str, float]:
    """Gabungkan bar baru dari spx_history ke deret di disk, lalu tulis.

    Bar yang sudah ada DITIMPA dengan nilai terbaru (Yahoo sesekali merevisi
    penutupan hari berjalan), tapi tidak ada tanggal yang pernah dibuang —
    itu inti gunanya file ini. Mengembalikan deret hasil gabungan supaya
    pemanggil tidak perlu membaca ulang dari disk.
    """
    series = load_benchmark_history(path)
    for bar in spx_history or []:
        if not isinstance(bar, dict):
            continue
        d = bar.get("date")
        price = bar.get("price")
        if not d or price is None or not isinstance(price, (int, float)):
            continue
        series[str(d)[:10]] = float(price)
    save_benchmark_history(series, path)
    return series


def benchmark_close_on_or_before(
    series: dict[str, float], target: str | None,
    tolerance_days: int = LOOKUP_TOLERANCE_DAYS,
) -> tuple[float | None, str | None]:
    """(close, tanggal_bar_yang_dipakai) untuk `target`.

    Mundur (bukan maju) karena tanggal yang diminta selalu tanggal yang SUDAH
    lewat — bar sesudahnya berarti memakai informasi dari masa depan tanggal
    itu. Tanggal bar yang benar-benar dipakai ikut dikembalikan supaya
    pemanggil bisa menampilkannya apa adanya; ini pola yang sama dengan
    `exit_date` di personal_evaluation.py, yang sengaja BUKAN `due_at`.
    """
    if not series or not target:
        return None, None
    try:
        t = date.fromisoformat(str(target)[:10])
    except ValueError:
        return None, None
    for back in range(tolerance_days + 1):
        key = (t - timedelta(days=back)).isoformat()
        if key in series:
            return series[key], key
    return None, None
