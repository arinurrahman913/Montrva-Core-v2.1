"""02_LAYER1_SPECS/09_MACRO_CALENDAR.md — kind=direct, komponen leaf.

Pakai FRED release calendar (endpoint /fred/release/dates), bukan scraping
Investing.com/Forex Factory — lihat 04_DATA_SOURCES/01_PROVIDERS_OVERVIEW.md §2b.

Catatan jujur: FRED release-dates endpoint cuma kasih TANGGAL rilis, bukan
nilai previous/consensus/actual (itu butuh feed forecast berbayar seperti
Trading Economics). Field itu sengaja tidak ditampilkan seolah ada,
daripada diisi placeholder yang menyesatkan.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import requests

from ... import cache
from ..contracts import ComponentReading
from ._util import ev, missing, source, th
from ..sources.fred import api_key

NAME = "macro_calendar"

# release_id resmi FRED: 10 = Consumer Price Index, 50 = Employment Situation
# Keduanya market-moving releases paling diawasi, jadi severity="high" tetap
# (bukan dihitung, karena hanya 2 seri yang di-track — tidak ada seri
# severity rendah untuk dibandingkan).
RELEASES = {10: "CPI", 50: "Employment Situation"}
HORIZON_DAYS = 30
SEVERITY = "high"
CALENDAR_CACHE_TTL = 12 * 3600  # selaras dgn caching seri FRED lain (fred.py)


def _today() -> date:
    return datetime.now(timezone.utc).date()  # UTC, konsisten dgn now_iso()


def _upcoming_dates(release_id: int, label: str) -> list[dict]:
    # Cache 12 jam: dulu endpoint ini dipanggil langsung tanpa cache, jadi tiap
    # build Layer 1 nembak FRED 2× — beda dari seri FRED lain yang sudah cached.
    cached = cache.get("layer1_macro_calendar", str(release_id), CALENDAR_CACHE_TTL)
    if cached is not None:
        return cached
    today = _today()
    resp = requests.get(
        "https://api.stlouisfed.org/fred/release/dates",
        params={
            "release_id": release_id,
            "api_key": api_key(),
            "file_type": "json",
            "realtime_start": today.isoformat(),
            "realtime_end": (today + timedelta(days=HORIZON_DAYS)).isoformat(),
            "include_release_dates_with_no_data": "true",
        },
        timeout=15,
    )
    resp.raise_for_status()
    events = [{"label": label, "date": d["date"], "severity": SEVERITY} for d in resp.json().get("release_dates", [])]
    cache.set("layer1_macro_calendar", str(release_id), events)
    return events


def compute() -> ComponentReading:
    try:
        events: list[dict] = []
        for rid, label in RELEASES.items():
            events.extend(_upcoming_dates(rid, label))
    except Exception as exc:
        return missing(NAME, "direct", f"FRED release calendar gagal ditarik: {exc}")

    events.sort(key=lambda e: e["date"])
    today = _today()

    # Impact label per severity (CPI & Employment = High; struktur siap kalau
    # nanti ada seri severity lebih rendah ditambahkan).
    IMPACT = {"high": "High", "medium": "Medium", "low": "Low"}
    top_events = [
        {
            "label": e["label"],
            "date": e["date"],
            "days_until": (date.fromisoformat(e["date"]) - today).days,
            "impact": IMPACT.get(e["severity"], "Medium"),
        }
        for e in events[:3]
    ]

    if events:
        days_to_next = (date.fromisoformat(events[0]["date"]) - today).days
        # Event risk: seberapa dekat rilis high-impact berikutnya.
        if days_to_next <= 3:
            event_risk = "High Event Risk"
        elif days_to_next <= 7:
            event_risk = "Medium Event Risk"
        else:
            event_risk = "Low Event Risk"
        top_txt = "; ".join(f"{e['label']} ({e['days_until']}h, {e['impact']})" for e in top_events)
        narrative = (
            f"{event_risk}. Terdekat: {top_txt}. {len(events)} peristiwa dalam {HORIZON_DAYS} hari."
        )
    else:
        days_to_next = None
        event_risk = "Low Event Risk"
        narrative = f"Low Event Risk — tidak ada rilis CPI/Employment terjadwal dalam {HORIZON_DAYS} hari."

    rule = "risk window sempit menurunkan score: rilis high-severity ≤3 hari → 40, ≤7 hari → 65, selain itu → 85"
    if days_to_next is None:
        raw_score = 85.0
    elif days_to_next <= 3:
        raw_score = 40.0
    elif days_to_next <= 7:
        raw_score = 65.0
    else:
        raw_score = 85.0

    return ComponentReading(
        name=NAME,
        value={"events": events, "horizon_days": HORIZON_DAYS, "top_events": top_events, "event_risk": event_risk},
        status="ok",
        kind="direct",
        sources=[source("FRED release calendar")],
        narrative=narrative,
        narrative_version="1.1.0",
        evidence=[
            ev("next_event", events[0]["label"] if events else None, today.isoformat(), "FRED release calendar"),
            ev("days_to_next", days_to_next, today.isoformat(), "FRED release calendar"),
        ],
        rule=rule,
        thresholds=[
            th("risk window ketat jika hari-ke-rilis di bawah ini", "<=", 3),
            th("risk window sedang jika hari-ke-rilis di bawah ini", "<=", 7),
        ],
        raw_score=raw_score,
    )
