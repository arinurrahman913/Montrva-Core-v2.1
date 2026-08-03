"""Kontrak Aliran Dana Institusi — agregasi populasi atas
`EvidencePackage.institutional_ownership.top_holders`.

Ini BUKAN stage per-ticker seperti Knowledge/Risk/Reasoning: outputnya satu
laporan tingkat populasi ("ke mana dana institusi bergerak"), sejenis dengan
Peer dalam hal butuh seluruh universe, tapi tanpa penilaian apa pun terhadap
saham individual. Tidak ada stance, tidak ada skor, tidak ada rekomendasi —
cuma arah kepemilikan yang dilaporkan institusi ke SEC.

Batas yang melekat pada sumbernya (13F lewat agregasi Yahoo), semuanya
dibawa eksplisit ke dalam kontrak ini alih-alih disembunyikan di balik satu
angka:

1. Cuma ~10 pemegang TERBESAR per saham yang tersedia, bukan seluruh
   institusi -- jadi setiap angka di sini adalah "arah pemegang terbesar",
   bukan net flow institusional sebenarnya.
2. Yahoo memakai `pctChange = 100.0` sebagai PENANDA "posisi baru" (tidak
   ada basis untuk menghitung persentase), bukan sebagai besaran +100%.
   Pemegang seperti itu dihitung terpisah di `new_position_count` dan
   DIKELUARKAN dari semua matematika persentase/nilai -- kalau ikut,
   peringkat teratas akan penuh saham yang seluruh pemegangnya kebetulan
   baru terbaca, bukan saham yang benar-benar dikumpulkan.
3. Nilai dolar dihitung dari `value_usd` yang dilaporkan SEKARANG, jadi ia
   memperkirakan "nilai posisi yang ditambah pada harga sekarang", bukan
   dolar transaksi saat pembelian terjadi.
4. 13F terlambat sampai 45 hari setelah tutup kuartal, dan pada satu waktu
   populasi bisa memuat dua kuartal berbeda (yang sudah lapor vs belum) --
   karena itu `report_date` dibawa per ticker dan dihitung per tanggal di
   `report_date_counts`, bukan dilebur jadi satu tanggal tunggal.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class TickerFlow:
    """Arah kepemilikan pemegang institusi terbesar untuk satu saham."""
    ticker: str
    sector: str | None = None
    report_date: str | None = None  # tanggal laporan yang paling banyak dipakai holder saham ini
    # Semua tanggal laporan berbeda yang muncul di antara pemegang saham ini.
    # Sering lebih dari satu: saat kuartal baru mulai masuk, sebagian institusi
    # sudah melapor dan sebagian belum, jadi satu saham bisa memuat dua kuartal
    # sekaligus. `report_date` di atas cuma yang terbanyak, bukan satu-satunya.
    report_dates: list[str] = field(default_factory=list)

    holders_total: int = 0  # jumlah pemegang yang dilaporkan (biasanya <= 10)
    holders_with_basis: int = 0  # yang punya pct_change bisa dipakai (bukan sentinel, bukan None)
    increased_count: int = 0
    reduced_count: int = 0
    unchanged_count: int = 0
    new_position_count: int = 0  # sentinel pctChange=100 -- posisi baru, besaran tidak diketahui

    # Perubahan lembar bersih dibagi lembar dasar (sebelum perubahan) di antara
    # pemegang ber-basis. None kalau tidak ada basis sama sekali.
    net_share_change_pct: float | None = None
    net_value_usd: float = 0.0  # perkiraan nilai posisi yang ditambah (negatif = dikurangi)
    gross_value_usd: float = 0.0  # total nilai yang berpindah, arah diabaikan
    top_holder_value_usd: float = 0.0  # total nilai yang dipegang 10 besar
    institutional_pct: float | None = None  # heldPercentInstitutions dari Yahoo (0-1)

    # Layak masuk papan peringkat: cukup pemegang ber-basis DAN cukup besar
    # untuk tidak didominasi noise saham mikro. Papan peringkat memakai ini;
    # tabel lengkap tetap memuat semuanya supaya penyaringnya kelihatan,
    # bukan diam-diam menghilangkan saham dari data.
    eligible_for_ranking: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SectorFlow:
    """Roll-up satu sektor. `net_ratio_pct` = net dibagi gross DI SEKTOR ITU
    SENDIRI -- bukan dibagi net terbesar lintas sektor. Tanpa normalisasi ini
    Technology selalu mendominasi batang cuma karena nilai yang berpindah di
    sana paling besar ($411 miliar), padahal arah bersihnya justru paling
    tipis; itu mengukur ukuran sektor, bukan arah dana."""
    sector: str
    ticker_count: int = 0
    tickers_net_up: int = 0
    tickers_net_down: int = 0
    net_value_usd: float = 0.0
    gross_value_usd: float = 0.0
    net_ratio_pct: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InstitutionalFlowReport:
    """Laporan tingkat populasi. Satu file per run pipeline."""
    method_version: str
    generated_at: str  # ISO datetime

    tickers_total: int = 0  # punya blok institutional_ownership
    tickers_with_holders: int = 0  # top_holders tidak kosong
    tickers_with_basis: int = 0  # minimal 1 pemegang ber-basis -> punya arah
    tickers_new_positions_only: int = 0  # semua pemegangnya sentinel -> tidak punya arah sama sekali

    net_value_usd: float = 0.0
    gross_value_usd: float = 0.0
    net_ratio_pct: float | None = None

    primary_report_date: str | None = None  # tanggal laporan terbanyak di populasi
    # tanggal -> jumlah ticker yang tanggal itu jadi tanggal DOMINAN-nya
    report_date_counts: dict[str, int] = field(default_factory=dict)
    # tanggal -> jumlah ticker yang punya MINIMAL SATU pemegang bertanggal itu.
    # Jauh lebih besar dari angka di atas saat kuartal baru mulai masuk, dan
    # itu justru pertanyaan yang biasa diajukan ("berapa saham yang laporan
    # kuartal barunya sudah mulai terbaca") -- dua angka yang berbeda, jadi
    # keduanya dibawa alih-alih memilih satu dan menyebutnya "jumlah ticker".
    report_date_presence_counts: dict[str, int] = field(default_factory=dict)

    sectors: list[SectorFlow] = field(default_factory=list)
    flows: list[TickerFlow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "method_version": self.method_version,
            "generated_at": self.generated_at,
            "tickers_total": self.tickers_total,
            "tickers_with_holders": self.tickers_with_holders,
            "tickers_with_basis": self.tickers_with_basis,
            "tickers_new_positions_only": self.tickers_new_positions_only,
            "net_value_usd": self.net_value_usd,
            "gross_value_usd": self.gross_value_usd,
            "net_ratio_pct": self.net_ratio_pct,
            "primary_report_date": self.primary_report_date,
            "report_date_counts": self.report_date_counts,
            "report_date_presence_counts": self.report_date_presence_counts,
            "sectors": [s.to_dict() for s in self.sectors],
            "flows": [f.to_dict() for f in self.flows],
        }
