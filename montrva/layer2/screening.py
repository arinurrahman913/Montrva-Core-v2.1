"""03_LAYER2_SPECS/01_SCREENING.md — hard exclude + soft flag.

Batching & delay antar request mengikuti 04_DATA_SOURCES/05_RATE_LIMIT_CACHING_STRATEGY.md
(env var YF_BATCH_SIZE / YF_BATCH_DELAY_SECONDS, pola yang sama dipakai v1).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from ..cache import CACHE_DIR, get as cache_get, set as cache_set
from .contracts import ScreeningCandidate, ScreeningResult
from .sources._retry import retry
from .sources.listing import cheap_filter, fetch_universe
from .sources.sector_map import load_sector_map

BATCH_SIZE = int(os.environ.get("YF_BATCH_SIZE", "50"))
BATCH_DELAY_SECONDS = float(os.environ.get("YF_BATCH_DELAY_SECONDS", "2.0"))
BATCH_FETCH_RETRIES = 2
BATCH_FETCH_RETRY_BACKOFF_SECONDS = 5.0

PRICE_CACHE_TTL_SECONDS = 6 * 3600
INFO_CACHE_TTL_SECONDS = 24 * 3600

MIN_AVG_DOLLAR_VOLUME = 300_000
MIN_PRICE = 0.50
MIN_PRICE_HISTORY_DAYS = 20
MIN_MARKET_CAP = 30_000_000  # 03_LAYER2_SPECS/01_SCREENING.md — hard exclude, bukan cuma tier

# Market cap tiers (kategorisasi untuk kandidat yang lolos MIN_MARKET_CAP di atas)
MICRO_CAP_THRESHOLD = 300_000_000
SMALL_CAP_THRESHOLD = 2_000_000_000
MID_CAP_THRESHOLD = 10_000_000_000
LARGE_CAP_THRESHOLD = 100_000_000_000

RECENT_IPO_MAX_DAYS = 252
LOW_LIQUIDITY_MAX = 1_000_000

# Frasa nama sekuritas yang menandakan ADR (American Depositary Receipt/Shares)
# — heuristik teks sama seperti NON_COMMON_STOCK_KEYWORDS di sources/listing.py,
# dipakai sebagai soft flag (bukan exclude, ADR tetap sekuritas valid untuk
# screening — cuma perlu ditandai karena nuansa regulasi/pelaporan beda dari
# domestic issuer biasa).
ADR_KEYWORDS = ["american depositary shares", "american depositary receipt"]


def _chunks(seq: list, size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _get_market_cap_tier(market_cap: float | None) -> str | None:
    """Kategorikan market cap ke tier berdasarkan threshold."""
    if market_cap is None:
        return None
    if market_cap < MICRO_CAP_THRESHOLD:
        return "micro_cap"
    elif market_cap < SMALL_CAP_THRESHOLD:
        return "small_cap"
    elif market_cap < MID_CAP_THRESHOLD:
        return "mid_cap"
    elif market_cap < LARGE_CAP_THRESHOLD:
        return "large_cap"
    else:
        return "mega_cap"


def fetch_price_history_batch(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Unduh histori harga banyak ticker sekaligus, per batch + jeda + cache."""
    result: dict[str, pd.DataFrame] = {}
    to_fetch = []
    for t in tickers:
        cached = cache_get("price_history", t, PRICE_CACHE_TTL_SECONDS)
        if cached is not None:
            df = pd.DataFrame(cached)
            if not df.empty:
                df.index = pd.to_datetime(df.pop("__date__"))
            result[t] = df
        else:
            to_fetch.append(t)

    for i, batch in enumerate(_chunks(to_fetch, BATCH_SIZE)):
        if i > 0:
            time.sleep(BATCH_DELAY_SECONDS)

        def _do_fetch(batch=batch):
            # period="2y" (bukan "1y") supaya `recent_ipo` bisa membedakan IPO
            # beneran baru dari perusahaan lama — dengan cap 1y, SEMUA ticker
            # yang punya histori >=1y ke-cap di ~252 hari yang sama persis
            # dengan RECENT_IPO_MAX_DAYS, jadi flag itu salah nembak ke hampir
            # semua ticker (termasuk perusahaan puluhan tahun kayak AAL).
            return yf.download(batch, period="2y", group_by="ticker", threads=True,
                                progress=False, auto_adjust=False)

        try:
            # Dulu retry manual TANPA timeout di sini -- ditemukan live 2026-07-28:
            # yf.download() untuk satu batch nyangkut 20+ menit tanpa exception sama
            # sekali (jadi loop retry di atas tidak pernah sempat jalan), macetin
            # seluruh pipeline persis seperti bug yahoo_evidence.py yang sudah
            # diperbaiki di _retry.py — tapi fungsi ini dulu tidak memakai helper
            # itu (retry loop sendiri, terpisah), jadi fix sebelumnya tidak menutup
            # celah ini. Sekarang pakai retry() yang sama (timeout 60s per percobaan
            # via worker thread) supaya SATU batch yang macet tidak bisa menahan
            # seluruh proses lagi.
            data = retry(_do_fetch, retries=BATCH_FETCH_RETRIES,
                         backoff_seconds=BATCH_FETCH_RETRY_BACKOFF_SECONDS,
                         label=f"screening_batch_{i}")
        except Exception as exc:  # noqa: BLE001
            # Batch ini gagal total setelah retry — dulu di-skip diam-diam
            # tanpa jejak sama sekali (sampai N=50 ticker hilang tak
            # terlacak). Sekarang minimal ke-log supaya kelihatan di
            # logs/refresh_full_pipeline.log kalau ada yang hilang & kenapa.
            print(f"[screening] batch {i} ({len(batch)} ticker) gagal setelah {BATCH_FETCH_RETRIES}x percobaan: {exc} — dilewati: {batch}",
                  file=sys.stderr)
            continue
        for t in batch:
            try:
                df = data[t] if len(batch) > 1 else data
                df = df.dropna(how="all")
            except Exception:
                df = pd.DataFrame()
            result[t] = df
            to_cache = df.reset_index()
            to_cache = to_cache.rename(columns={to_cache.columns[0]: "__date__"})
            to_cache["__date__"] = to_cache["__date__"].astype(str)
            cache_set("price_history", t, to_cache.to_dict(orient="records"))

    return result


