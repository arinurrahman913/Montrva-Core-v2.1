"""CAGR revenue + padanan revenue untuk bank.

Dua field (`cagr_3y`, `cagr_5y`) sudah dideklarasikan di knowledge_contracts.py
sejak awal tapi tidak pernah di-assign siapa pun — 0 terisi dari 4.273 ticker,
dan memang mustahil selama Evidence cuma menyimpan 8 kuartal (3Y butuh 16, 5Y
butuh 24). Bank juga tidak memakai tag `Revenues` sama sekali, jadi seluruh
blok kuartalannya dulu terbuang.
"""

from datetime import date, timedelta

from montrva.layer2.contracts import QuarterlyFundamental
from montrva.layer2.knowledge_helpers import compute_financial_trends, _revenue_cagr, _ttm_revenue
from montrva.layer2.sources import sec_parser as sp

passed = 0


def check(label, actual, expected):
    global passed
    assert actual == expected, f"{label}: dapat {actual!r}, harusnya {expected!r}"
    passed += 1


def close(label, actual, expected, tol=1e-6):
    global passed
    assert actual is not None and abs(actual - expected) < tol, \
        f"{label}: dapat {actual!r}, harusnya ~{expected!r}"
    passed += 1


def quarters(revenues, start="2026-06-30"):
    """Deret kuartal terbaru-dulu, mundur ~91 hari tiap langkah."""
    d = date.fromisoformat(start)
    out = []
    for i, rev in enumerate(revenues):
        fd = (d - timedelta(days=91 * i)).isoformat()
        out.append(QuarterlyFundamental(period=fd, fiscal_date=fd, revenue=rev, net_income=None))
    return out


# --- TTM ---------------------------------------------------------------
q = quarters([10, 20, 30, 40, 50, 60])
close("TTM dari indeks 0", _ttm_revenue(q, 0), 100)
close("TTM dari indeks 2", _ttm_revenue(q, 2), 180)
check("TTM lewat ujung deret", _ttm_revenue(q, 4), None)

# satu kuartal bolong = bukan 12 bulan penuh, bukan "anggap saja"
q_bolong = quarters([10, None, 30, 40])
check("TTM dengan kuartal bolong", _ttm_revenue(q_bolong, 0), None)

# --- CAGR ---------------------------------------------------------------
# TTM kini 400 (4x100), TTM 3 tahun lalu 100 (4x25) -> 4x dalam 3 tahun
tumbuh = quarters([100] * 4 + [50] * 8 + [25] * 4)
close("CAGR 3Y 4x lipat", _revenue_cagr(tumbuh, 3), (4 ** (1 / 3) - 1) * 100, tol=1e-9)
check("CAGR 5Y kurang kuartal", _revenue_cagr(tumbuh, 5), None)

datar = quarters([100] * 24)
close("CAGR datar = 0%", _revenue_cagr(datar, 3), 0.0, tol=1e-9)
close("CAGR 5Y datar = 0%", _revenue_cagr(datar, 5), 0.0, tol=1e-9)

turun = quarters([50] * 12 + [100] * 12)  # TTM kini 200, TTM 3 tahun lalu 400
close("CAGR menyusut", _revenue_cagr(turun, 3), (0.5 ** (1 / 3) - 1) * 100, tol=1e-9)

# Basis nol/negatif: akar dari rasio negatif tidak punya arti -> None,
# bukan angka apa pun.
dari_nol = quarters([100] * 16)
for i in range(12, 16):
    dari_nol[i].revenue = 0
check("CAGR dari basis nol", _revenue_cagr(dari_nol, 3), None)

dari_negatif = quarters([100] * 16)
for i in range(12, 16):
    dari_negatif[i].revenue = -10
check("CAGR dari basis negatif", _revenue_cagr(dari_negatif, 3), None)

# --- Terpasang di compute_financial_trends ------------------------------
tren = compute_financial_trends(quarters([100] * 24))
check("cagr_3y masuk trends", round(tren["revenue_cagr_3y"], 6), 0.0)
check("cagr_5y masuk trends", round(tren["revenue_cagr_5y"], 6), 0.0)

# Deret 8 kuartal (bentuk data LAMA) tidak boleh melahirkan CAGR palsu
tren8 = compute_financial_trends(quarters([100] * 8))
check("8 kuartal -> tanpa cagr_3y", "revenue_cagr_3y" in tren8, False)
check("8 kuartal -> tanpa cagr_5y", "revenue_cagr_5y" in tren8, False)
check("8 kuartal -> YoY tetap ada", round(tren8["revenue_yoy_q4"], 6), 0.0)


