"""Fetch daftar SEC filings terkini dari EDGAR submissions API.

Endpoint `https://data.sec.gov/submissions/CIK{cik}.json` resmi dan gratis,
tapi wajib header User-Agent (https://www.sec.gov/os/webmaster-faq#developers)
— tanpa header ini semua request di-403. Fix ini yang sebelumnya bikin modul
selalu return status=missing walau data-nya sebenarnya tersedia.

Lihat 03_LAYER2_SPECS/02_EVIDENCE.md §1.5 & 04_DATA_SOURCES/01_PROVIDERS_OVERVIEW.md.
"""
from __future__ import annotations

import sys
from datetime import date as _date, datetime, timedelta, timezone

import requests

from ... import cache
from ..contracts import SecFiling, SecFilings, SourceMetadata
from ._retry import retry
from .sec_parser import (
    SEC_USER_AGENT, SEC_RETRIES, SEC_RETRY_BACKOFF_SECONDS,
    apply_sec_rate_limit, get_cik_from_ticker,
)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_SUBMISSIONS_TTL = 24 * 3600  # 24 jam

_HEADERS = {"User-Agent": SEC_USER_AGENT}

# Form yang relevan untuk analisis fundamental/governance; abaikan Form 3/4/144
# (insider transaction) yang volumenya tinggi tapi tidak dibutuhkan di sini.
_RELEVANT_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A", "DEF 14A"}

# Jendela = pemeriksaan governance terpanjang di risk.py (auditor 3 tahun).
# Kalau ada pemeriksaan baru dengan jendela lebih panjang, angka ini yang
# harus ikut naik -- kalau tidak, pemeriksaan itu diam-diam kekurangan data.
FILINGS_WINDOW_YEARS = 3.0
FILINGS_HARD_CAP = 200


def _parse_date(value: str | None) -> _date | None:
    try:
        return _date.fromisoformat(value) if value else None
    except ValueError:
        return None


def fetch_raw_submissions(cik: str) -> dict | None:
    """Fetch (atau baca dari cache 24 jam) submissions JSON mentah untuk satu CIK.

    Dipakai bersama oleh fetch_sec_filings() di bawah dan
    sec_form4.fetch_institutional_activity() — dua-duanya butuh persis payload
    yang sama (`data.sec.gov/submissions/CIK{cik}.json`, daftar filing terkini),
    jadi di-cache & dipakai ulang lewat satu fungsi supaya tidak fetch network
    2x per ticker per run Evidence (sebelumnya masing-masing modul fetch +
    cache sendiri-sendiri di namespace cache berbeda, dobel hit ke data.sec.gov
    untuk data yang identik)."""
    cache_key = f"submissions_{cik}"
    data = cache.get("sec_edgar", cache_key, _SUBMISSIONS_TTL)
    if data is not None:
        return data

    apply_sec_rate_limit()

    def _do_fetch():
        r = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=_HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()

    try:
        data = retry(_do_fetch, retries=SEC_RETRIES, backoff_seconds=SEC_RETRY_BACKOFF_SECONDS,
                    label=f"sec_submissions:{cik}")
        cache.set("sec_edgar", cache_key, data)
        return data
    except Exception as exc:
        print(f"[sec_submissions:{cik}] gagal (final): {exc}", file=sys.stderr)
        return None


def fetch_sec_filings(ticker: str, window_years: float = FILINGS_WINDOW_YEARS,
                      hard_cap: int = FILINGS_HARD_CAP) -> SecFilings:
    """Ambil filing (10-K/10-Q/8-K/proxy) dalam jendela `window_years` tahun.

    Dulu dibatasi 10 filing terbaru. Diukur atas 456 ticker dari cache
    (2026-08-03), 10 filing itu cuma menjangkau median 201 hari ke belakang
    dan cuma 6,4% ticker yang jangkauannya sampai 3 tahun — padahal
    pemeriksaan governance di risk.py bernalar atas jendela 2-3 tahun. Efek
    nyatanya: item 4.01 (ganti auditor) kelewat di 52 dari 80 ticker yang
    punya, item 4.02 (restatement) kelewat di 19 dari 22. Batas berbasis
    TANGGAL menghapus ketimpangan itu.

    Tidak ada request jaringan baru: fetch_raw_submissions() sejak awal sudah
    menarik & meng-cache seluruh payload (ratusan filing), truncate ke 10 cuma
    terjadi di sisi kita.

    hard_cap membendung issuer ekstrem (ada yang 757 filing relevan dalam 3
    tahun) supaya evidence.json tidak meledak. Kalau cap kena, filing yang
    disimpan tetap yang TERBARU — jadi jendela efektifnya memendek untuk
    ticker itu, dan history_start di bawah yang jadi penanda kejujurannya.
    """
    cik = get_cik_from_ticker(ticker)
    if not cik:
        metadata = SourceMetadata(
            source="sec_edgar",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="missing",
        )
        return SecFilings(filings=[], metadata=metadata)

    data = fetch_raw_submissions(cik)

    if not data:
        metadata = SourceMetadata(
            source="sec_edgar",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="missing",
        )
        return SecFilings(filings=[], metadata=metadata)

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    # Kode item 8-K resmi (mis. "4.01,9.01") -- sudah ada di response SEC,
    # sebelumnya dibuang. Cuma terisi untuk form_type=="8-K", string kosong
    # untuk form lain (10-K/10-Q/dll tidak punya konsep item number).
    items_arr = recent.get("items", [])

    cutoff = (datetime.now(timezone.utc) - timedelta(days=365 * window_years)).date()

    filings: list[SecFiling] = []
    history_start: str | None = None
    for i, (form, date, accn, doc) in enumerate(zip(forms, dates, accessions, docs)):
        filed = _parse_date(date)
        # history_start dihitung atas SEMUA form (termasuk Form 4 dll), bukan
        # cuma _RELEVANT_FORMS: yang ditanya "sejauh mana payload SEC ini
        # menjangkau ke belakang", bukan "sejauh mana filing yang kita simpan".
        if filed and (history_start is None or date < history_start):
            history_start = date
        if form not in _RELEVANT_FORMS:
            continue
        if filed and filed < cutoff:
            continue
        if len(filings) >= hard_cap:
            continue
        accn_nodash = accn.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn_nodash}/{doc}"
        items = items_arr[i] if i < len(items_arr) and items_arr[i] else None
        filings.append(SecFiling(form_type=form, filing_date=date, url=url, items=items))

    metadata = SourceMetadata(
        source="sec_edgar",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        status="ok" if filings else "degraded",
    )
    return SecFilings(filings=filings, metadata=metadata, history_start=history_start)
