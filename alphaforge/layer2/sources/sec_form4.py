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
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

from ... import cache
from ..contracts import InstitutionalActivity, InstitutionalTrade, SourceMetadata
from .sec_parser import get_cik_from_ticker
from .sec_edgar import fetch_raw_submissions

_FORM4_TTL = 24 * 3600  # 24 jam


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

    cache_key = f"form4_activity_{cik}"
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

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days_lookback)).date()
    trades: list[InstitutionalTrade] = []
    buy_count = 0  # Not literal buys — counts Form 4 filings as an activity proxy (see docstring)

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

            # Create synthetic trade entry: Form 4 filed = insider activity
            # Assume filer is "insider" (we don't parse detailed names from XML)
            # Direction: assume neutral (we don't know if buy/sell without parsing)
            # but track that filing occurred
            trade = InstitutionalTrade(
                trader_name="[Form 4 Filer]",  # Placeholder, details would require XML parse
                relationship="Insider",
                transaction_type="filing",  # Not buy/sell, just marks activity
                shares=0,  # Unknown without parsing
                price=None,
                transaction_date=date_str,
                form_type="4",
                filing_date=date_str,
                evidence_id=accessions[i] if i < len(accessions) else None,
            )
            trades.append(trade)
            # For MVP, just track that filing occurred
            buy_count += 1  # Count as activity (not literal buy, but insider involvement)

        except (ValueError, IndexError):
            continue

    print(f"[sec_form4:{cik}] Found {len(trades)} Form 4 filings in {days_lookback}-day window", file=sys.stderr)

    result = InstitutionalActivity(
        metadata=SourceMetadata(
            source="sec_form4",
            fetched_at=datetime.now(timezone.utc).isoformat(),
            status="ok" if trades else "degraded",
        ),
        recent_trades=trades[:50],
        buy_count_30d=buy_count,  # Counts Form 4 filings as insider activity indicators
        sell_count_30d=0,  # Not tracked in MVP
        net_shares_30d=0,  # Not tracked in MVP
        top_buyer="[Form 4 Filers]" if trades else None,  # Generic for MVP
        top_seller=None,
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
        "sell_count_30d": result.sell_count_30d,
        "net_shares_30d": result.net_shares_30d,
        "top_buyer": result.top_buyer,
        "top_seller": result.top_seller,
    })

    return result


def _parse_form4_xml(xml_text: str, filing_date_str: str) -> list[InstitutionalTrade]:
    """Parse Form 4 XML, extract transaction details."""
    trades = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"Malformed XML: {e}")

    # Navigate to transactions (path varies, try common ones)
    # Form 4 XML structure: root > form4 > ownershipDocument > transactionOrAmendment > ...
    namespaces = {'': 'http://www.sec.gov/cgi-bin'}

    # Find all transaction entries
    for doc in root.findall(".//ownershipDocument"):
        # Issuer info
        issuer = doc.find(".//issuer")
        # Skip if not a stock transaction

        # Owner info
        owner = doc.find(".//reportingOwner")
        if owner is None:
            continue

        owner_name = owner.findtext(".//reportingOwnerId/rptOwnerName", "")
        relationship = owner.findtext(".//reportingOwnerRelationship/isDirector", "")
        if not relationship:
            relationship = owner.findtext(".//reportingOwnerRelationship/isOfficer", "")
        if not relationship:
            relationship = "Insider"

        # Transactions
        tx_doc = doc.find(".//transactionOrAmendment/documentType")
        if tx_doc is not None and tx_doc.text != "4":
            continue

        for tx in doc.findall(".//transactionOrAmendment/nonDerivativeTransaction"):
            try:
                trans_type_el = tx.find(".//transactionType")
                trans_type = trans_type_el.text if trans_type_el is not None else "unknown"

                # Map transaction codes to buy/sell
                trans_code = trans_type.upper()
                if trans_code in ("P", "PURCHASE"):
                    transaction_type = "buy"
                elif trans_code in ("S", "SALE"):
                    transaction_type = "sell"
                elif trans_code in ("M", "EXERCISE"):
                    transaction_type = "exercise"
                elif trans_code in ("G", "GRANT"):
                    transaction_type = "grant"
                else:
                    transaction_type = trans_type.lower()

                # Shares & price
                shares_el = tx.find(".//transactionShares/value")
                if shares_el is None:
                    continue
                try:
                    shares = int(float(shares_el.text or 0))
                except (ValueError, TypeError):
                    continue

                price_el = tx.find(".//transactionPricePerShare/value")
                price = None
                if price_el is not None:
                    try:
                        price = float(price_el.text or 0)
                    except (ValueError, TypeError):
                        pass

                # Transaction date
                trans_date_el = tx.find(".//transactionDate/value")
                transaction_date = trans_date_el.text if trans_date_el is not None else filing_date_str

                trade = InstitutionalTrade(
                    trader_name=owner_name,
                    relationship=relationship,
                    transaction_type=transaction_type,
                    shares=shares,
                    price=price,
                    transaction_date=transaction_date,
                    form_type="4",
                    filing_date=filing_date_str,
                )
                trades.append(trade)

            except Exception as e:
                # Skip malformed transaction
                continue

    return trades
