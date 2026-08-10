"""Agregasi Aliran Dana Institusi — "ke saham/sektor apa dana institusi bergerak".

Membaca `institutional_ownership.top_holders` yang SUDAH diambil Evidence
(yahoo_evidence._fetch_institutional_holders_detail) — nol panggilan jaringan
baru, nol perhitungan ulang: stage ini murni menjumlahkan apa yang sudah ada
di memori saat pipeline berjalan.

Semua batas sumber datanya dijelaskan di institutional_flow_contracts.py;
yang paling menentukan bentuk kode di bawah adalah sentinel Yahoo
`pctChange = 100.0` yang berarti "posisi baru", bukan "+100%". Diukur pada
run 2026-08-01: 10.067 dari 39.719 baris pemegang memakai sentinel itu, dan
142 saham (3,5%) SELURUH pemegangnya sentinel. Kalau sentinel diperlakukan
sebagai besaran, papan peringkat teratas akan berisi ratusan saham mikro
bernilai +100% seragam dan angka yang berarti tenggelam di bawahnya.

Tidak ada penilaian apa pun di sini: tidak ada stance, skor, atau
rekomendasi. Institusi menambah posisi bukan berarti saham itu bagus — modul
ini melaporkan arah kepemilikan, titik.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from .institutional_flow_contracts import (
    InstitutionalFlowReport,
    SectorFlow,
    TickerFlow,
)

METHOD_VERSION = "1.0.0"

# Di atas ambang ini `pct_change` dianggap penanda "posisi baru", bukan
# besaran. Yahoo mengirim tepat 100.0; ambang sedikit di bawahnya dipakai
# supaya pembulatan float tidak meleset. Pola & angka yang sama sudah dipakai
# di TickerModal.jsx untuk badge beli/jual per pemegang — kalau salah satu
# berubah, keduanya harus berubah bersama.
NEW_POSITION_SENTINEL = 99.5

# Syarat masuk papan peringkat. Keduanya menyaring hal yang berbeda:
# jumlah pemegang ber-basis menjaga supaya satu pemegang tidak menentukan
# seluruh angka sebuah saham, sedangkan lantai nilai menyingkirkan saham
# mikro yang persentasenya besar cuma karena penyebutnya kecil.
MIN_HOLDERS_FOR_RANKING = 5
MIN_TOP_HOLDER_VALUE_USD = 5e8

# Sektor butuh sampel lebih longgar dari papan peringkat: sebuah saham boleh
# ikut menyumbang ke total sektor dengan basis lebih tipis, karena di sana ia
# cuma salah satu dari puluhan/ratusan penyumbang.
MIN_HOLDERS_FOR_SECTOR = 3

UNKNOWN_SECTOR = "Tidak diketahui"


def _get(obj, name, default=None):
    """Baca field dari dataclass ATAU dict.

    Jalur pipeline mengoper objek `EvidencePackage`/`KnowledgeProfile`, tapi
    scripts/build_institutional_flow.py membaca file JSON yang sudah ditulis
    (supaya halaman bisa terisi tanpa menunggu full run ~3 jam). Satu fungsi
    agregasi melayani keduanya; menduplikasi logikanya untuk dua bentuk input
    adalah cara termudah membuat keduanya diam-diam berbeda.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _basis_shares(shares: float, pct_change: float) -> float | None:
    """Lembar SEBELUM perubahan, dihitung mundur dari lembar sekarang.

    `pct_change <= -100` berarti posisi habis; basisnya tidak bisa dipulihkan
    dari angka sekarang (pembagian nol/negatif), jadi pemegang itu dilewati
    daripada memaksa hasil yang mengarang.
    """
    factor = 1.0 + pct_change / 100.0
    if factor <= 0:
        return None
    return shares / factor


