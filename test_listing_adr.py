"""Uji gerbang universe: ADR masuk, turunannya tidak.

Ada karena bug yang ditemukan audit 15 Agu 2026 TIDAK TERLIHAT dari mana pun —
274 ADR dibuang di `cheap_filter`, tahap yang tidak membuat record, jadi mereka
tidak muncul di `passed` MAUPUN di `hard_excluded` beserta alasannya. Satu-
satunya cara menemukannya adalah membandingkan universe mentah dengan keluaran
screening. Tes ini membuat perbandingan itu otomatis.

Nama sekuritas di bawah SALINAN PERSIS dari berkas listing NASDAQ Trader
(bukan karangan): kata kunci diuji terhadap prosa yang benar-benar dipakai
bursa, karena persis prosa itulah yang dulu salah tertangkap.

Jalankan: python test_listing_adr.py
"""
from __future__ import annotations

from montrva.layer2.sources.listing import ListingRow, is_adr, is_common_stock

_passed = 0


def check(label: str, actual, expected):
    global _passed
    if actual != expected:
        raise AssertionError(f"{label}: dapat {actual!r}, harusnya {expected!r}")
    _passed += 1
    print(f"  ok  {label}")


def row(symbol: str, name: str, is_etf: bool = False, is_test: bool = False) -> ListingRow:
    return ListingRow(symbol=symbol, security_name=name, exchange="NASDAQ",
                      is_etf=is_etf, is_test_issue=is_test)


# --- ADR saham biasa: HARUS masuk universe ---------------------------------
DITERIMA = [
    ("ARGX", "argenx SE - American Depositary Shares"),
    ("ABEV", "Ambev S.A. American Depositary Shares (Each representing 1 Common Share)"),
    # Prosa "the right to receive" dulu tertangkap kata kunci " right".
    ("AMX", "America Movil, S.A.B. de C.V. American Depositary Shares (each representing "
            "the right to receive twenty (20) Series L Shares)"),
    ("WDH", "Waterdrop Inc. American Depositary Shares (each representing the right to "
            "receive 10 Class A Ordinary Shares)"),
    # Prosa "one unit" / "10 Units" dulu tertangkap kata kunci " unit".
    ("BSBR", "Banco Santander Brasil SA American Depositary Shares, each representing one unit"),
    ("KOF", "Coca Cola Femsa S.A.B. de C.V.  American Depositary Shares, each representing "
            "10 Units (each Unit consists of 3 Series B Shares)"),
    ("CIG", "Comp En De Mn Cemig ADS American Depositary Receipts"),
]

# --- Turunan/kelas lain dari ADR: HARUS tetap ditolak ----------------------
DITOLAK = [
    ("IQMXW", "IQM Quantum Computers Oyj - Warrants to purchase American Depositary Shares",
     "warrant atas ADS, bukan ADS-nya"),
    ("ITUB", "Itau Unibanco Banco Holding SA American Depositary Shares (Each repstg 500 "
             "Preferred shares)", "ADS mewakili saham preferen"),
    ("PBR.A", "Petroleo Brasileiro S.A. Petrobras American Depositary Shares representing "
              "Preferred Shares", "ADS preferen (dan simbol bertitik)"),
]

print("1. ADR saham biasa masuk universe")
for sym, name in DITERIMA:
    r = row(sym, name)
    check(f"{sym} dikenali ADR", is_adr(name), True)
    check(f"{sym} lolos cheap_filter", is_common_stock(r), True)

print("\n2. Turunan ADR tetap ditolak")
for sym, name, alasan in DITOLAK:
    check(f"{sym} ditolak ({alasan})", is_common_stock(row(sym, name)), False)

print("\n3. Non-ADR tidak terpengaruh sama sekali")
NON_ADR = [
    ("AAPL", "Apple Inc. - Common Stock", True),
    ("BRKB", "Berkshire Hathaway Inc. Class B", True),
    ("ACGLN", "Arch Capital Group Ltd. - Depositary Shares, each Representing a 1/1,000th "
              "Interest in a 4.550% Preferred Share", False),
    ("XYZW", "Some Corp - Warrant", False),
    ("XYZU", "Some Corp - Units", False),
    ("XYZR", "Some Corp - Rights", False),
    ("XYZN", "Some Corp - 5.00% Notes due 2030", False),
]
for sym, name, expected in NON_ADR:
    check(f"{sym} -> {expected}", is_common_stock(row(sym, name)), expected)

print("\n4. ETF & test issue tetap dibuang walau namanya ADR")
check("ETF ber-ADR ditolak", is_common_stock(row("ADRE", "Invesco BLDRS Emerging Markets "
                                                 "50 ADR Index Fund - American Depositary Shares",
                                                 is_etf=True)), False)
check("test issue ditolak", is_common_stock(row("ZZZT", "argenx SE - American Depositary Shares",
                                                is_test=True)), False)

print(f"\n{_passed} pemeriksaan lolos.")
