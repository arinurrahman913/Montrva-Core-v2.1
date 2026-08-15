"""04_DATA_SOURCES/03_MARKET_LISTING_SOURCES.md — daftar ticker NASDAQ + NYSE.

Sumber: NASDAQ Trader symbol directory (gratis, publik, tidak perlu API key).
"""
from __future__ import annotations

import sys
import time

import requests

from ...cache import get as cache_get, set as cache_set, get_stale as cache_get_stale
from ..contracts import ListingRow

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

LISTING_TTL_SECONDS = 24 * 3600  # daftar ticker tidak berubah drastis harian
FETCH_RETRIES = 3
FETCH_RETRY_BACKOFF_SECONDS = 3.0

# Kata kunci nama sekuritas yang menandakan BUKAN common stock — dipakai untuk
# hard exclude "tipe listing" (03_LAYER2_SPECS/01_SCREENING.md). Ini heuristik
# berbasis teks karena listing file tidak punya kolom "instrument type" eksplisit
# selain ETF; didokumentasikan sebagai simplifikasi sadar, bukan disamarkan.
NON_COMMON_STOCK_KEYWORDS = [
    "warrant", " right", " rights", " unit", " units", "preferred",
    "depositary share", "depository share", "trust preferred", " notes",
    "acquisition corp - class", "acquisition corp. - class",
]


