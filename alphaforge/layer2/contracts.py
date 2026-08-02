"""Bentuk output Screening & Evidence — 03_LAYER2_SPECS/."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal
from datetime import datetime

Exchange = Literal["NASDAQ", "NYSE"]

# Kenapa satu field kosong (§1 Evidence audit notes) — hanya diisi untuk field
# bernilai None; field yang tidak ada di map fallback ke "unavailable" di UI.
DataAvailability = Literal["not_yet_reported", "unavailable", "not_applicable", "insufficient_data"]

# Seberapa bisa dipercaya satu field yang BERISI nilai — hanya diisi untuk
# field yang BUKAN "verified" (default kalau tidak ada di map = "verified").
DataQuality = Literal["verified", "partial", "estimated", "unverified"]


@dataclass
class ListingRow:
    symbol: str
    security_name: str
    exchange: Exchange
    is_etf: bool
    is_test_issue: bool


@dataclass
class ScreeningCandidate:
    ticker: str
    exchange: Exchange
    passed: bool
    hard_exclude_reason: str | None = None
    soft_flags: list[str] = field(default_factory=list)
    market_cap: float | None = None
    market_cap_tier: str | None = None  # micro_cap, small_cap, mid_cap, large_cap, mega_cap
    avg_dollar_volume_20d: float | None = None
    last_price: float | None = None
    price_history_days: int | None = None
    sector: str | None = None  # dari sector_map cache, kalau sudah ter-klasifikasi

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScreeningResult:
    universe_raw: int              # jumlah ticker mentah dari listing file
    universe_after_cheap_filter: int  # setelah exclude ETF/test issue/non-common-stock
    universe_scanned: int          # jumlah ticker yang benar-benar dicek (bisa dibatasi --limit)
    passed: list[ScreeningCandidate]
    hard_excluded: list[ScreeningCandidate]
    generated_at: str
    sector_filter: str | None = None            # sektor yang diminta, kalau screening di-filter per sektor
    universe_after_sector_filter: int | None = None  # jumlah setelah filter sektor (sebelum --limit)

    def to_dict(self) -> dict:
        return {
            "universe_raw": self.universe_raw,
            "universe_after_cheap_filter": self.universe_after_cheap_filter,
            "universe_scanned": self.universe_scanned,
            "sector_filter": self.sector_filter,
            "universe_after_sector_filter": self.universe_after_sector_filter,
            "passed_count": len(self.passed),
            "hard_excluded_count": len(self.hard_excluded),
            "generated_at": self.generated_at,
            "passed": [c.to_dict() for c in self.passed],
            "hard_excluded": [c.to_dict() for c in self.hard_excluded],
        }


@dataclass
class SourceMetadata:
    """Metadata untuk tracking provenance + completeness."""
    source: str  # "yahoo_finance", "sec_edgar", "finnhub", dll
    fetched_at: str  # ISO datetime string
    status: Literal["ok", "missing", "degraded"]  # ok=lengkap, missing=gagal fetch, degraded=partial


@dataclass
class PriceBar:
    """Satu bar OHLCV."""
    date: str  # ISO date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class PriceMarketData:
    metadata: SourceMetadata
    last_price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    shares_outstanding: int | None = None
    beta: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    # ~5 tahun OHLCV: ~1 tahun terakhir daily (252 bar), sisanya downsampled
    # ke akhir-bulan -- lihat _downsample_price_history. Bar TIDAK berjarak
    # seragam; konsumen yang plot chart harus posisikan berdasarkan `date`,
    # bukan indeks array (lihat sparklinePoints di frontend).
    price_history: list[PriceBar] = field(default_factory=list)


@dataclass
class QuarterlyFundamental:
    """Satu quarter fundamental data dari SEC EDGAR 10-K/10-Q."""
    period: str  # '2024-Q1' atau '2024-09-30'
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    cash_from_operations: float | None = None
    capital_expenditures: float | None = None
    fiscal_date: str | None = None  # ISO date end of period


@dataclass
class FundamentalData:
    """Ringkasan fundamental dari Yahoo Finance + quarterly dari SEC EDGAR."""
    metadata: SourceMetadata
    revenue: float | None = None
    net_income: float | None = None
    eps: float | None = None
    pe_ratio: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    roe: float | None = None
    roa: float | None = None
    operating_margin: float | None = None
    gross_margin: float | None = None
    free_cash_flow: float | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    book_value_per_share: float | None = None
    asset_turnover: float | None = None
    inventory_turnover: float | None = None
    interest_coverage: float | None = None
    sector: str | None = None
    industry: str | None = None
    quarterly_data: list[QuarterlyFundamental] = field(default_factory=list)  # Last 8 quarters dari EDGAR
    shares_outstanding_change_12m: float | None = None  # % perubahan ~12 bulan (SEC XBRL), baseline dilution
    field_availability: dict[str, DataAvailability] = field(default_factory=dict)
    field_quality: dict[str, DataQuality] = field(default_factory=dict)
    cross_reference_notes: list[str] = field(default_factory=list)


@dataclass
class CompanyProfile:
    """Identitas & deskripsi perusahaan — dari field `.info` Yahoo Finance
    yang sudah ke-cache (yahoo_info) tapi sebelumnya dibuang tanpa diekstrak.
    Tidak ada network call baru."""
    metadata: SourceMetadata
    long_name: str | None = None
    business_summary: str | None = None
    website: str | None = None
    employees: int | None = None
    city: str | None = None
    country: str | None = None


@dataclass
class EpsSurprise:
    """Satu kuartal histori EPS actual vs estimate analis (yfinance earnings_history)."""
    quarter: str  # ISO date, akhir kuartal fiskal
    eps_actual: float | None = None
    eps_estimate: float | None = None
    surprise_pct: float | None = None


@dataclass
class RevenueEstimatePeriod:
    """Satu horizon konsensus revenue analis (kuartal ini/depan, tahun ini/depan)."""
    period: str  # "0q", "+1q", "0y", "+1y"
    avg: float | None = None
    low: float | None = None
    high: float | None = None
    growth: float | None = None
    num_analysts: int | None = None


@dataclass
class PriceTargetSnapshot:
    """Historical snapshot dari analyst price target consensus pada satu titik waktu."""
    date: str  # ISO date (YYYY-MM-DD)
    target_mean: float | None = None
    target_median: float | None = None
    target_low: float | None = None
    target_high: float | None = None
    num_analysts: int | None = None


@dataclass
class AnalystEstimates:
    """Konsensus analis — price target (dari `.info`, gratis) + EPS surprise
    history & revenue estimate forward (dari yfinance earnings_history /
    revenue_estimate, network call baru per ticker)."""
    metadata: SourceMetadata
    target_low: float | None = None
    target_high: float | None = None
    target_mean: float | None = None
    target_median: float | None = None
    recommendation_mean: float | None = None
    recommendation_key: str | None = None
    num_analyst_opinions: int | None = None
    eps_surprise_history: list[EpsSurprise] = field(default_factory=list)
    revenue_estimates: list[RevenueEstimatePeriod] = field(default_factory=list)
    price_target_history: list[PriceTargetSnapshot] = field(default_factory=list)
    field_availability: dict[str, DataAvailability] = field(default_factory=dict)
    field_quality: dict[str, DataQuality] = field(default_factory=dict)


@dataclass
class InstitutionalHolder:
    """Satu institusi pemegang saham — dari Yahoo Finance `institutional_holders`
    (agregasi Yahoo atas SEC 13F, bukan parsing 13F manual)."""
    holder: str
    shares: int | None = None
    pct_held: float | None = None  # persentase dari total shares outstanding
    value_usd: float | None = None
    date_reported: str | None = None
    pct_change: float | None = None  # perubahan kepemilikan vs laporan sebelumnya


@dataclass
class InstitutionalOwnership:
    """Kepemilikan institusional."""
    metadata: SourceMetadata
    percentage: float | None = None  # persentase agregat institusi dari Yahoo
    # Persentase kepemilikan insider (Yahoo `.info["heldPercentInsiders"]`) --
    # field yang SAMA persis sudah dipakai buat `percentage` di atas
    # (heldPercentInstitutions, dari objek .info yang sama), tapi versi
    # insider-nya sebelumnya tidak pernah diambil sama sekali (audit
    # 2026-07-29: Knowledge.ownership.insider_pct 0% terisi di 4055 ticker,
    # bukan jarang -- field-nya cuma gak pernah dibaca).
    insider_percentage: float | None = None
    top_holders: list[InstitutionalHolder] = field(default_factory=list)  # top ~10 institusi pemegang terbesar


@dataclass
class CompanyNews:
    """Satu berita perusahaan."""
    headline: str
    source: str
    published_at: str  # ISO datetime
    url: str | None
    evidence_id: str | None = None  # Finnhub's own article id (stabil, tidak berubah antar-fetch)


@dataclass
class NewsCollection:
    """Kumpulan berita terkini."""
    news: list[CompanyNews] = field(default_factory=list)
    metadata: SourceMetadata | None = None


@dataclass
class SecFiling:
    """Ringkasan satu SEC filing."""
    form_type: str  # "10-K", "10-Q", "8-K", dll
    filing_date: str  # ISO date
    url: str | None
    items: str | None = None  # Kode item 8-K resmi SEC, comma-separated (mis. "4.01,9.01") — cuma bermakna untuk form_type=="8-K"


@dataclass
class SecFilings:
    """Daftar SEC filings terkini."""
    filings: list[SecFiling] = field(default_factory=list)
    metadata: SourceMetadata | None = None


@dataclass
class InstitutionalTrade:
    """Satu transaksi insider/institusional dari SEC Form 4.

    CATATAN MVP (lihat sources/sec_form4.py): fetcher saat ini TIDAK parse
    XML Form 4 (SEC archive path per-filing tidak predictable, sering 404).
    Jadi tiap Form 4 filing dalam window direpresentasikan sebagai satu
    "trade" sintetis: transaction_type selalu "filing" (bukan "buy"/"sell"
    sungguhan), shares selalu 0, price selalu None, trader_name adalah
    placeholder "[Form 4 Filer]" — bukan nama insider asli. Field type hint
    di bawah ("buy"/"sell"/"grant"/"exercise") menggambarkan skema masa
    depan (_parse_form4_xml, sudah ada tapi belum dipanggil), bukan behavior
    saat ini."""
    trader_name: str
    relationship: str  # "Director", "Officer", "10% Owner", "Other"
    transaction_type: str  # "buy", "sell", "grant", "exercise" (skema masa depan) — MVP: selalu "filing"
    shares: int
    price: float | None  # transaction price, or None if tidak tersedia
    transaction_date: str  # ISO date
    form_type: str  # "4" (insider) atau "144" (affiliate sale)
    filing_date: str  # ISO date Form 4 was filed
    evidence_id: str | None = None  # SEC accession number — satu-satunya field yang genuinely unik per filing (lihat catatan MVP di atas, field lain sering placeholder/identik)


@dataclass
class InstitutionalActivity:
    """Ringkasan trading activity dari insider/institusional (Form 4).

    CATATAN MVP: buy_count_30d saat ini adalah JUMLAH FORM 4 FILING dalam
    window (proxy "insider terlibat"), BUKAN jumlah transaksi beli
    sungguhan — arah (beli/jual) tidak diketahui tanpa parse XML detail
    (lihat sources/sec_form4.py). sell_count_30d/net_shares_30d selalu 0
    dan top_buyer selalu placeholder generik ("[Form 4 Filers]") kalau ada
    filing — jangan diperlakukan sebagai nama insider asli atau sinyal
    net-buy/sell yang valid sampai parsing detail transaksi diimplementasi.
    """
    metadata: SourceMetadata
    recent_trades: list[InstitutionalTrade] = field(default_factory=list)  # Last 30 days
    buy_count_30d: int = 0  # MVP: jumlah Form 4 filing, bukan literal "buy" — lihat catatan di atas
    sell_count_30d: int = 0  # MVP: belum dilacak, selalu 0
    net_shares_30d: int = 0  # positive = net buys; MVP: belum dilacak, selalu 0
    top_buyer: str | None = None  # who bought most; MVP: placeholder generik, bukan nama asli
    top_seller: str | None = None  # who sold most; MVP: belum dilacak, selalu None


@dataclass
class EvidencePackage:
    """Fakta terverifikasi per satu ticker — 03_LAYER2_SPECS/02_EVIDENCE.md."""
    ticker: str
    exchange: Exchange
    price_market: PriceMarketData
    fundamental: FundamentalData
    institutional_ownership: InstitutionalOwnership
    institutional_activity: InstitutionalActivity
    news: NewsCollection
    sec_filings: SecFilings
    generated_at: str
    company_profile: CompanyProfile | None = None
    analyst_estimates: AnalystEstimates | None = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "exchange": self.exchange,
            "price_market": asdict(self.price_market),
            "fundamental": asdict(self.fundamental),
            "company_profile": asdict(self.company_profile) if self.company_profile else None,
            "analyst_estimates": asdict(self.analyst_estimates) if self.analyst_estimates else None,
            "institutional_ownership": asdict(self.institutional_ownership),
            "institutional_activity": {
                "buy_count_30d": self.institutional_activity.buy_count_30d,
                "sell_count_30d": self.institutional_activity.sell_count_30d,
                "net_shares_30d": self.institutional_activity.net_shares_30d,
                "top_buyer": self.institutional_activity.top_buyer,
                "top_seller": self.institutional_activity.top_seller,
                "recent_trades": [asdict(t) for t in self.institutional_activity.recent_trades],
                "metadata": asdict(self.institutional_activity.metadata),
            },
            "news": {
                "count": len(self.news.news),
                "items": [asdict(n) for n in self.news.news],
                "metadata": asdict(self.news.metadata) if self.news.metadata else None,
            },
            "sec_filings": {
                "count": len(self.sec_filings.filings),
                "items": [asdict(f) for f in self.sec_filings.filings],
                "metadata": asdict(self.sec_filings.metadata) if self.sec_filings.metadata else None,
            },
            "generated_at": self.generated_at,
        }