def load_cached_price_cache(tickers: list[str] | set[str] | None = None) -> dict[str, pd.DataFrame]:
    """Rekonstruksi price_cache dari file cache price_history yang SUDAH ADA,
    tanpa panggilan jaringan sama sekali. Dipakai refresh cepat Layer 1
    (refresh_layer1.py) supaya market_breadth/market_sentiment tetap terisi
    dari data Screening terakhir tanpa harus menjalankan Screening penuh lagi.

    `tickers` membatasi ke universe tertentu — WAJIB diisi dengan daftar
    ticker yang LOLOS Screening terakhir (lihat refresh_layer1.py yang
    membacanya dari screening.json) supaya breadth dihitung atas universe
    yang PERSIS SAMA dengan run full pipeline harian (yang cuma memakai
    kandidat passed). Tanpa filter ini, folder cache bisa berisi sisa ticker
    dari run lama (mis. --screening-limit besar) sehingga angka breadth
    melenceng dari run harian. `tickers=None` → pakai semua yang ada di cache
    (fallback, mis. screening.json belum ada).

    Sengaja mengabaikan TTL: data yang dipakai ulang boleh beberapa jam/hari
    lebih tua — klasifikasi data_freshness di pipeline yang menandai fresh/
    acceptable/stale secara jujur berdasarkan tanggal harga sebenarnya. Kalau
    belum ada cache sama sekali (run pertama), return {} → breadth jadi
    missing, sama seperti perilaku lama."""
    out: dict[str, pd.DataFrame] = {}
    d = CACHE_DIR / "price_history"
    if not d.exists():
        return out
    allow = set(tickers) if tickers is not None else None
    for p in d.glob("*.json"):
        if allow is not None and p.stem not in allow:
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        records = payload.get("data") if isinstance(payload, dict) else None
        if not records:
            continue
        df = pd.DataFrame(records)
        if df.empty or "__date__" not in df.columns:
            continue
        df.index = pd.to_datetime(df.pop("__date__"))
        out[p.stem] = df
    return out


