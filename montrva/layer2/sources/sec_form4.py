"""Fetch & track SEC Form 4 (insider trades) untuk detect institutional activity.

Form 4 filings (regulasi EDGAR) track ownership perubahan oleh officers, directors,
10% owners, dan other insiders. Saat diisi oleh institutional traders / hedge fund
operators, pergerakan saham mereka jadi signal kuat untuk bullish/bearish sentiment.

Strategy (MVP):
- Fetch Form 4 filing list dari SEC submissions API
- Track filing metadata: date, filer name, relationship (no detailed parsing)
- Signal: "Form 4 filed in last N days" = insider activity detected
- Future: parse XML/HTML untuk get transaction details (complex, deferred)
"""
from __future__ import annotations

import sys

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

from ... import cache
from ..contracts import InstitutionalActivity, InstitutionalTrade, SourceMetadata
from .sec_parser import apply_sec_rate_limit, get_cik_from_ticker
from .sec_edgar import _HEADERS, fetch_raw_submissions

_FORM4_TTL = 24 * 3600  # 24 jam

# --- Parsing detail transaksi (15 Agu 2026) --------------------------------
#
# Sebelum ini seluruh berkas berhenti di metadata: tiap Form 4 jadi satu "trade"
# sintetis ber-transaction_type="filing", shares=0, price=None. Alasan yang
# tertulis di kode: "SEC archive path per-filing tidak predictable, sering 404".
#
# DIUJI LANGSUNG DAN ALASAN ITU TIDAK BENAR. Path-nya sepenuhnya bisa dibangun
# dari `accessionNumber` + `primaryDocument` di submissions JSON, dan 3 dari 3
# filing yang diuji balik HTTP 200. Yang membuatnya terlihat gagal: SEC mengisi
# `primaryDocument` dengan versi TAMPILAN hasil render XSL
# ("xslF345X06/form4.xml") — HTML, bukan XML. XML mentahnya ada di folder
# accession yang sama tanpa awalan itu. Satu potongan path, bukan ketidakpastian.
#
# `_parse_form4_xml` versi lama juga tidak akan pernah cocok walau XML-nya benar:
# ia mencari `.//ownershipDocument` padahal itu ELEMEN ROOT, dan menelusuri
# `transactionOrAmendment/documentType` yang bukan jalur Form 4 mana pun.
_SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

# Kode transaksi Form 4 (SEC Table I). Yang dipisahkan di sini cuma yang
# BERBEDA ARTINYA untuk pertanyaan "apakah insider menjual di pasar":
#   P/S = beli/jual di pasar terbuka -> ini yang dihitung
#   A   = hibah/award dari perusahaan (bukan keputusan beli)
#   M   = exercise opsi (menambah lembar, tapi bukan pembelian pasar)
#   F   = lembar ditahan untuk pajak (bukan penjualan sukarela)
#   G   = hibah/gift
# Tanpa pemisahan ini, "insider menjual" akan ikut menghitung pemotongan pajak
# dan exercise opsi — dua hal yang terjadi otomatis dan tidak mengandung
# pendapat siapa pun tentang harga.
_TX_CODE_MEANING = {
    "P": "buy", "S": "sell", "A": "grant", "M": "exercise",
    "F": "tax", "G": "gift", "D": "disposition", "C": "conversion",
}
_MARKET_CODES = ("P", "S")  # cuma ini yang dianggap keputusan beli/jual


