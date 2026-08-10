"""Bangun dashboard/data/institutional_flow.json dari file stage yang SUDAH ada.

Berdiri sendiri (pola yang sama dengan scripts/build_sector_map.py): stage ini
juga berjalan otomatis di dalam refresh_full_pipeline.py, tapi full run makan
~3 jam — script ini mengisi file-nya sekarang dari evidence.json +
knowledge.json hasil run terakhir, tanpa satu pun panggilan jaringan.

`session_id` sengaja disalin dari evidence.json, bukan dibuat baru: file ini
harus mengaku berasal dari run yang SAMA dengan data yang dipakainya. Membuat
session_id sendiri akan membuat /api/consistency melaporkan dashboard
tercampur dua run, padahal justru sebaliknya.

Jalankan: python scripts/build_institutional_flow.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from montrva.json_safe import dumps_safe, write_text_atomic  # noqa: E402
from montrva.layer2.institutional_flow import run_institutional_flow  # noqa: E402

DATA_DIR = ROOT / "dashboard" / "data"


def main() -> int:
    evidence_path = DATA_DIR / "evidence.json"
    knowledge_path = DATA_DIR / "knowledge.json"
    if not evidence_path.exists():
        print(f"tidak ada {evidence_path} — jalankan pipeline dulu")
        return 1

    print(f"membaca {knowledge_path.name} …")
    profiles = json.loads(knowledge_path.read_text(encoding="utf-8")).get("profiles", []) \
        if knowledge_path.exists() else []

    print(f"membaca {evidence_path.name} ({evidence_path.stat().st_size / 1e6:.0f} MB) …")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    packages = evidence.get("packages", [])

    print(f"agregasi {len(packages)} paket evidence …")
    report = run_institutional_flow(packages, profiles)

    data = report.to_dict()
    data["session_id"] = evidence.get("session_id")

    out = DATA_DIR / "institutional_flow.json"
    write_text_atomic(out, dumps_safe(data))

    net_b = report.net_value_usd / 1e9
    gross_b = report.gross_value_usd / 1e9
    print(
        f"ditulis {out.name}: {report.tickers_with_holders} saham berpemegang, "
        f"{report.tickers_with_basis} punya arah, "
        f"{report.tickers_new_positions_only} hanya posisi baru; "
        f"net ${net_b:,.1f}B / gross ${gross_b:,.1f}B "
        f"({report.net_ratio_pct:.1f}%) · {len(report.sectors)} sektor"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
