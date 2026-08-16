"""Catalyst Tracking — Layer 2 Fase A: bangun CatalystSet per ticker.

10_CATALYST_TRACKING.md + Data Contracts §5b (D-11). Diturunkan dari Evidence,
per-ticker, TANPA panggilan network baru: sumber earnings adalah Yahoo `.info`
yang SUDAH di-fetch & di-cache stage Evidence (`_fetch_yahoo_info`, cache
"yahoo_info" 24h) — field earningsTimestamp/Start/End + isEarningsDateEstimate
ada di respons itu, cuma tidak ikut diekstrak fetch_fundamental_data. Jadi
provenance-nya tetap "Evidence-stage Yahoo fetch" (memenuhi D-11), bukan sumber
baru.

Keterbatasan yang didokumentasikan jujur (bukan bug):
- Cuma katalis `earnings` dan `dividend` yang derivable dari Yahoo `.info`
  (field tanggal tersedia langsung, tanpa fetch/parse tambahan). Kategori lain
  yang diminta spec (M&A, FDA approval, Investor Day, buyback, macro event,
  regulatory update) butuh parsing ISI SEC filing (mis. item code di 8-K —
  yang kita simpan sekarang cuma form_type generik, bukan item code) atau isi
  berita (NLP) — dua-duanya scope jauh lebih besar dari sekadar field baru,
  didokumentasikan sebagai gap, bukan dipaksa di-derive dari data yang tidak
  ada. `certainty="rumored"` (rumor M&A dll) tidak pernah dihasilkan untuk
  alasan yang sama.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from .catalyst_contracts import CatalystSet, Catalyst, CatalystSource
from .sources.yahoo_evidence import _fetch_yahoo_info

METHOD_VERSION = "1.0.0"
DEFAULT_HORIZON_DAYS = 90


# Panjang lockup IPO yang lazim di AS. Konstanta, bukan angka di tengah kode:
# kalau suatu saat panjangnya diambil dari prospektus (S-1) per-emiten, yang
# berubah cuma sumber nilainya, bukan letak logikanya.
LOCKUP_DAYS = 180


def _ms_to_seconds(ms):
    """Milidetik -> detik. None tetap None."""
    try:
        return float(ms) / 1000.0 if ms is not None else None
    except (TypeError, ValueError):
        return None


def _epoch_to_date(ts) -> str | None:
    """Unix epoch seconds -> ISO date (UTC). None kalau bukan angka valid."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def build_catalyst_set(
    ticker: str,
    info: dict,
    fetched_at: str,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    now: datetime | None = None,
) -> CatalystSet:
    """Bangun CatalystSet dari Yahoo `.info` dict. `now` bisa di-inject untuk
    test deterministik."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    horizon_end = today + timedelta(days=horizon_days)
    source = CatalystSource(provider="yahoo_finance", fetched_at=fetched_at)
    catalysts: list[Catalyst] = []

    # --- Earnings ---
    start = _epoch_to_date(info.get("earningsTimestampStart"))
    end = _epoch_to_date(info.get("earningsTimestampEnd"))
    single = _epoch_to_date(info.get("earningsTimestamp"))
    earnings_date = start or single
    earnings_end = end if (end and start and end != start) else None
    is_estimate = bool(info.get("isEarningsDateEstimate", False))

    has_earnings_field = any(
        k in info for k in ("earningsTimestamp", "earningsTimestampStart", "earningsTimestampEnd")
    )

    if earnings_date:
        d = datetime.fromisoformat(earnings_date).date()
        # Hanya earnings MENDATANG dalam horizon (yang sudah lewat bukan lagi
        # katalis — sudah terjadi, tinggal hasilnya di data fundamental).
        if today <= d <= horizon_end:
            # expires 1 hari setelah tanggal earnings (atau akhir rentang).
            expiry_base = datetime.fromisoformat(earnings_end).date() if earnings_end else d
            catalysts.append(Catalyst(
                catalyst_id=f"earnings_{earnings_date}",
                kind="earnings",
                expected_at=earnings_date,
                expected_at_end=earnings_end,
                certainty="expected" if is_estimate else "scheduled",
                expires_at=(expiry_base + timedelta(days=1)).isoformat(),
                source=source,
            ))

    # --- Ex-dividend ---
    ex_div = _epoch_to_date(info.get("exDividendDate"))
    if ex_div:
        d = datetime.fromisoformat(ex_div).date()
        if today <= d <= horizon_end:
            catalysts.append(Catalyst(
                catalyst_id=f"ex_dividend_{ex_div}",
                kind="dividend",
                expected_at=ex_div,
                certainty="scheduled",
                expires_at=(d + timedelta(days=1)).isoformat(),
                source=source,
            ))

    # --- Lockup expiry pasca-IPO ---------------------------------------
    #
    # DITAMBAHKAN 15 Agu 2026 sesudah audit menemukan katalis nyaris tidak
    # membedakan apa pun: laju dasar 79,1% ticker punya katalis mendatang, dan
    # di kelompok berskor tinggi cuma 82,6% — selisih +3,5pp. Sebabnya cuma ada
    # dua jenis katalis, earnings (3.105) & dividen (916), DUA-DUANYA KUARTALAN,
    # jadi hampir semua perusahaan punya. Stance "asimetri_berkatalis" praktis
    # berarti "punya earnings terjadwal", bukan "punya pemicu yang tidak
    # dimiliki orang lain".
    #
    # Lockup expiry memecah pola itu justru karena TIDAK kuartalan: ia terjadi
    # sekali seumur hidup perusahaan, ~180 hari sesudah IPO, dan melepas pasokan
    # lembar baru ke pasar. Terukur di universe 15 Agu: 76 ticker punya lockup
    # yang jatuh dalam horizon 90 hari — 1,8% populasi, bukan 79%.
    #
    # NOL PANGGILAN JARINGAN BARU: `firstTradeDateEpochUtc` ada di dict `.info`
    # yang sudah dibaca fungsi ini untuk earnings & dividen.
    #
    # certainty="expected", BUKAN "scheduled": 180 hari adalah konvensi pasar
    # yang lazim, bukan tanggal yang diumumkan emiten. Panjang lockup yang
    # sebenarnya ada di prospektus S-1 dan bisa 90-360 hari. Menandainya
    # "scheduled" akan mengklaim kepastian yang tidak dimiliki sumbernya.
    # DUA NAMA, dan yang benar bukan yang pertama kutulis. Dict `.info` yang
    # dipakai fungsi ini memuat `firstTradeDateMilliseconds` (MILIDETIK);
    # `firstTradeDateEpochUtc` cuma muncul di sebagian respons yfinance.
    # Versi pertama kode ini hanya membaca nama kedua, dan akibatnya lolos
    # seluruh pengujian lalu menghasilkan NOL katalis di run produksi 15 Agu —
    # gagal diam-diam, bukan error. Uji unitnya ikut lolos karena memakai dict
    # buatan sendiri dengan nama yang dipilih penulisnya, bukan bentuk yang
    # benar-benar dikirim produsennya.
    first_trade = (_epoch_to_date(info.get("firstTradeDateEpochUtc"))
                   or _epoch_to_date(_ms_to_seconds(info.get("firstTradeDateMilliseconds"))))
    if first_trade:
        ipo = datetime.fromisoformat(first_trade).date()
        lockup = ipo + timedelta(days=LOCKUP_DAYS)
        if today <= lockup <= horizon_end:
            catalysts.append(Catalyst(
                catalyst_id=f"lockup_expiry_{lockup.isoformat()}",
                kind="other",
                expected_at=lockup.isoformat(),
                certainty="expected",
                expires_at=(lockup + timedelta(days=7)).isoformat(),
                source=source,
            ))

    # status: missing kalau field earnings tidak ada sama sekali di info
    # (info fetch gagal / ticker tanpa data) — beda dari "ada field tapi
    # tanggalnya sudah lewat / di luar horizon" (itu ok, cuma tidak ada
    # katalis mendatang).
    status = "ok" if has_earnings_field else "missing"

    return CatalystSet(
        ticker=ticker,
        method_version=METHOD_VERSION,
        horizon_days=horizon_days,
        catalysts=catalysts,
        status=status,
    )


def build_catalyst_for_ticker(ticker: str, horizon_days: int = DEFAULT_HORIZON_DAYS) -> CatalystSet:
    """Ambil info (dari cache Evidence) lalu bangun CatalystSet. Degradasi
    anggun: kalau info fetch gagal total, kembalikan CatalystSet status=missing
    ketimbang meledak — satu ticker tanpa katalis bukan alasan gagalkan run."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        info = _fetch_yahoo_info(ticker)
    except Exception as exc:  # noqa: BLE001
        print(f"[catalyst:{ticker}] gagal ambil info, status=missing: {exc}", file=sys.stderr)
        return CatalystSet(
            ticker=ticker, method_version=METHOD_VERSION,
            horizon_days=horizon_days, catalysts=[], status="missing",
        )
    return build_catalyst_set(ticker, info or {}, fetched_at, horizon_days)


def run_catalyst(tickers: list[str], horizon_days: int = DEFAULT_HORIZON_DAYS) -> list[CatalystSet]:
    """Bangun CatalystSet untuk banyak ticker. Menerima list ticker (bukan
    KnowledgeProfile) karena katalis diturunkan langsung dari Evidence/Yahoo
    info, bukan dari Knowledge — konsisten dengan D-11 (katalis bukan bagian
    Knowledge)."""
    results = []
    total = len(tickers)
    for i, ticker in enumerate(tickers, 1):
        if i % 200 == 0 or i == 1:
            print(f"Catalyst {i}/{total}: {ticker}", file=sys.stderr)
        results.append(build_catalyst_for_ticker(ticker, horizon_days))
    print(f"Catalyst complete: {len(results)} sets", file=sys.stderr)
    return results