# --- Revenue lembaga keuangan -------------------------------------------
def pt(end, val, form="10-Q", filed="2026-08-01"):
    start = (date.fromisoformat(end) - timedelta(days=91)).isoformat()
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


def facts(**tags):
    return {"facts": {"us-gaap": {t: {"units": {"USD": pts}} for t, pts in tags.items()}}}


DATES = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]

# Jalur 1: tag total pendapatan bank dipakai apa adanya
bank_total = facts(RevenuesNetOfInterestExpense=[pt(d, 500) for d in DATES])
series, basis = sp._financial_revenue_series(bank_total)
check("bank: basis net revenue", basis, "financial_net_revenue")
check("bank: nilai", series["2026-06-30"], 500)

# Jalur 2: dijumlahkan dari pendapatan bunga bersih + non-bunga
bank_komposit = facts(
    InterestIncomeExpenseNet=[pt(d, 100) for d in DATES],
    NoninterestIncome=[pt(d, 20) for d in DATES],
)
series, basis = sp._financial_revenue_series(bank_komposit)
check("bank: basis komposit", basis, "financial_composite")
check("bank: jumlah dua komponen", series["2026-06-30"], 120)
check("bank: jumlah periode", len(series), 4)

# Periode yang cuma punya satu komponen DIBUANG -- kalau tidak, kuartal itu
# akan terlihat seperti pendapatan yang anjlok separuh.
timpang = facts(
    InterestIncomeExpenseNet=[pt(d, 100) for d in DATES + ["2025-06-30"]],
    NoninterestIncome=[pt(d, 20) for d in DATES],
)
series, _ = sp._financial_revenue_series(timpang)
check("bank: periode timpang dibuang", "2025-06-30" in series, False)
check("bank: sisanya utuh", len(series), 4)

# Irisan terlalu tipis (< 4 kuartal) -> jangan dipakai sama sekali
tipis = facts(
    InterestIncomeExpenseNet=[pt(d, 100) for d in DATES[:3]],
    NoninterestIncome=[pt(d, 20) for d in DATES[:3]],
)
check("bank: irisan tipis ditolak", sp._financial_revenue_series(tipis), ({}, None))

# Emiten biasa TIDAK boleh menyentuh tier keuangan, walau pendapatan bunganya
# lebih baru sekuartal -- itu alasan tier-nya dipisah, bukan digabung.
biasa = facts(
    Revenues=[pt(d, 1000) for d in DATES[1:]],
    InterestIncomeExpenseNet=[pt(d, 5) for d in DATES],
    NoninterestIncome=[pt(d, 1) for d in DATES],
)
std, _ = sp._extract_quarterly_series(biasa, sp.REVENUE_TAGS)
check("non-bank: tetap pakai tag standar", std["2026-03-31"], 1000)
check("non-bank: tier keuangan tak dipanggil", max(std), "2026-03-31")

# --- Tag standar yang cuma mengukur sepotong pendapatan bank -------------
# Bentuk FULT: pendapatan fee (79,3 jt) dilaporkan di bawah tag revenue
# standar, sementara pendapatan sesungguhnya 363,6 jt (bunga bersih +
# non-bunga). Tag standarnya "berhasil", jadi dulu net margin-nya keluar
# 129% — laba bersih dibagi pendapatan fee saja.
fult = facts(
    RevenueFromContractWithCustomerIncludingAssessedTax=[pt(d, 79) for d in DATES],
    InterestIncomeExpenseNet=[pt(d, 284) for d in DATES],
    NoninterestIncome=[pt(d, 79) for d in DATES],
)
std_s, _ = sp._extract_quarterly_series(fult, sp.REVENUE_TAGS)
fin_s, fin_basis = sp._financial_revenue_series(fult)
check("FULT: tag standar cuma sepotong", sp._is_subset_measure(std_s, fin_s), True)
check("FULT: pendapatan penuh", fin_s["2026-06-30"], 363)

# Sebaliknya: emiten biasa dengan pendapatan bunga kecil TIDAK boleh diganti
kecil = {"2026-06-30": 5, "2026-03-31": 5}
besar = {"2026-06-30": 1000, "2026-03-31": 1000}
check("non-bank: bukan sepotong", sp._is_subset_measure(besar, kecil), False)
check("tanpa periode beririsan -> standar menang",
      sp._is_subset_measure({"2026-06-30": 1}, {"2020-06-30": 999}), False)

print(f"OK — {passed} assert lolos")