def _flow_for_ticker(ticker: str, ownership, sector: str | None) -> TickerFlow:
    holders = _get(ownership, "top_holders") or []
    flow = TickerFlow(
        ticker=ticker,
        sector=sector,
        holders_total=len(holders),
        institutional_pct=_get(ownership, "percentage"),
    )

    add_shares = cut_shares = base_shares = 0.0
    dates: Counter[str] = Counter()

    for h in holders:
        value = _get(h, "value_usd") or 0.0
        flow.top_holder_value_usd += value
        date_reported = _get(h, "date_reported")
        if date_reported:
            dates[date_reported] += 1

        pct = _get(h, "pct_change")
        if pct is None:
            continue
        if pct >= NEW_POSITION_SENTINEL:
            # Posisi baru: arahnya jelas menambah, besarannya TIDAK diketahui.
            # Dihitung sebagai kejadian, tidak pernah sebagai angka.
            flow.new_position_count += 1
            continue

        # Arah selalu diketahui dari tanda pct_change, bahkan ketika
        # besarannya tidak bisa dihitung (posisi habis, lembar/nilai kosong).
        # Karena itu hitungan arah dan hitungan basis dipisah: yang pertama
        # menjawab "berapa pemegang yang menambah", yang kedua menjawab
        # "berapa yang angkanya bisa dipakai" -- dan kelayakan papan peringkat
        # bersandar pada yang kedua.
        if pct > 0:
            flow.increased_count += 1
        elif pct < 0:
            flow.reduced_count += 1
        else:
            flow.unchanged_count += 1

        shares = _get(h, "shares") or 0.0
        basis = _basis_shares(shares, pct) if shares else None
        value_basis = _basis_shares(value, pct) if value else None
        if basis is None and value_basis is None:
            continue

        flow.holders_with_basis += 1

        if basis is not None:
            delta = shares - basis
            base_shares += basis
            if delta >= 0:
                add_shares += delta
            else:
                cut_shares += -delta

        if value_basis is not None:
            delta_value = value - value_basis
            flow.net_value_usd += delta_value
            flow.gross_value_usd += abs(delta_value)

    if base_shares:
        flow.net_share_change_pct = (add_shares - cut_shares) / base_shares * 100.0
    if dates:
        flow.report_date = dates.most_common(1)[0][0]
        flow.report_dates = sorted(dates)

    flow.eligible_for_ranking = (
        flow.holders_with_basis >= MIN_HOLDERS_FOR_RANKING
        and flow.top_holder_value_usd >= MIN_TOP_HOLDER_VALUE_USD
        and flow.net_share_change_pct is not None
    )
    return flow


def _roll_up_sectors(flows: list[TickerFlow]) -> list[SectorFlow]:
    buckets: dict[str, SectorFlow] = defaultdict(lambda: SectorFlow(sector=UNKNOWN_SECTOR))
    for f in flows:
        if f.holders_with_basis < MIN_HOLDERS_FOR_SECTOR:
            continue
        name = f.sector or UNKNOWN_SECTOR
        s = buckets[name]
        s.sector = name
        s.ticker_count += 1
        s.net_value_usd += f.net_value_usd
        s.gross_value_usd += f.gross_value_usd
        if f.net_value_usd > 0:
            s.tickers_net_up += 1
        elif f.net_value_usd < 0:
            s.tickers_net_down += 1

    for s in buckets.values():
        if s.gross_value_usd:
            s.net_ratio_pct = s.net_value_usd / s.gross_value_usd * 100.0
    return sorted(buckets.values(), key=lambda s: s.net_value_usd, reverse=True)


def run_institutional_flow(evidence_packages, profiles=None) -> InstitutionalFlowReport:
    """Agregasi seluruh populasi.

    `profiles` (KnowledgeProfile) dipakai HANYA untuk memetakan ticker ->
    sektor; sektor tidak ada di Evidence dalam bentuk yang seragam. Boleh
    None — roll-up sektor lalu jatuh ke satu bucket "Tidak diketahui" alih-alih
    gagal, karena arah per saham tetap benar tanpa sektor.
    """
    sector_by_ticker = {
        _get(p, "ticker"): _get(p, "sector") for p in (profiles or [])
    }

    flows: list[TickerFlow] = []
    date_counts: Counter[str] = Counter()
    date_presence: Counter[str] = Counter()
    tickers_total = 0

    for pkg in evidence_packages:
        ownership = _get(pkg, "institutional_ownership")
        if ownership is None:
            continue
        ticker = _get(pkg, "ticker")
        if not ticker:
            continue
        tickers_total += 1
        flow = _flow_for_ticker(ticker, ownership, sector_by_ticker.get(ticker))
        if flow.holders_total == 0:
            continue
        flows.append(flow)
        if flow.report_date:
            date_counts[flow.report_date] += 1
        for d in flow.report_dates:
            date_presence[d] += 1

    report = InstitutionalFlowReport(
        method_version=METHOD_VERSION,
        generated_at=datetime.now(timezone.utc).isoformat(),
        tickers_total=tickers_total,
        tickers_with_holders=len(flows),
        tickers_with_basis=sum(1 for f in flows if f.holders_with_basis > 0),
        # Saham yang PUNYA pemegang tapi tidak satu pun bisa dijadikan basis
        # arah: seluruhnya sentinel posisi-baru. Dilaporkan terpisah supaya
        # "tidak ada arah" tidak terbaca sebagai "arahnya nol".
        tickers_new_positions_only=sum(
            1 for f in flows if f.holders_with_basis == 0 and f.new_position_count > 0
        ),
        report_date_counts=dict(date_counts.most_common()),
        report_date_presence_counts=dict(date_presence.most_common()),
        primary_report_date=date_counts.most_common(1)[0][0] if date_counts else None,
    )
    report.net_value_usd = sum(f.net_value_usd for f in flows)
    report.gross_value_usd = sum(f.gross_value_usd for f in flows)
    if report.gross_value_usd:
        report.net_ratio_pct = report.net_value_usd / report.gross_value_usd * 100.0

    report.sectors = _roll_up_sectors(flows)
    report.flows = sorted(flows, key=lambda f: f.ticker)
    return report
