"""Portofolio pribadi — buku transaksi (sumber kebenaran) + posisi turunan.

Kenapa buku transaksi dan bukan snapshot posisi: sampai sekarang holdings.json
diisi tangan dengan {ticker, avg_cost, buy_date, quantity} — bentuk yang tidak
bisa menjawab "dari mana avg_cost itu", tidak punya tempat untuk jual sebagian,
dan tidak punya tempat sama sekali untuk realized P/L. Yang dicatat pengguna di
sini adalah PERISTIWA (beli/jual), dan holdings.json JADI TURUNAN yang dihitung
ulang setiap kali buku berubah.

Konsekuensi yang disengaja: `load_holdings()` dan `compute_position()` di
personal_reasoning.py TIDAK BERUBAH SATU BARIS PUN — pipeline tetap membaca
holdings.json persis seperti sebelumnya. Yang bertambah cuma siapa yang
menulisnya. Modul ini tidak mengimpor apa pun dari montrva.layer1/layer2
(arah impor satu arah tetap, lihat personal_contracts.py).

METODE BIAYA: AVERAGE COST, bukan FIFO. Dipilih karena field `avg_cost` yang
sudah dipakai compute_position memang bentuk itu, dan karena broker menampilkan
harga rata-rata. Bedanya cuma di angka realized P/L saat jual sebagian; kalau
suatu saat FIFO benar-benar dibutuhkan, buku transaksinya sudah menyimpan cukup
data untuk menghitung ulang tanpa kehilangan apa pun — itu justru alasan
menyimpan peristiwa, bukan snapshot.

FEE masuk basis biaya saat beli (menaikkan avg_cost) dan mengurangi hasil saat
jual. Jadi `realized_pnl` di sini sudah bersih fee kedua sisi.

SATU ATURAN YANG TIDAK BOLEH DILANGGAR: posisi yang sudah dijual habis TIDAK
IKUT ditulis ke holdings.json. `compute_position()` memutuskan "holding"
semata-mata dari ADA/TIDAKNYA kunci ticker di sana (`if not h: return
"no_holding"`), bukan dari quantity-nya — menulis posisi tertutup dengan
quantity 0 akan menyalakan seluruh kolom ACTION_TABLE["holding"] untuk saham
yang sudah tidak dipegang, dan tidak ada satu pun pemeriksa di jalur itu yang
akan menangkapnya.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from ..json_safe import dumps_safe, write_text_atomic

TransactionSide = Literal["beli", "jual"]

SIDES: tuple[str, ...] = ("beli", "jual")

# Toleransi pembanding jumlah lembar. Bukan hiasan: 0.1+0.2 != 0.3 juga berlaku
# untuk fractional share, dan tanpa epsilon "jual semua" bisa ditolak sebagai
# "melebihi kepemilikan" karena sisa 1e-16.
_EPS = 1e-9

# Ticker Yahoo: huruf/angka, titik & strip (BRK.B, RDS-A). Sengaja longgar —
# validasi ini menolak yang jelas bukan ticker (spasi, path, kalimat), BUKAN
# memutuskan ticker itu ada atau tidak. Yang terakhir dijawab data, di lapisan
# di atas (lihat `outside_universe` di rute).
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")

TRANSACTIONS_FILENAME = "transactions.json"
HOLDINGS_FILENAME = "holdings.json"


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def load_transactions(path: str | Path) -> list[dict]:
    """Baca transactions.json -> daftar transaksi apa adanya (BELUM diurut).

    File tidak wajib ada (default: buku kosong), sejajar dengan load_holdings."""
    import json

    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("transactions", []))


def _load_raw_holdings(path: str | Path) -> list[dict]:
    import json

    p = Path(path)
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("holdings", []))


def import_legacy_holdings(holdings: list[dict]) -> list[dict]:
    """Ubah entri holdings.json tulisan tangan jadi transaksi "beli" sintetis.

    Dipanggil SEKALI, saat holdings.json sudah berisi sesuatu tapi belum ada
    transactions.json. Alternatifnya — membiarkan penulisan pertama menimpa
    holdings.json dengan hasil turunan buku yang masih kosong — akan menghapus
    portofolio riil pengguna tanpa jejak; satu-satunya salinannya ada di file
    yang gitignored, jadi tidak ada git yang bisa mengembalikannya.

    Entri tanpa quantity dilewati dengan sengaja: buku ini menyimpan jumlah
    lembar, dan menebak angka itu (mis. 1) akan mengarang basis biaya."""
    out: list[dict] = []
    for h in holdings:
        ticker = str(h.get("ticker", "")).upper().strip()
        qty = h.get("quantity")
        price = h.get("avg_cost")
        if not ticker or not qty or not price:
            continue
        out.append(_new_transaction(
            ticker=ticker,
            side="beli",
            tx_date=str(h.get("buy_date") or date.today().isoformat()),
            price=float(price),
            quantity=float(qty),
            fee=0.0,
            note="Diimpor dari holdings.json lama (ditulis tangan, sebelum ada buku transaksi).",
        ))
    return out


def save_portfolio(personal_dir: str | Path, transactions: list[dict]) -> dict:
    """Tulis buku + turunkan holdings.json. Mengembalikan payload holdings.

    Dua berkas, dua penulisan atomik terpisah — bukan satu transaksi (persis
    keterbatasan yang sudah dicatat audit C2/C9 untuk file stage). Urutannya
    dipilih supaya kegagalan di tengah tidak berbohong: buku ditulis DULU,
    holdings menyusul. Kalau mati di antaranya, holdings tertinggal satu
    transaksi — konservatif (posisi terbaca lebih kecil dari sebenarnya, atau
    belum ada) — sedangkan urutan sebaliknya bisa membuat holdings mengklaim
    posisi yang tidak punya catatan asalnya sama sekali."""
    d = Path(personal_dir)
    ordered = sort_transactions(transactions)
    write_text_atomic(d / TRANSACTIONS_FILENAME, dumps_safe(
        {
            "transactions": ordered,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        indent=2, ensure_ascii=False,
    ))
    payload = holdings_payload(ordered)
    write_text_atomic(d / HOLDINGS_FILENAME, dumps_safe(payload, indent=2, ensure_ascii=False))
    return payload


def ensure_book(personal_dir: str | Path) -> list[dict]:
    """Buku transaksi saat ini, mengimpor holdings.json lama kalau perlu.

    Titik masuk tunggal untuk semua pembaca/penulis buku, supaya impor warisan
    itu tidak bisa terlewat di salah satu jalur (lihat import_legacy_holdings)."""
    d = Path(personal_dir)
    tx_path = d / TRANSACTIONS_FILENAME
    if tx_path.exists():
        return load_transactions(tx_path)
    legacy = import_legacy_holdings(_load_raw_holdings(d / HOLDINGS_FILENAME))
    if legacy:
        save_portfolio(d, legacy)
    return legacy


# --------------------------------------------------------------------------
# Bentuk transaksi
# --------------------------------------------------------------------------

# Batas "cukup dekat bilangan bulat untuk dianggap bulat" saat menurunkan
# lembar dari nominal. RELATIF, bukan absolut: galat pembagian floating point
# tumbuh seiring besar hasilnya, jadi ambang tetap akan berhenti bekerja pada
# posisi besar (2.000 lembar) padahal bekerja pada 12.
_SHARE_SNAP_REL_EPS = 1e-9


def shares_from_amount(amount: float, price: float) -> float:
    """Lembar dari nominal uang. `2413.80 / 201.15` = 12.000000000000002 di
    floating point — disimpan apa adanya, angka itu akan muncul di tabel
    selamanya dan ikut ke setiap perhitungan turunannya. Hasil yang sangat
    dekat bilangan bulat dikunci ke bulat; pembelian yang memang pecahan
    (mis. $100 di saham $201) tetap pecahan."""
    qty = amount / price
    nearest = round(qty)
    if nearest > 0 and abs(qty - nearest) <= _SHARE_SNAP_REL_EPS * nearest:
        return float(nearest)
    return round(qty, 8)


def _maybe_float(value):
    """float(value) kalau bisa, kalau tidak kembalikan apa adanya.

    Sengaja TIDAK melempar: transaksi dibangun dulu, baru divalidasi. Kalau
    konstruktor ini yang meledak duluan (mis. `float(None)` dari field kosong
    di form), pengguna dapat TypeError mentah alih-alih kalimat "Harga harus
    angka" dari validate_transaction — dan hanya satu kesalahan terlaporkan,
    bukan seluruhnya sekaligus."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _new_transaction(
    ticker: str, side: str, tx_date: str, price, quantity=None,
    fee=0.0, note: str = "", amount=None,
) -> dict:
    """Satu transaksi. Diberi `amount` (nominal uang) ATAU `quantity` (lembar).

    Halaman Portofolio selalu mengirim `amount` — itu yang benar-benar diingat
    orang tentang transaksinya ("masuk 2.400 dolar di 201"), bukan jumlah
    lembar. `quantity` tetap diterima untuk pemanggil lain (tes, impor
    holdings.json lama) dan tetap jadi field yang dihitung seluruh replay.

    `amount` DISIMPAN, tidak cuma dipakai lalu dibuang: menampilkannya kembali
    sebagai `price * quantity` bisa meleset satu sen dari yang diketik, dan
    nominal yang tidak sama dengan catatan broker adalah persis jenis selisih
    yang membuat orang berhenti percaya halamannya."""
    price_f = _maybe_float(price)
    amount_f = _maybe_float(amount)
    if quantity is None and isinstance(amount_f, float) and isinstance(price_f, float) and price_f > 0:
        quantity = shares_from_amount(amount_f, price_f)
    return {
        "id": uuid.uuid4().hex[:12],
        "ticker": str(ticker).upper().strip(),
        "side": side,
        "date": tx_date,
        "price": price_f,
        "quantity": _maybe_float(quantity),
        "amount": amount_f,
        "fee": _maybe_float(fee or 0.0),
        "note": (note or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def sort_transactions(transactions: list[dict]) -> list[dict]:
    """Urut kronologis: tanggal, lalu created_at, lalu id.

    `created_at` jadi pemecah seri karena dua transaksi bertanggal sama harus
    diputar ulang dalam urutan pencatatan (beli lalu jual di hari yang sama
    valid; urutan terbalik akan salah menolaknya sebagai jual berlebih). `id`
    ikut terakhir supaya urutannya stabil — tanpa itu, dua transaksi yang
    diimpor dalam satu detik yang sama bisa bertukar tempat antar-pembacaan
    dan membuat hasil turunan tidak deterministik."""
    return sorted(
        transactions,
        key=lambda t: (str(t.get("date") or ""), str(t.get("created_at") or ""), str(t.get("id") or "")),
    )


# --------------------------------------------------------------------------
# Turunan: replay buku -> posisi
# --------------------------------------------------------------------------

def replay(transactions: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """Putar ulang seluruh buku secara kronologis -> (posisi per ticker, error).

    SELALU replay penuh, tidak pernah menambah-kurangi state yang tersimpan.
    Alasannya bukan kerapian: transaksi boleh di-backdate dan dihapus, jadi
    satu-satunya cara membuat "jual tidak melebihi kepemilikan" tetap benar
    adalah memeriksanya pada urutan waktu final, bukan pada saat pencatatan.

    Error dikembalikan, tidak dilempar — pemanggil validasi butuh daftarnya
    utuh, dan pembaca (halaman portofolio) tidak boleh mati total cuma karena
    satu baris buku bermasalah."""
    positions: dict[str, dict] = {}
    errors: list[str] = []

    for tx in sort_transactions(transactions):
        ticker = str(tx.get("ticker", "")).upper()
        side = tx.get("side")
        qty = float(tx.get("quantity") or 0.0)
        price = float(tx.get("price") or 0.0)
        fee = float(tx.get("fee") or 0.0)
        tx_date = str(tx.get("date") or "")

        pos = positions.setdefault(ticker, {
            "ticker": ticker,
            "quantity": 0.0,
            "total_cost": 0.0,   # basis biaya lembar yang MASIH dipegang (termasuk fee beli)
            "avg_cost": None,
            "buy_date": None,    # tanggal transaksi yang MEMBUKA posisi terbuka sekarang
            "realized_pnl": 0.0,
            "total_fee": 0.0,
            "transaction_count": 0,
            "first_transaction_date": tx_date,
            "last_transaction_date": tx_date,
            # Riwayat per-langkah: keadaan posisi SESUDAH tiap transaksi.
            # Dicatat di sini, bukan dihitung ulang di frontend, karena loop ini
            # sudah melewati persis state itu untuk menghitung posisinya. Dua
            # penulis untuk satu angka (avg cost berjalan) adalah kelas bug
            # "pemeriksa vs format produsen" yang sudah enam kali kena di
            # proyek ini — dan yang di layar akan terlihat sama meyakinkannya
            # waktu keduanya menyimpang.
            "history": [],
        })
        pos["transaction_count"] += 1
        pos["total_fee"] += fee
        pos["last_transaction_date"] = tx_date
        avg_before = pos["avg_cost"]
        realized_before = pos["realized_pnl"]
        opened_here = False

        if side == "beli":
            if pos["quantity"] <= _EPS:
                # Posisi dibuka (atau dibuka ULANG setelah pernah habis dijual).
                # buy_date sengaja di-reset di sini, bukan dipertahankan dari
                # pembelian pertama seumur hidup: yang dipakai horizon_status
                # adalah umur posisi YANG SEDANG dipegang.
                pos["buy_date"] = tx_date
                opened_here = True
            pos["quantity"] += qty
            pos["total_cost"] += price * qty + fee
        elif side == "jual":
            if qty > pos["quantity"] + _EPS:
                errors.append(
                    f"{ticker}: jual {qty:g} lembar pada {tx_date} melebihi kepemilikan saat itu "
                    f"({pos['quantity']:g} lembar)."
                )
                continue
            avg = (pos["total_cost"] / pos["quantity"]) if pos["quantity"] > _EPS else 0.0
            pos["realized_pnl"] += (price - avg) * qty - fee
            pos["total_cost"] -= avg * qty
            pos["quantity"] -= qty
            if pos["quantity"] <= _EPS:
                # Tutup bersih. total_cost dinolkan eksplisit supaya sisa
                # pembulatan tidak menetes ke posisi berikutnya kalau ticker
                # yang sama dibeli lagi nanti.
                pos["quantity"] = 0.0
                pos["total_cost"] = 0.0
                pos["buy_date"] = None
        else:
            errors.append(f"{ticker}: side tidak dikenal '{side}' pada {tx_date}.")
            continue

        pos["avg_cost"] = (pos["total_cost"] / pos["quantity"]) if pos["quantity"] > _EPS else None
        pos["history"].append(_history_row(
            tx, side, qty, price, fee, tx_date, pos, avg_before, realized_before, opened_here,
        ))

    return positions, errors


def _history_row(tx, side, qty, price, fee, tx_date, pos, avg_before, realized_before, opened_here) -> dict:
    """Satu langkah riwayat: transaksinya + keadaan posisi SESUDAHNYA.

    `effect` dirakit di sini, bukan di frontend, karena kalimatnya adalah
    pernyataan tentang angka-angka di atas — kalau ia hidup di tempat lain,
    ia bisa mendeskripsikan perubahan yang tidak pernah terjadi tanpa satu pun
    tes yang gagal. Nominal memakai `amount` yang BENAR-BENAR diketik pengguna
    kalau ada; `price * quantity` cuma cadangan untuk transaksi lama (dan bisa
    meleset satu sen dari catatan broker)."""
    realized = pos["realized_pnl"] - realized_before
    if side == "beli":
        if opened_here:
            effect = "posisi dibuka"
        elif avg_before is None or pos["avg_cost"] is None:
            effect = "lembar bertambah"
        else:
            delta = pos["avg_cost"] - avg_before
            if abs(delta) < 0.005:
                effect = "avg cost praktis tidak bergerak"
            else:
                effect = f"avg cost {'naik' if delta > 0 else 'turun'} {abs(delta):,.2f}"
    elif pos["quantity"] <= _EPS:
        effect = f"posisi tutup, realized {realized:+,.2f}"
    else:
        effect = f"realized {realized:+,.2f}, avg cost tetap"

    amount = tx.get("amount")
    return {
        "id": tx.get("id"),
        "date": tx_date,
        "side": side,
        "price": price,
        "quantity": qty,
        "fee": fee,
        "amount": float(amount) if amount is not None else price * qty,
        "amount_derived": amount is None,
        "note": tx.get("note") or "",
        "quantity_after": pos["quantity"],
        "avg_cost_after": pos["avg_cost"],
        "realized": realized if side == "jual" else None,
        "effect": effect,
    }


def derive_positions(transactions: list[dict]) -> list[dict]:
    """Semua ticker yang pernah disentuh buku — TERMASUK yang sudah tutup.

    Dipakai halaman portofolio (posisi tertutup tetap membawa realized_pnl yang
    harus kelihatan). Yang ditulis ke holdings.json cuma yang terbuka — lihat
    holdings_payload dan aturan di docstring modul."""
    positions, _ = replay(transactions)
    out = []
    for pos in positions.values():
        row = dict(pos)
        row["is_open"] = row["quantity"] > _EPS
        out.append(row)
    return sorted(out, key=lambda r: (not r["is_open"], r["ticker"]))


def holdings_payload(transactions: list[dict]) -> dict:
    """Isi holdings.json — HANYA posisi terbuka, dalam bentuk yang sudah dibaca
    load_holdings() sekarang (`{"holdings": [{ticker, avg_cost, buy_date, ...}]}`).

    Field tambahan (realized_pnl, total_cost, ...) ikut ditulis: load_holdings
    mengambil seluruh dict per ticker apa adanya, dan compute_position cuma
    memetik avg_cost/buy_date — jadi field ekstra tidak mengganggu apa pun dan
    membuat file ini tetap bisa dibaca manusia tanpa membuka buku transaksinya."""
    holdings = [
        {
            "ticker": p["ticker"],
            "quantity": p["quantity"],
            "avg_cost": p["avg_cost"],
            "buy_date": p["buy_date"],
            "total_cost": p["total_cost"],
            "realized_pnl": p["realized_pnl"],
            "total_fee": p["total_fee"],
            "last_transaction_date": p["last_transaction_date"],
        }
        for p in derive_positions(transactions) if p["is_open"]
    ]
    return {
        "holdings": holdings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "derived_from": TRANSACTIONS_FILENAME,
        "cost_basis_method": "average_cost",
        # Peringatan yang tertulis DI DALAM datanya, bukan cuma di kode: berkas
        # ini dulu sumber input, sekarang keluaran. Siapa pun (termasuk aku, di
        # sesi lain) yang mengeditnya tangan akan kehilangan suntingannya pada
        # penulisan buku berikutnya.
        "note": "TURUNAN — jangan diedit tangan; sunting transactions.json lewat halaman Portofolio.",
    }


# --------------------------------------------------------------------------
# Validasi
# --------------------------------------------------------------------------

def validate_transaction(tx: dict, existing: list[dict], today: date | None = None) -> list[str]:
    """Daftar alasan penolakan (kosong = boleh masuk).

    Pemeriksaan "jual tidak melebihi kepemilikan" dilakukan dengan REPLAY buku
    berisi transaksi kandidat, bukan dengan membandingkan terhadap posisi akhir.
    Bedanya nyata: transaksi bertanggal mundur bisa lolos uji posisi-akhir
    padahal membuat kepemilikan negatif di tengah rentang waktu."""
    today = today or datetime.now(timezone.utc).date()
    errors: list[str] = []

    ticker = str(tx.get("ticker", "")).upper().strip()
    if not ticker:
        errors.append("Ticker wajib diisi.")
    elif not _TICKER_RE.match(ticker):
        errors.append(f"Ticker '{ticker}' tidak berbentuk simbol yang sah.")

    if tx.get("side") not in SIDES:
        errors.append(f"Jenis transaksi harus salah satu dari {', '.join(SIDES)}.")

    raw_date = str(tx.get("date") or "")
    try:
        tx_date = date.fromisoformat(raw_date)
        if tx_date > today:
            errors.append(f"Tanggal {raw_date} ada di masa depan.")
    except ValueError:
        errors.append(f"Tanggal '{raw_date}' bukan format YYYY-MM-DD.")

    # Field kedua yang diperiksa mengikuti CARA transaksinya dicatat: halaman
    # Portofolio mengirim nominal uang, pemanggil lain mengirim lembar.
    # Melaporkan "Jumlah lembar harus angka" kepada orang yang mengetik
    # nominal akan menunjuk field yang tidak ada di layarnya.
    second = ("amount", "Nominal") if tx.get("amount") is not None else ("quantity", "Jumlah lembar")
    for field_name, label in (("price", "Harga"), second):
        try:
            value = float(tx.get(field_name))
        except (TypeError, ValueError):
            errors.append(f"{label} harus angka.")
            continue
        if value <= 0:
            errors.append(f"{label} harus lebih besar dari 0.")

    # Nominal yang terlalu kecil dibanding harga membulat jadi nol lembar —
    # transaksi yang tidak memindahkan apa pun tapi terlihat tersimpan.
    if not errors and second[0] == "amount" and not float(tx.get("quantity") or 0.0) > 0:
        errors.append(
            f"Nominal {float(tx['amount']):g} pada harga {float(tx['price']):g} "
            f"tidak cukup untuk satu pecahan lembar pun."
        )

    try:
        if float(tx.get("fee") or 0.0) < 0:
            errors.append("Fee tidak boleh negatif.")
    except (TypeError, ValueError):
        errors.append("Fee harus angka.")

    if errors:
        # Replay tidak dijalankan kalau bentuknya sendiri sudah salah — hasilnya
        # cuma akan menambah error turunan yang membingungkan (mis. "jual 0
        # lembar melebihi kepemilikan") di atas sebab aslinya.
        return errors

    _, replay_errors = replay(list(existing) + [tx])
    errors.extend(replay_errors)
    return errors


def add_transaction(
    personal_dir: str | Path, ticker: str, side: str, tx_date: str,
    price, quantity=None, fee: float = 0.0, note: str = "",
    today: date | None = None, amount=None,
) -> tuple[dict | None, list[str]]:
    """(transaksi yang tersimpan, error). Menulis hanya kalau nol error.

    Beri `amount` (nominal uang) atau `quantity` (lembar) — lihat
    _new_transaction untuk kenapa halaman Portofolio selalu memakai yang
    pertama."""
    book = ensure_book(personal_dir)
    tx = _new_transaction(ticker, side, tx_date, price, quantity, fee, note, amount=amount)
    errors = validate_transaction(tx, book, today=today)
    if errors:
        return None, errors
    book.append(tx)
    save_portfolio(personal_dir, book)
    return tx, []


def delete_transaction(personal_dir: str | Path, tx_id: str) -> tuple[dict | None, list[str]]:
    """Hapus satu transaksi lalu turunkan ulang.

    Penghapusan bisa membuat buku jadi tidak sah (menghapus sebuah "beli"
    membuat "jual" sesudahnya melebihi kepemilikan). Buku yang dihasilkan
    di-replay dulu; kalau muncul error, penghapusan DIBATALKAN dan alasannya
    dikembalikan — lebih baik menolak daripada menyimpan buku yang posisinya
    tidak bisa dihitung."""
    book = ensure_book(personal_dir)
    remaining = [t for t in book if t.get("id") != tx_id]
    if len(remaining) == len(book):
        return None, [f"Transaksi '{tx_id}' tidak ditemukan."]
    _, errors = replay(remaining)
    if errors:
        return None, [
            "Penghapusan dibatalkan — buku jadi tidak sah:", *errors,
        ]
    removed = next(t for t in book if t.get("id") == tx_id)
    save_portfolio(personal_dir, remaining)
    return removed, []


# --------------------------------------------------------------------------
# Ringkasan (dipakai halaman Portofolio; tidak menyentuh harga di sini —
# harga live disuntik pemanggil supaya modul ini tetap bebas jaringan)
# --------------------------------------------------------------------------

def summarize(positions: list[dict], prices: dict[str, float | None]) -> dict:
    """Ringkasan portofolio dari posisi turunan + harga terakhir per ticker.

    `prices` disuntik dari luar (bukan difetch di sini) supaya modul ini tetap
    murni & bisa diuji tanpa jaringan — sekaligus supaya pemanggil bebas
    memilih sumber harga (live quote vs last_price Evidence).

    Posisi yang harganya tidak diketahui TIDAK dianggap bernilai 0 — itu akan
    melaporkan rugi palsu sebesar seluruh modalnya. Ia dihitung terpisah lewat
    `positions_without_price` dan dikeluarkan dari nilai pasar & unrealized,
    sementara modalnya tetap masuk `total_cost` (uangnya memang keluar)."""
    total_cost = 0.0
    market_value = 0.0
    priced_cost = 0.0
    realized = 0.0
    unpriced: list[str] = []
    open_count = 0

    for pos in positions:
        realized += pos.get("realized_pnl") or 0.0
        if not pos.get("is_open"):
            continue
        open_count += 1
        cost = pos.get("total_cost") or 0.0
        total_cost += cost
        price = prices.get(pos["ticker"])
        if price is None:
            unpriced.append(pos["ticker"])
            continue
        market_value += price * (pos.get("quantity") or 0.0)
        priced_cost += cost

    unrealized = market_value - priced_cost if priced_cost else 0.0
    return {
        "open_positions": open_count,
        "total_cost": total_cost,
        "market_value": market_value,
        "priced_cost": priced_cost,
        "unrealized_pnl": unrealized,
        "unrealized_pct": (unrealized / priced_cost * 100.0) if priced_cost else None,
        "realized_pnl": realized,
        "positions_without_price": unpriced,
    }


def position_weights(positions: list[dict], prices: dict[str, float | None]) -> dict[str, float | None]:
    """Bobot tiap posisi terbuka terhadap nilai pasar total (persen).

    Berbasis NILAI PASAR, bukan modal: konsentrasi yang berbahaya adalah
    konsentrasi uang yang ada sekarang, bukan uang yang dulu dikeluarkan.
    Ticker tanpa harga dapat None, bukan 0 — dan penyebutnya juga tidak
    memasukkannya, jadi bobot yang dilaporkan selalu terhadap bagian yang
    benar-benar bisa dinilai."""
    values: dict[str, float] = {}
    for pos in positions:
        if not pos.get("is_open"):
            continue
        price = prices.get(pos["ticker"])
        if price is None:
            continue
        values[pos["ticker"]] = price * (pos.get("quantity") or 0.0)
    total = sum(values.values())
    out: dict[str, float | None] = {}
    for pos in positions:
        if not pos.get("is_open"):
            continue
        v = values.get(pos["ticker"])
        out[pos["ticker"]] = (v / total * 100.0) if (v is not None and total > 0) else None
    return out
