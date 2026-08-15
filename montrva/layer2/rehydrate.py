"""Membangun ulang dataclass Layer 2 dari dict JSON hasil pipeline.

Ada karena artefak `dashboard/data/*.json` adalah dict polos, sementara semua
fungsi produksi (aggregator, personal) menerima dataclass. Sebelum modul ini,
rekonstruksinya ditulis inline di `cli.py` — dan begitu backend butuh yang sama
untuk membangun ulang PersonalCallSet ticker yang dipegang, salinan kedua akan
lahir. Dua pembaca untuk satu format adalah instans berikutnya dari kelas bug
"pemeriksa vs format produsen" yang sudah enam kali kena di proyek ini: yang
satu diperbarui saat kontraknya berubah, yang lain diam-diam tidak.

Prinsipnya sengaja sempit: modul ini TIDAK memvalidasi dan TIDAK mengisi
default apa pun di luar yang sudah jadi default dataclass-nya. Field baru yang
belum ada di berkas lama akan memakai default kontraknya; field yang sudah
tidak dikenal kontrak akan MELEDAK (TypeError) alih-alih diserap diam-diam —
berkas basi lebih baik ketahuan daripada terbaca separuh.
"""
from __future__ import annotations

from .catalyst_contracts import Catalyst, CatalystSet, CatalystSource, ResolvedCatalyst
from .reasoning_contracts import (
    ContextUsage, FlagResponse, ModuleConfidence, ModuleOutput, ReasoningBundle,
)
from .risk_contracts import Flag, RedFlag, RiskAssessment

_MODULES = ("multibagger", "quality_compound", "speculative")


def module_output(d: dict) -> ModuleOutput:
    d = d.copy()
    d["confidence"] = ModuleConfidence(**d["confidence"])
    d["flag_responses"] = [FlagResponse(**r) for r in d.get("flag_responses", [])]
    d["context_used"] = [ContextUsage(**c) for c in d.get("context_used", [])]
    return ModuleOutput(**d)


def reasoning_bundle(d: dict) -> ReasoningBundle:
    d = d.copy()
    for key in _MODULES:
        d[key] = module_output(d[key])
    return ReasoningBundle(**d)


def risk_assessment(d: dict) -> RiskAssessment:
    d = d.copy()
    d["red_flags"] = [RedFlag(**rf) for rf in d.get("red_flags", [])]
    d["flags"] = [Flag(**f) for f in d.get("flags", [])]
    return RiskAssessment(**d)


def catalyst_set(d: dict) -> CatalystSet:
    d = d.copy()
    catalysts = []
    for c in d.get("catalysts", []):
        c = c.copy()
        c["source"] = CatalystSource(**c["source"])
        catalysts.append(Catalyst(**c))
    d["catalysts"] = catalysts
    d["resolved_history"] = [ResolvedCatalyst(**r) for r in d.get("resolved_history", [])]
    return CatalystSet(**d)


def index_by_ticker(items: list[dict], build) -> dict:
    """`{ticker: dataclass}` dari daftar dict stage, melewati yang gagal.

    Satu baris rusak (berkas dari versi kontrak lama, run yang terpotong) tidak
    boleh membuat seluruh halaman kosong — itu justru kegagalan yang paling
    membingungkan untuk didiagnosis dari dashboard."""
    out = {}
    for item in items:
        ticker = item.get("ticker")
        if not ticker:
            continue
        try:
            out[ticker] = build(item)
        except (TypeError, KeyError, ValueError):
            continue
    return out
