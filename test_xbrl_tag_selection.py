"""Pemilihan tag XBRL di sec_parser: yang MUTAKHIR menang, bukan yang pertama.

Latar: `_extract_quarterly_series` dulu mengembalikan tag pertama yang berisi
apa pun. Emiten berganti tag saat standar pelaporan berubah dan tag lamanya
tetap tertinggal di companyfacts, jadi NVDA memakai deret yang berhenti
2020-01-26 sementara `Revenues` punya data sampai 2026-04-26. Terukur di
artefak produksi: 304 dari 4.273 ticker punya kuartal terbaru <= 2024, dan
1.302 nol kuartal karena blok kuartalan dibuang utuh saat tag revenue tak
ketemu (kebanyakan bank — mereka tidak melaporkan `Revenues`).

Bentuk payload di bawah menyalin struktur companyfacts SEC apa adanya:
facts -> us-gaap -> <tag> -> units -> USD -> [{start,end,val,form,filed}].
"""

from datetime import date, timedelta

from montrva.layer2.sources import sec_parser as sp

REV1 = "RevenueFromContractWithCustomerExcludingAssessedTax"  # prioritas 0
REV2 = "RevenueFromContractWithCustomerIncludingAssessedTax"  # prioritas 1
REV3 = "Revenues"                                             # prioritas 2

passed = 0


def check(label, actual, expected):
    global passed
    assert actual == expected, f"{label}: dapat {actual!r}, harusnya {expected!r}"
    passed += 1


def q(end, val, *, start=None, form="10-Q", filed="2026-08-01"):
    """Satu datapoint kuartalan (durasi 91 hari supaya lolos filter 75-100)."""
    if start is None:
        start = (date.fromisoformat(end) - timedelta(days=91)).isoformat()
    return {"start": start, "end": end, "val": val, "form": form, "filed": filed}


def facts(**tags):
    return {"facts": {"us-gaap": {t: {"units": {"USD": pts}} for t, pts in tags.items()}}}


# --- 1. Bentuk NVDA: tag prioritas tinggi berhenti bertahun lalu -------------
nvda = facts(
    **{
        REV1: [q("2019-10-27", 3014), q("2020-01-26", 3105)],
        REV3: [q(d, i) for i, d in enumerate(
            ["2019-10-27", "2020-01-26", "2025-10-26", "2026-01-25", "2026-04-26"])],
    }
)
series, tag = sp._extract_quarterly_series(nvda, sp.REVENUE_TAGS)
check("NVDA tag terpilih", tag, REV3)
check("NVDA kuartal terbaru", max(series), "2026-04-26")

# --- 2. Berakhir di tanggal sama -> deret yang lebih dalam menang ------------
sama_tanggal = facts(
    **{
        REV1: [q("2026-06-30", 10)],
        REV3: [q(d, 1) for d in ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]],
    }
)
series, tag = sp._extract_quarterly_series(sama_tanggal, sp.REVENUE_TAGS)
check("tanggal sama -> lebih dalam", tag, REV3)
check("tanggal sama -> jumlah titik", len(series), 4)

# --- 3. Tanggal & kedalaman sama -> urutan prioritas daftar ------------------
imbang = facts(**{REV1: [q("2026-06-30", 5)], REV2: [q("2026-06-30", 7)]})
_, tag = sp._extract_quarterly_series(imbang, sp.REVENUE_TAGS)
check("imbang -> prioritas daftar", tag, REV1)

# --- 4. Satu titik mutakhir vs deret panjang usang --------------------------
# Keputusan sadar: yang mutakhir menang walau YoY jadi hilang. Deret 2016 yang
# dipajang sebagai "kini" persis bug yang diperbaiki di sini; YoY yang hilang
# jujur, YoY dari 2016 tidak. Diukur pada MS/MBOT/BNY saat aturan sebaliknya
# dicoba — ketiganya mundur ke 2015-2016.
dangkal_vs_usang = facts(
    **{
        REV1: [q("2026-06-30", 99)],
        REV3: [q(f"{y}-06-30", y) for y in range(2014, 2018)],
    }
)
series, tag = sp._extract_quarterly_series(dangkal_vs_usang, sp.REVENUE_TAGS)
check("mutakhir menang atas kedalaman", tag, REV1)
check("mutakhir menang -> tanggal", max(series), "2026-06-30")

