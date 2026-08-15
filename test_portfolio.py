"""Uji matematika uang portofolio (montrva/personal/portfolio.py).

Berbasis assert, bukan cetak-lalu-dibaca-mata: ini satu-satunya tempat di
codebase yang menghitung UANG RIIL pengguna (basis biaya, realized P/L), dan
kesalahannya tidak akan terlihat dari dashboard — angka yang salah tetap
terlihat seperti angka.

Jalankan: python test_portfolio.py
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import date
from pathlib import Path

from montrva.personal import portfolio as pf
from montrva.personal.personal_reasoning import compute_position, load_holdings

TODAY = date(2026, 8, 14)
_passed = 0


def check(label: str, actual, expected, tol: float = 1e-6):
    global _passed
    ok = (abs(actual - expected) <= tol) if isinstance(expected, (int, float)) and not isinstance(expected, bool) else actual == expected
    if not ok:
        raise AssertionError(f"{label}: dapat {actual!r}, harusnya {expected!r}")
    _passed += 1
    print(f"  ok  {label}: {actual!r}")


def tx(ticker, side, d, price, qty, fee=0.0, note="", tid=None, created="2026-01-01T00:00:00"):
    return {"id": tid or f"{ticker}{side}{d}{qty}", "ticker": ticker, "side": side, "date": d,
            "price": price, "quantity": qty, "fee": fee, "note": note, "created_at": created}


def section(title):
    print(f"\n{title}")


# --------------------------------------------------------------------------
section("1. Rata-rata biaya beli bertingkat — fee masuk basis biaya")
book = [
    tx("AAPL", "beli", "2026-03-26", 200.00, 10, fee=1.50),
    tx("AAPL", "beli", "2026-05-02", 220.00, 10, fee=1.50),
]
pos, errs = pf.replay(book)
check("tidak ada error", errs, [])
check("lembar", pos["AAPL"]["quantity"], 20)
# (200*10 + 1.50) + (220*10 + 1.50) = 4203.00 -> /20
check("total_cost termasuk fee", pos["AAPL"]["total_cost"], 4203.00)
check("avg_cost", pos["AAPL"]["avg_cost"], 210.15)
check("buy_date = pembelian yang membuka posisi", pos["AAPL"]["buy_date"], "2026-03-26")

# --------------------------------------------------------------------------
section("2. Jual sebagian — realized bersih fee, avg_cost TIDAK berubah")
book.append(tx("AAPL", "jual", "2026-06-10", 240.00, 8, fee=1.00))
pos, errs = pf.replay(book)
check("tidak ada error", errs, [])
check("sisa lembar", pos["AAPL"]["quantity"], 12)
# (240 - 210.15) * 8 - 1.00 = 237.80
check("realized_pnl", pos["AAPL"]["realized_pnl"], 237.80)
check("avg_cost tetap", pos["AAPL"]["avg_cost"], 210.15)
check("total_cost sisa", pos["AAPL"]["total_cost"], 210.15 * 12)
check("buy_date tidak bergeser", pos["AAPL"]["buy_date"], "2026-03-26")

# --------------------------------------------------------------------------
section("3. Jual habis — posisi tutup, realized tetap tercatat")
book.append(tx("AAPL", "jual", "2026-07-01", 250.00, 12, fee=1.00))
pos, errs = pf.replay(book)
check("tidak ada error", errs, [])
check("lembar nol", pos["AAPL"]["quantity"], 0.0)
check("total_cost dinolkan", pos["AAPL"]["total_cost"], 0.0)
check("avg_cost jadi None", pos["AAPL"]["avg_cost"], None)
check("buy_date dilepas", pos["AAPL"]["buy_date"], None)
# 237.80 + ((250 - 210.15) * 12 - 1.00) = 237.80 + 477.20 = 715.00
check("realized_pnl kumulatif", pos["AAPL"]["realized_pnl"], 715.00)

section("3b. Posisi tertutup TIDAK boleh masuk holdings.json")
payload = pf.holdings_payload(book)
check("holdings kosong", payload["holdings"], [])
check("tapi tetap muncul di derive_positions", len(pf.derive_positions(book)), 1)
check("dan ditandai tertutup", pf.derive_positions(book)[0]["is_open"], False)

# --------------------------------------------------------------------------
section("4. Beli lagi sesudah tutup — basis biaya & buy_date mulai dari nol")
book.append(tx("AAPL", "beli", "2026-08-01", 190.00, 5))
pos, _ = pf.replay(book)
check("avg_cost bersih dari posisi lama", pos["AAPL"]["avg_cost"], 190.00)
check("buy_date direset", pos["AAPL"]["buy_date"], "2026-08-01")
check("realized lama tidak hilang", pos["AAPL"]["realized_pnl"], 715.00)

# --------------------------------------------------------------------------
section("5. Jual melebihi kepemilikan ditolak")
bad = pf._new_transaction("AAPL", "jual", "2026-08-13", 200.0, 99)
errs = pf.validate_transaction(bad, book, today=TODAY)
check("ada satu error", len(errs), 1)
check("menyebut kepemilikan saat itu", "melebihi kepemilikan" in errs[0], True)

section("5b. Jual BERTANGGAL MUNDUR ke sebelum posisi ada — juga ditolak")
# Posisi hari ini 5 lembar, jadi uji "lawan posisi akhir" akan MELOLOSKAN ini.
backdated = pf._new_transaction("AAPL", "jual", "2026-01-05", 200.0, 3)
errs = pf.validate_transaction(backdated, book, today=TODAY)
check("ditolak walau posisi hari ini cukup", len(errs), 1)
check("menyebut tanggal transaksinya", "2026-01-05" in errs[0], True)

section("5c. Beli lalu jual di HARI yang sama — sah")
same_day = [
    tx("MU", "beli", "2026-08-10", 90.0, 10, created="2026-08-10T01:00:00"),
    tx("MU", "jual", "2026-08-10", 95.0, 10, created="2026-08-10T02:00:00"),
]
pos, errs = pf.replay(same_day)
check("tidak ada error (urutan pakai created_at)", errs, [])
check("realized", pos["MU"]["realized_pnl"], 50.0)

section("5d. Bentuk salah tidak memicu error turunan")
errs = pf.validate_transaction(
    {"ticker": "", "side": "hibah", "date": "kemarin", "price": -1, "quantity": 0, "fee": -5},
    book, today=TODAY,
)
check("semua kesalahan bentuk terlaporkan", len(errs), 6)
check("tanpa error replay ikut nempel", any("melebihi" in e for e in errs), False)

section("5e. Tanggal masa depan ditolak")
future = pf._new_transaction("AAPL", "beli", "2026-12-31", 200.0, 1)
errs = pf.validate_transaction(future, book, today=TODAY)
check("ditolak", any("masa depan" in e for e in errs), True)

# --------------------------------------------------------------------------
section("6. Pecahan lembar — jual habis tidak tertolak sisa pembulatan")
frac = [
    tx("VOO", "beli", "2026-01-10", 500.0, 0.1),
    tx("VOO", "beli", "2026-01-11", 510.0, 0.2),
    tx("VOO", "jual", "2026-02-01", 520.0, 0.3),
]
pos, errs = pf.replay(frac)
check("tidak ada error walau 0.1+0.2 != 0.3", errs, [])
check("posisi benar-benar tutup", pos["VOO"]["quantity"], 0.0)

# --------------------------------------------------------------------------
section("7. Ringkasan — posisi tanpa harga bukan berarti nilainya nol")
positions = pf.derive_positions([
    tx("AAPL", "beli", "2026-03-26", 200.0, 10),   # modal 2000, harga 250 -> 2500
    tx("XYZ", "beli", "2026-03-26", 100.0, 10),    # modal 1000, harga tidak diketahui
])
summary = pf.summarize(positions, {"AAPL": 250.0, "XYZ": None})
check("modal mencakup keduanya", summary["total_cost"], 3000.0)
check("nilai pasar cuma yang berharga", summary["market_value"], 2500.0)
check("unrealized dibanding modal yang berharga saja", summary["unrealized_pnl"], 500.0)
check("persen tidak terseret modal tak berharga", summary["unrealized_pct"], 25.0)
check("yang tak berharga dilaporkan", summary["positions_without_price"], ["XYZ"])

section("7b. Bobot berbasis nilai pasar, ticker tanpa harga dapat None")
weights = pf.position_weights(positions, {"AAPL": 250.0, "XYZ": None})
check("AAPL 100% dari yang bisa dinilai", weights["AAPL"], 100.0)
check("XYZ None, bukan 0", weights["XYZ"], None)

# --------------------------------------------------------------------------
section("8. Tulis ke disk lalu dibaca ulang oleh KODE PIPELINE yang asli")
tmp = Path(tempfile.mkdtemp(prefix="montrva_pf_"))
try:
    live = [
        tx("NVDA", "beli", "2026-06-01", 118.40, 24, fee=2.00),
        tx("SMCI", "beli", "2026-06-14", 41.20, 18, fee=1.00),
        tx("SMCI", "jual", "2026-08-02", 58.90, 18, fee=1.00),
    ]
    pf.save_portfolio(tmp, live)
    check("transactions.json ditulis", (tmp / "transactions.json").exists(), True)

    holdings = load_holdings(tmp / "holdings.json")
    check("cuma NVDA yang terbaca sebagai holding", sorted(holdings), ["NVDA"])

    status, since, unreal = compute_position("NVDA", holdings, 184.72)
    check("compute_position -> holding", status, "holding")
    check("holding_since", since, "2026-06-01")
    # avg_cost (118.40*24 + 2)/24 = 118.4833..; (184.72 - avg)/avg * 100
    check("unrealized %", unreal, (184.72 - 118.4833333333) / 118.4833333333 * 100, tol=1e-6)

    status_closed, _, _ = compute_position("SMCI", holdings, 41.60)
    check("SMCI yang sudah dijual -> no_holding", status_closed, "no_holding")

    section("8b. Hapus transaksi yang membuat buku tidak sah dibatalkan")
    removed, errs = pf.delete_transaction(tmp, live[0]["id"])
    check("boleh hapus (NVDA tidak punya jual sesudahnya)", errs, [])
    check("yang terhapus dikembalikan", removed["ticker"], "NVDA")
    pf.save_portfolio(tmp, live)  # kembalikan
    removed, errs = pf.delete_transaction(tmp, live[1]["id"])  # buang BELI SMCI
    check("ditolak", removed, None)
    check("alasannya disebut", any("melebihi kepemilikan" in e for e in errs), True)
    check("buku di disk tidak berubah", len(pf.load_transactions(tmp / "transactions.json")), 3)

    section("9. holdings.json tulisan tangan diimpor, bukan ditimpa")
    legacy_dir = Path(tempfile.mkdtemp(prefix="montrva_legacy_"))
    (legacy_dir / "holdings.json").write_text(
        '{"holdings": [{"ticker": "aal", "avg_cost": 12.5, "quantity": 100, "buy_date": "2025-11-03"}]}',
        encoding="utf-8",
    )
    try:
        book_imported = pf.ensure_book(legacy_dir)
        check("satu transaksi sintetis", len(book_imported), 1)
        check("ticker dinormalkan", book_imported[0]["ticker"], "AAL")
        check("sisi beli", book_imported[0]["side"], "beli")
        restored = load_holdings(legacy_dir / "holdings.json")
        check("posisi lama utuh sesudah turunan ditulis ulang", restored["AAL"]["quantity"], 100)
        check("avg_cost lama utuh", restored["AAL"]["avg_cost"], 12.5)
        check("buy_date lama utuh", restored["AAL"]["buy_date"], "2025-11-03")
    finally:
        shutil.rmtree(legacy_dir, ignore_errors=True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
section("10. Input nominal uang -> lembar (jalur yang dipakai halaman Portofolio)")
check("pembagian yang kebetulan tepat tetap tepat", pf.shares_from_amount(2413.80, 201.15), 12.0)

# Kasus NYATA, dicari dengan menyapu 400 ribu kombinasi (n lembar x harga 2
# desimal, nominal dibulatkan ke sen seperti yang benar-benar diketik orang).
# Kejadiannya jarang — sekitar 1 dari 80 ribu — tapi hasilnya permanen: angka
# itu masuk buku, ikut avg_cost, dan tampil di tabel selamanya. Dua arah
# dipakai supaya penguncinya terbukti bekerja ke atas DAN ke bawah.
check("881 lembar (mentahnya 881.0000000000001)", pf.shares_from_amount(30156.63, 34.23), 881.0)
check("  — memang meleset tanpa pengunci", 30156.63 / 34.23 != 881.0, True)
check("2340 lembar (mentahnya 2339.9999999999995)", pf.shares_from_amount(650473.2, 277.98), 2340.0)
check("  — memang meleset tanpa pengunci", 650473.2 / 277.98 != 2340.0, True)
check("nominal besar tetap terkunci", pf.shares_from_amount(1000.0, 0.5), 2000.0)
check("pembelian pecahan tetap pecahan", pf.shares_from_amount(100.0, 201.15), round(100.0 / 201.15, 8))

t = pf._new_transaction("AAPL", "beli", "2026-08-01", 201.15, amount=2413.80)
check("lembar diturunkan", t["quantity"], 12.0)
check("nominal yang diketik ikut disimpan", t["amount"], 2413.80)
check("fee default nol", t["fee"], 0.0)

section("10b. Nominal terlalu kecil untuk satu pecahan lembar pun")
tiny = pf._new_transaction("AAPL", "beli", "2026-08-01", 201.15, amount=0)
errs = pf.validate_transaction(tiny, [], today=TODAY)
check("ditolak", any("lebih besar dari 0" in e for e in errs), True)

section("10c. Pesan galat menyebut field yang ADA di layar")
errs = pf.validate_transaction(
    pf._new_transaction("AAPL", "beli", "2026-08-01", 201.15, amount="banyak"), [], today=TODAY,
)
check("bicara 'Nominal', bukan 'Jumlah lembar'", errs, ["Nominal harus angka."])
errs = pf.validate_transaction(
    pf._new_transaction("AAPL", "beli", "2026-08-01", 201.15, quantity="banyak"), [], today=TODAY,
)
check("jalur lembar tetap bicara 'Jumlah lembar'", errs, ["Jumlah lembar harus angka."])

section("10d. Jual seluruh posisi lewat nominal -> tutup bersih, tanpa sisa")
book_amt = [pf._new_transaction("AAPL", "beli", "2026-06-01", 201.15, amount=2413.80)]
held = pf.replay(book_amt)[0]["AAPL"]["quantity"]
# Persis yang dilakukan tombol "Jual semua": nominal = lembar x harga.
book_amt.append(pf._new_transaction("AAPL", "jual", "2026-08-01", 250.0, amount=round(held * 250.0, 2)))
pos, errs = pf.replay(book_amt)
check("tidak ada error", errs, [])
check("posisi benar-benar tutup", pos["AAPL"]["quantity"], 0.0)
check("tidak ikut ditulis ke holdings.json", pf.holdings_payload(book_amt)["holdings"], [])
check("realized", pos["AAPL"]["realized_pnl"], (250.0 - 201.15) * 12)

print(f"\n{_passed} pemeriksaan lolos.")