def _parse_pipe_table(text: str) -> list[list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # baris terakhir NASDAQ Trader adalah footer "File Creation Time: ..."
    if lines and lines[-1].lower().startswith("file creation time"):
        lines = lines[:-1]
    rows = [ln.split("|") for ln in lines]
    return rows


def _fetch_text(url: str) -> str:
    """GET dengan retry (backoff linear) — NASDAQ Trader kadang timeout/503
    sesaat, jangan langsung nyerah di percobaan pertama."""
    last_exc: Exception | None = None
    for attempt in range(1, FETCH_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < FETCH_RETRIES:
                print(f"listing fetch gagal ({url}), percobaan {attempt}/{FETCH_RETRIES}: {exc} — retry dalam {FETCH_RETRY_BACKOFF_SECONDS}s",
                      file=sys.stderr)
                time.sleep(FETCH_RETRY_BACKOFF_SECONDS)
    raise last_exc


def fetch_universe(use_cache: bool = True) -> list[ListingRow]:
    """Gabungan NASDAQ + NYSE, sudah melewati exclude ETF/test issue/non-common-stock
    (hard exclude "tipe listing" — zero panggilan API, langsung dari kolom listing file).

    Kalau fetch fresh gagal total (situs down, retry habis) DAN ada cache lama
    (meski sudah lewat TTL 24 jam), fallback pakai itu daripada crash total —
    daftar ticker NASDAQ+NYSE tidak berubah drastis dalam semalam, jadi data
    yang agak basi masih jauh lebih baik daripada pipeline berhenti."""
    cached = cache_get("listing", "universe", LISTING_TTL_SECONDS) if use_cache else None
    if cached is not None:
        return [ListingRow(**row) for row in cached]

    try:
        rows: list[ListingRow] = []

        nasdaq_text = _fetch_text(NASDAQ_URL)
        header, *data = _parse_pipe_table(nasdaq_text)
        idx = {name: i for i, name in enumerate(header)}
        for r in data:
            if len(r) != len(header):
                continue
            symbol = r[idx["Symbol"]].strip()
            name = r[idx["Security Name"]].strip()
            is_etf = r[idx["ETF"]].strip() == "Y"
            is_test = r[idx["Test Issue"]].strip() == "Y"
            rows.append(ListingRow(symbol=symbol, security_name=name, exchange="NASDAQ",
                                    is_etf=is_etf, is_test_issue=is_test))

        other_text = _fetch_text(OTHER_URL)
        header2, *data2 = _parse_pipe_table(other_text)
        idx2 = {name: i for i, name in enumerate(header2)}
        for r in data2:
            if len(r) != len(header2):
                continue
            if r[idx2["Exchange"]].strip() != "N":  # 'N' = NYSE; spec cuma minta NASDAQ+NYSE
                continue
            symbol = r[idx2["ACT Symbol"]].strip()
            name = r[idx2["Security Name"]].strip()
            is_etf = r[idx2["ETF"]].strip() == "Y"
            is_test = r[idx2["Test Issue"]].strip() == "Y"
            rows.append(ListingRow(symbol=symbol, security_name=name, exchange="NYSE",
                                    is_etf=is_etf, is_test_issue=is_test))
    except requests.exceptions.RequestException as exc:
        stale = cache_get_stale("listing", "universe") if use_cache else None
        if stale is None:
            raise
        stale_data, age_seconds = stale
        print(f"listing fetch gagal total ({exc}) — fallback ke cache lama umur {age_seconds/3600:.1f} jam",
              file=sys.stderr)
        return [ListingRow(**row) for row in stale_data]

    cache_set("listing", "universe", [vars(r) for r in rows])
    return rows


# Frasa yang menandai American Depositary Receipt/Share — saham biasa
# perusahaan non-AS yang diperdagangkan di bursa AS. TINGGAL DI SINI, bukan di
# screening.py, karena ini fakta tentang NAMA SEKURITAS dari berkas listing,
# dan karena dua daftar kata yang saling menimpa di dua berkas berbeda adalah
# persis penyebab bug yang diperbaiki di bawah.
ADR_KEYWORDS = ["american depositary share", "american depositary receipt",
                "american depository share", "american depository receipt"]

# Kata kunci di NON_COMMON_STOCK_KEYWORDS yang secara harfiah TERKANDUNG di
# dalam nama ADR ("american depositary shares" memuat "depositary share").
# Cuma dua kata inilah yang boleh dimaafkan untuk ADR; sisanya (preferred,
# warrant, notes, unit) tetap berlaku penuh — ADR preferen atau warrant ADR
# tetap bukan saham biasa.
_ADR_OVERLAPPING_KEYWORDS = {"depositary share", "depository share"}

# Klausa yang memisahkan "sekuritas yang TERDAFTAR" dari "saham yang DIWAKILI".
# Nama ADR panjang dan deskriptif: "… American Depositary Shares, each
# representing the right to receive twenty (20) Series L Shares". Semua kata
# sesudah klausa ini menggambarkan saham dasarnya, bukan sekuritas yang
# diperdagangkan di bursa AS.
_ADR_UNDERLYING_SPLIT = ("representing", "repstg", "to purchase", "consists of")

# Satu pengecualian dari aturan di atas: PREFEREN menular. ADS yang mewakili
# saham preferen berperilaku seperti preferen (ITUB, PBR.A, CIB), jadi kata ini
# tetap diperiksa di SELURUH nama, bukan cuma di kepalanya.
_ADR_UNDERLYING_VETO = "preferred"


def is_adr(security_name: str) -> bool:
    name_lower = (security_name or "").lower()
    return any(kw in name_lower for kw in ADR_KEYWORDS)


def _adr_objections(name_lower: str) -> set[str]:
    """Keberatan yang benar-benar berlaku untuk sebuah ADR.

    Ada karena NON_COMMON_STOCK_KEYWORDS mencocokkan PROSA, bukan jenis
    sekuritas: `" right"` menangkap "the right to receive", `" unit"`
    menangkap "each representing one unit". Pada nama non-ADR yang pendek
    ("Acme Corp. - Common Stock") itu tidak pernah terlihat; pada nama ADR
    yang deskriptif, ia membuang America Movil, Coca-Cola FEMSA, Santander
    Brasil, RLX, dan Waterdrop — semuanya saham biasa.

    Karena itu kata kunci diuji pada KEPALA nama saja (sekuritas yang
    terdaftar), kecuali "preferred" yang tetap diuji di seluruh nama."""
    head = name_lower
    for sep in _ADR_UNDERLYING_SPLIT:
        head = head.split(sep, 1)[0]
    obj = {kw.strip() for kw in NON_COMMON_STOCK_KEYWORDS
           if kw in head and kw.strip() not in _ADR_OVERLAPPING_KEYWORDS}
    if _ADR_UNDERLYING_VETO in name_lower:
        obj.add(_ADR_UNDERLYING_VETO)
    return obj


def is_common_stock(row: ListingRow) -> bool:
    """Apakah baris listing ini saham biasa yang layak masuk universe.

    ADR DIMAAFKAN SECARA EKSPLISIT (audit 15 Agu 2026). Sebelum ini, kata
    kunci `"depositary share"` — yang ada untuk membuang *preferred*
    depositary shares — ikut menelan setiap ADR, karena "american depositary
    shares" memuat substring itu. Akibatnya 274 sekuritas (ARGX, AMRN, ABVX,
    AKTX, …) hilang dari SELURUH sistem tanpa jejak: mereka dibuang di
    cheap_filter, tahap yang tidak membuat record apa pun, jadi tidak muncul
    di `passed` maupun di `hard_excluded` beserta alasannya.

    Efek kedua yang sama pentingnya: `soft_flags.append("adr")` di
    screening.py menjadi kode yang TIDAK MUNGKIN dieksekusi — dua aturan di
    pipeline yang sama saling bertentangan, satu bilang "ADR sekuritas valid,
    cukup ditandai", satu lagi menghapusnya lebih dulu. Diukur atas universe
    nyata: 274 ADR, 0 lolos, 0 pernah ditandai."""
    if row.is_etf or row.is_test_issue:
        return False
    if "$" in row.symbol or "." in row.symbol:  # kelas saham/unit khusus, notasi non-standar
        return False
    name_lower = row.security_name.lower()
    hits = {kw.strip() for kw in NON_COMMON_STOCK_KEYWORDS if kw in name_lower}
    if not hits:
        return True
    if not is_adr(name_lower):
        return False
    return not _adr_objections(name_lower)


def cheap_filter(universe: list[ListingRow]) -> list[ListingRow]:
    """Hard exclude tipe listing & test issue — tanpa panggilan API sama sekali
    (03_LAYER2_SPECS/01_SCREENING.md, "Cara Kerja — Sumber Data per Tahap Filter" #1)."""
    return [r for r in universe if is_common_stock(r)]