def fetch_fast_info(ticker: str) -> dict | None:
    cached = cache_get("fast_info", ticker, INFO_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    try:
        # Dipanggil per-ticker (bisa ribuan kali di full-market run, cache-miss
        # cuma di hari pertama/cache expired) -- retry() (bukan try/except polos)
        # supaya SATU ticker yang nyangkut kena timeout 60s, tidak menahan
        # ribuan ticker lain di belakangnya (kelas bug yang sama dengan batch
        # OHLCV di fetch_price_history_batch, lihat catatan di sana).
        fi = retry(lambda: yf.Ticker(ticker).fast_info, retries=1,
                   backoff_seconds=0, label=f"fast_info:{ticker}")
        data = {"market_cap": fi.get("marketCap"), "quote_type": fi.get("quoteType")}
    except Exception:
        return None
    cache_set("fast_info", ticker, data)
    return data


def _is_adr(security_name: str) -> bool:
    name_lower = (security_name or "").lower()
    return any(kw in name_lower for kw in ADR_KEYWORDS)


def evaluate_candidate(row, price_df: pd.DataFrame | None, fast_info: dict | None,
                        sector: str | None = None) -> ScreeningCandidate:
    ticker = row.symbol
    exchange = row.exchange

    if price_df is None or price_df.empty:
        return ScreeningCandidate(ticker=ticker, exchange=exchange, passed=False,
                                   hard_exclude_reason="no_price_data", sector=sector)

    history_days = len(price_df)
    if history_days < MIN_PRICE_HISTORY_DAYS:
        return ScreeningCandidate(ticker=ticker, exchange=exchange, passed=False,
                                   hard_exclude_reason="insufficient_price_history",
                                   price_history_days=history_days, sector=sector)

    last_price = float(price_df["Close"].iloc[-1])
    if last_price < MIN_PRICE:
        return ScreeningCandidate(ticker=ticker, exchange=exchange, passed=False,
                                   hard_exclude_reason="price_below_minimum",
                                   last_price=last_price, price_history_days=history_days, sector=sector)

    window = price_df.iloc[-20:] if history_days >= 20 else price_df
    avg_dollar_volume = float((window["Close"] * window["Volume"]).mean())
    if avg_dollar_volume < MIN_AVG_DOLLAR_VOLUME:
        return ScreeningCandidate(ticker=ticker, exchange=exchange, passed=False,
                                   hard_exclude_reason="avg_dollar_volume_below_minimum",
                                   last_price=last_price, avg_dollar_volume_20d=avg_dollar_volume,
                                   price_history_days=history_days, sector=sector)

    # Get market cap dan categorize ke tier
    market_cap = (fast_info or {}).get("market_cap")
    market_cap_tier = _get_market_cap_tier(market_cap)

    if market_cap is not None and market_cap < MIN_MARKET_CAP:
        return ScreeningCandidate(ticker=ticker, exchange=exchange, passed=False,
                                   hard_exclude_reason="market_cap_below_minimum",
                                   market_cap=market_cap, market_cap_tier=market_cap_tier,
                                   avg_dollar_volume_20d=avg_dollar_volume, last_price=last_price,
                                   price_history_days=history_days, sector=sector)

    soft_flags = []
    if history_days < RECENT_IPO_MAX_DAYS:
        soft_flags.append("recent_ipo")
    if avg_dollar_volume < LOW_LIQUIDITY_MAX:
        soft_flags.append("low_liquidity")
    if market_cap is None:
        soft_flags.append("market_cap_unavailable")
    if _is_adr(row.security_name):
        soft_flags.append("adr")

    return ScreeningCandidate(ticker=ticker, exchange=exchange, passed=True,
                               soft_flags=soft_flags, market_cap=market_cap,
                               market_cap_tier=market_cap_tier,
                               avg_dollar_volume_20d=avg_dollar_volume, last_price=last_price,
                               price_history_days=history_days, sector=sector)


def run_screening(limit: int | None = None, sector: str | None = None) -> tuple[ScreeningResult, dict[str, pd.DataFrame]]:
    """`sector` (opsional): filter ke satu sektor GICS SEBELUM tahap price/volume
    yang mahal, pakai sector_map cache (lihat sources/sector_map.py) — kalau
    map belum dibangun (scripts/build_sector_map.py), tickernya belum
    ter-klasifikasi sama sekali sehingga filter sektor apapun akan hasilkan
    0 kandidat (bukan silently full-scan)."""
    universe = fetch_universe()
    universe_raw = len(universe)
    survivors_cheap = cheap_filter(universe)
    universe_after_cheap_filter = len(survivors_cheap)

    sector_map: dict[str, str] = {}
    universe_after_sector_filter: int | None = None
    if sector:
        sector_map = load_sector_map()
        sector_lower = sector.lower()
        survivors_cheap = [r for r in survivors_cheap if sector_map.get(r.symbol, "").lower() == sector_lower]
        universe_after_sector_filter = len(survivors_cheap)

    scan_list = survivors_cheap[:limit] if limit else survivors_cheap
    tickers = [r.symbol for r in scan_list]

    price_data = fetch_price_history_batch(tickers)

    if not sector:
        sector_map = load_sector_map()  # non-filtering run masih mau tampilkan sector per kandidat kalau ada

    passed: list[ScreeningCandidate] = []
    hard_excluded: list[ScreeningCandidate] = []
    price_cache: dict[str, pd.DataFrame] = {}

    for row in scan_list:
        df = price_data.get(row.symbol)
        fast_info = None
        if df is not None and not df.empty and len(df) >= MIN_PRICE_HISTORY_DAYS:
            last_price_check = float(df["Close"].iloc[-1]) if not df.empty else 0
            window = df.iloc[-20:] if len(df) >= 20 else df
            avg_dv_check = float((window["Close"] * window["Volume"]).mean()) if not window.empty else 0
            if last_price_check >= MIN_PRICE and avg_dv_check >= MIN_AVG_DOLLAR_VOLUME:
                fast_info = fetch_fast_info(row.symbol)

        candidate = evaluate_candidate(row, df, fast_info, sector=sector_map.get(row.symbol))
        if candidate.passed:
            passed.append(candidate)
            if df is not None and not df.empty:
                price_cache[row.symbol] = df
        else:
            hard_excluded.append(candidate)

    result = ScreeningResult(
        universe_raw=universe_raw,
        universe_after_cheap_filter=universe_after_cheap_filter,
        universe_scanned=len(scan_list),
        passed=passed,
        hard_excluded=hard_excluded,
        generated_at=datetime.now(timezone.utc).isoformat(),
        sector_filter=sector,
        universe_after_sector_filter=universe_after_sector_filter,
    )
    return result, price_cache