def fetch_institutional_activity(ticker: str, days_lookback: int = 30) -> InstitutionalActivity:
    """Ambil Form 4 filings terkini untuk satu ticker — MVP version tracks filing metadata only.

    Simplified approach: Form 4 XML parsing kompleks (SEC archive structure varies),
    jadi untuk now kita track filing dates & filer names as indicators of insider activity.
    Future: full transaction parsing ketika SEC API improvements available.
    """
    cik = get_cik_from_ticker(ticker)
    if not cik:
        return InstitutionalActivity(
            metadata=SourceMetadata(
                source="sec_form4",
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="missing",
            )
        )

    # Lookback WAJIB masuk kunci: tanpa itu pemanggil 30-hari dan 90-hari
    # saling memakan hasil satu sama lain.
    cache_key = f"form4_activity_{cik}_{days_lookback}"
    cached = cache.get("sec_form4", cache_key, _FORM4_TTL)
    if cached:
        # Reconstruct SourceMetadata dari dict
        meta_dict = cached.pop("metadata")
        metadata = SourceMetadata(**meta_dict)
        # Reconstruct trades list
        trades_data = cached.pop("recent_trades", [])
        trades = [InstitutionalTrade(**t) for t in trades_data]
        return InstitutionalActivity(
            metadata=metadata,
            recent_trades=trades,
            **cached
        )

    # Fetch daftar filing (submissions JSON) — shared dengan sec_edgar.fetch_sec_filings()
    # lewat fetch_raw_submissions(), yang cache-nya sudah ditandai per-CIK, jadi kalau
    # sec_edgar.py sudah/akan fetch CIK yang sama di run Evidence yang sama, tidak ada
    # panggilan network dobel ke data.sec.gov (juga tetap lewat apply_sec_rate_limit()
    # yang sama kalau memang cache-miss).
    data = fetch_raw_submissions(cik)

    if not data:
        return InstitutionalActivity(
            metadata=SourceMetadata(
                source="sec_form4",
                fetched_at=datetime.now(timezone.utc).isoformat(),
                status="missing",
            )
        )

    # Extract Form 4 filings dari recent submissions
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days_lookback)).date()
    trades: list[InstitutionalTrade] = []
    filing_count = 0  # jumlah Form 4 yang DIAJUKAN, terlepas dari isinya

    print(f"[sec_form4:{cik}] Scanning {len(forms)} total filings for Form 4...", file=sys.stderr)

    # MVP approach: Form 4 filings = indicator of insider activity (yang pasti ada)
    # Detail transaction parsing deferred (SEC archive structure too complex)
    for i, (form, date_str) in enumerate(zip(forms, dates)):
        if form != "4":
            continue
        try:
            filing_date = datetime.fromisoformat(date_str).date()
            if filing_date < cutoff_date:
                break  # sorted descending

            filing_count += 1
            acc = accessions[i] if i < len(accessions) else None
            doc = primary_docs[i] if i < len(primary_docs) else None
            parsed = _fetch_and_parse(cik, acc, doc, date_str) if (acc and doc) else []
            if parsed:
                trades.extend(parsed)
                continue

            # Cadangan: XML tidak terambil/terbaca. Entri sintetis LAMA
            # dipertahankan supaya satu filing yang bermasalah tidak
            # menghilangkan fakta bahwa ada aktivitas insider -- tapi
            # transaction_type="filing" menandai bahwa arahnya TIDAK diketahui,
            # dan hitungan beli/jual di bawah sengaja tidak menghitungnya.
            trades.append(InstitutionalTrade(
                trader_name="[Form 4 Filer]",
                relationship="Insider",
                transaction_type="filing",
                shares=0,
                price=None,
                transaction_date=date_str,
                form_type="4",
                filing_date=date_str,
                evidence_id=acc,
            ))

        except (ValueError, IndexError):
            continue

    # Menyebut KEDUANYA: sesudah parsing detail hidup, satu filing bisa berisi
    # beberapa transaksi, dan pesan lama ("Found {len(trades)} Form 4 filings")
    # membuat 8 transaksi dari 4 filing terbaca sebagai 8 filing.
    print(f"[sec_form4:{cik}] {filing_count} Form 4 filing -> {len(trades)} transaksi "
          f"({days_lookback} hari)", file=sys.stderr)

    # Hitungan sekarang NYATA, bukan proksi jumlah filing. Cuma kode pasar
    # (P/S) yang masuk: hibah, exercise, dan pemotongan pajak bukan keputusan
    # beli/jual siapa pun.
    beli = [t for t in trades if t.transaction_type == "buy"]
    jual = [t for t in trades if t.transaction_type == "sell"]
    net = sum(t.shares for t in beli) - sum(t.shares for t in jual)

    def _terbesar(rows):
        if not rows:
            return None
        per_orang: dict[str, int] = {}
        for t in rows:
            per_orang[t.trader_name] = per_orang.get(t.trader_name, 0) + t.shares
        return max(per_orang.items(), key=lambda kv: kv[1])[0]

    result = InstitutionalActivity(
        metadata=SourceMetadata(
            source="sec_form4",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="ok" if trades else "degraded",
        ),
        recent_trades=trades[:50],
        buy_count_30d=len(beli),
        sell_count_30d=len(jual),
        net_shares_30d=net,
        top_buyer=_terbesar(beli),
        top_seller=_terbesar(jual),
        # Jumlah filing yang DIAJUKAN, bukan yang menghasilkan transaksi
        # terparse: sebuah Form 4 bisa sah tapi cuma berisi transaksi
        # derivatif, dan itu tetap aktivitas insider.
        filing_count_30d=filing_count,
    )

    # Cache result
    cache.set("sec_form4", cache_key, {
        "metadata": {
            "source": result.metadata.source,
            "fetched_at": result.metadata.fetched_at,
            "status": result.metadata.status,
        },
        "recent_trades": [{
            "trader_name": t.trader_name,
            "relationship": t.relationship,
            "transaction_type": t.transaction_type,
            "shares": t.shares,
            "price": t.price,
            "transaction_date": t.transaction_date,
            "form_type": t.form_type,
            "filing_date": t.filing_date,
        } for t in result.recent_trades],
        "buy_count_30d": result.buy_count_30d,
        "filing_count_30d": result.filing_count_30d,
        "sell_count_30d": result.sell_count_30d,
        "net_shares_30d": result.net_shares_30d,
        "top_buyer": result.top_buyer,
        "top_seller": result.top_seller,
    })

    return result