# --- 5. Filter durasi & jenis form masih berlaku ----------------------------
kotor = facts(
    **{
        REV1: [
            q("2026-06-30", 1, start="2025-07-01"),          # setahun penuh -> buang
            q("2026-03-31", 2, form="8-K"),                  # bukan 10-Q/10-K -> buang
            {"end": "2026-06-30", "val": 3, "form": "10-Q", "filed": "2026-08-01"},  # tanpa start
            q("2025-12-31", 4),                              # sah
        ],
    }
)
series, tag = sp._extract_quarterly_series(kotor, sp.REVENUE_TAGS)
check("filter durasi/form", sorted(series), ["2025-12-31"])

# --- 6. Revisi belakangan menimpa angka lama untuk periode yang sama --------
revisi = facts(
    **{
        REV1: [
            q("2026-06-30", 100, filed="2026-08-01"),
            q("2026-06-30", 111, filed="2026-11-01"),  # restatement
        ],
    }
)
series, _ = sp._extract_quarterly_series(revisi, sp.REVENUE_TAGS)
check("revisi terbaru menang", series["2026-06-30"], 111)

# --- 7. Tanpa revenue, net income tetap jadi tulang punggung (pola bank) ----
bank_facts = facts(
    **{
        "NetIncomeLoss": [q(d, 5) for d in
                          ["2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]],
        "NetCashProvidedByUsedInOperatingActivities": [q("2026-06-30", 7)],
    }
)
sp_cik, sp_facts = sp.get_cik_from_ticker, sp._fetch_company_facts
sp.get_cik_from_ticker = lambda t: "0000000001"
sp._fetch_company_facts = lambda cik: bank_facts
try:
    out = sp.fetch_quarterly_financials("BANKX")
finally:
    sp.get_cik_from_ticker, sp._fetch_company_facts = sp_cik, sp_facts

check("bank: blok tidak dibuang", out is not None, True)
check("bank: jumlah periode", len(out["periods"]), 4)
check("bank: revenue kosong", out["periods"][0]["revenue"], None)
check("bank: net income terisi", out["periods"][0]["net_income"], 5)
check("bank: revenue_available", out["revenue_available"], False)
check("bank: status limited", out["status"], "limited")
check("bank: latest_fiscal_date", out["latest_fiscal_date"], "2026-06-30")
check("bank: revenue_tag", out["revenue_tag"], None)

# Margin tetap None tanpa revenue -- tidak boleh mengarang dari net income saja
metrics = sp.extract_quarterly_metrics(out)
check("bank: nol margin terhitung",
      [k for k in metrics if "margin" in k], [])

# --- 8. Instant series (shares outstanding) pakai aturan yang sama ----------
def s(end, val, filed="2026-08-01"):
    return {"end": end, "val": val, "form": "10-Q", "filed": filed}


instant = {"facts": {"us-gaap": {
    "CommonStockSharesOutstanding": {"units": {"shares": [s("2019-06-30", 100), s("2019-09-30", 101)]}},
    "CommonStockSharesIssued": {"units": {"shares": [s("2026-03-31", 200), s("2026-06-30", 202)]}},
}}}
series = sp._extract_instant_series(instant, sp.SHARES_OUTSTANDING_TAGS)
check("instant: tag mutakhir menang", max(series), "2026-06-30")

# --- 9. Tanpa data sama sekali ---------------------------------------------
kosong_series, kosong_tag = sp._extract_quarterly_series(facts(), sp.REVENUE_TAGS)
check("kosong: series", kosong_series, {})
check("kosong: tag", kosong_tag, None)

print(f"OK — {passed} assert lolos")