def _fetch_and_parse(cik: str, accession: str, primary_doc: str, filing_date: str) -> list[InstitutionalTrade]:
    """Ambil XML mentah satu Form 4 lalu parse. [] kalau gagal — pemanggil
    jatuh ke entri sintetis, jadi satu filing bermasalah tidak menghapus
    fakta bahwa ada aktivitas insider.

    KUNCI PATH-NYA: `primary_doc` dari SEC berbentuk "xslF345X06/form4.xml" —
    itu versi TAMPILAN hasil render XSL (HTML). XML mentahnya ada di folder
    accession yang sama tanpa awalan itu, jadi cukup ambil basename-nya.
    """
    acc = accession.replace("-", "")
    url = _SEC_ARCHIVE_URL.format(cik=int(cik), acc=acc, doc=primary_doc.split("/")[-1])
    key = f"form4_xml_{acc}"
    xml_text = cache.get("sec_form4_xml", key, _FORM4_TTL * 30)
    if xml_text is None:
        try:
            apply_sec_rate_limit()
            r = requests.get(url, headers=_HEADERS, timeout=15)
            if r.status_code != 200:
                return []
            xml_text = r.text
        except Exception:  # noqa: BLE001 — jaringan/timeout: degradasi, bukan gagal
            return []
        # XML satu Form 4 tidak pernah berubah sesudah diajukan (amendemen
        # datang sebagai filing BARU ber-accession sendiri), jadi TTL-nya jauh
        # lebih panjang dari daftar filing-nya: sekali diambil, selamanya sah.
        cache.set("sec_form4_xml", key, xml_text)
    try:
        return _parse_form4_xml(xml_text, filing_date, accession)
    except Exception:  # noqa: BLE001
        return []


def _parse_form4_xml(xml_text: str, filing_date: str, accession: str | None = None) -> list[InstitutionalTrade]:
    """Transaksi non-derivatif dari satu Form 4.

    Root dokumennya SENDIRI `ownershipDocument` — versi lama fungsi ini mencari
    `.//ownershipDocument` (anak, bukan root) lalu menelusuri
    `transactionOrAmendment/documentType` yang bukan jalur Form 4 mana pun, jadi
    ia mengembalikan [] untuk setiap masukan yang sah sekalipun.

    Cuma `nonDerivativeTransaction` yang dibaca: transaksi derivatif (opsi,
    RSU) tidak memindahkan lembar biasa pada saat itu, dan menghitungnya sebagai
    beli/jual akan menggandakan exercise yang sudah muncul sebagai kode M.
    """
    root = ET.fromstring(xml_text)
    nama = (root.findtext(".//reportingOwner/reportingOwnerId/rptOwnerName") or "").strip() or "[Insider]"

    rel = root.find(".//reportingOwner/reportingOwnerRelationship")
    relasi = "Insider"
    if rel is not None:
        for tag, label in (("isDirector", "Director"), ("isOfficer", "Officer"),
                           ("isTenPercentOwner", "10% Owner")):
            if (rel.findtext(tag) or "").strip() in ("1", "true"):
                relasi = rel.findtext("officerTitle") or label if tag == "isOfficer" else label
                break

    out: list[InstitutionalTrade] = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        kode = (tx.findtext(".//transactionCoding/transactionCode") or "").strip().upper()
        jenis = _TX_CODE_MEANING.get(kode, "other")
        # Arah diambil dari transactionAcquiredDisposedCode, BUKAN ditebak dari
        # kode: kode P/S sudah menyiratkan arah, tapi kode lain tidak, dan
        # A/D-lah satu-satunya field yang menyatakannya secara eksplisit.
        ad = (tx.findtext(".//transactionAcquiredDisposedCode/value") or "").strip().upper()
        if kode in _MARKET_CODES:
            jenis = "buy" if ad == "A" else "sell"

        def _angka(path):
            raw = tx.findtext(path)
            try:
                return float(raw) if raw not in (None, "") else None
            except ValueError:
                return None

        lembar = _angka(".//transactionShares/value")
        if lembar is None:
            continue
        out.append(InstitutionalTrade(
            trader_name=nama,
            relationship=relasi,
            transaction_type=jenis,
            shares=int(lembar),
            price=_angka(".//transactionPricePerShare/value"),
            transaction_date=(tx.findtext(".//transactionDate/value") or filing_date)[:10],
            form_type="4",
            filing_date=filing_date,
            evidence_id=accession,
        ))
    return out

