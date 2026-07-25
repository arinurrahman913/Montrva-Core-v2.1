"""AI-generated narrative summary of a ticker via Google Gemini.

Reads the FULL Evidence package (all 8 sections) plus a handful of already-
computed Knowledge metrics (trends/streak/price-target upside — passed in
so the model narrates numbers Knowledge already got right instead of
re-deriving them itself, which LLMs are unreliable at over multi-quarter
arithmetic). Output is structured (Gemini response_schema, not free text
parsed by hand): a short qualitative narrative plus a handful of
AI-selected quantitative highlights.

Deliberately NOT part of the ~5000-ticker full pipeline refresh (would add
minutes/cost per run for tickers nobody ever looks at) — called on-demand
by backend/app.py when the dashboard opens a ticker's Knowledge modal, then
cached to disk keyed by ticker + KnowledgeMetadata.evidence_date so
re-opening the same ticker doesn't re-call the API unless its underlying
Evidence/Knowledge data actually changed.

Free tier (aistudio.google.com/apikey). Gracefully degrades — returns None,
never raises — if GEMINI_API_KEY isn't set, same convention as
sources/finnhub.py's FINNHUB_API_KEY.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "qualitative": {"type": "STRING"},
        "quantitative_highlights": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"label": {"type": "STRING"}, "value": {"type": "STRING"}},
                "required": ["label", "value"],
            },
        },
    },
    "required": ["qualitative", "quantitative_highlights"],
}


def _fmt(value, suffix: str = "") -> str:
    if value is None:
        return "tidak tersedia"
    if isinstance(value, float):
        return f"{value:.1f}{suffix}"
    return f"{value}{suffix}"


def _fmt_money(value) -> str:
    """Compact notation (K/M/B) buat nominal dolar besar (market cap, FCF,
    revenue kuartalan) -- tanpa ini LLM cuma echo angka mentah yang dikasih
    (mis. 219082897434.4), bukan salahnya LLM, sumbernya emang belum
    diringkas sebelum masuk prompt."""
    if value is None:
        return "tidak tersedia"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e9:
        return f"{sign}${value / 1e9:.2f}B"
    if value >= 1e6:
        return f"{sign}${value / 1e6:.2f}M"
    if value >= 1e3:
        return f"{sign}${value / 1e3:.1f}K"
    return f"{sign}${value:.0f}"


def _build_prompt(evidence: dict, knowledge: dict) -> str:
    pm = evidence.get("price_market") or {}
    fnd = evidence.get("fundamental") or {}
    quarters = (fnd.get("quarterly_data") or [])[:4]
    io = evidence.get("institutional_ownership") or {}
    top_holders = (io.get("top_holders") or [])[:3]
    ia = evidence.get("institutional_activity") or {}
    news = ((evidence.get("news") or {}).get("news") or [])[:5]
    filings = ((evidence.get("sec_filings") or {}).get("filings") or [])[:5]
    cp = evidence.get("company_profile") or {}
    ae = evidence.get("analyst_estimates") or {}
    eps_hist = ae.get("eps_surprise_history") or []
    rev_est = ae.get("revenue_estimates") or []

    ht = knowledge.get("historical_trend") or {}
    val = knowledge.get("valuation") or {}
    pt = val.get("price_target") or {}

    quarters_txt = "; ".join(
        f"{q.get('period')}: revenue {_fmt_money(q.get('revenue'))}, net income {_fmt_money(q.get('net_income'))}"
        for q in quarters
    ) or "tidak tersedia"
    holders_txt = "; ".join(
        f"{h.get('holder')} ({_fmt(h.get('pct_held'), '%')})" for h in top_holders
    ) or "tidak tersedia"
    news_txt = "; ".join(
        f"\"{n.get('headline')}\" ({n.get('published_at', '')[:10]})" for n in news
    ) or "tidak ada berita terbaru"
    filings_txt = "; ".join(
        f"{f.get('form_type')} ({f.get('filing_date')})" for f in filings
    ) or "tidak ada filing terbaru"
    eps_txt = "; ".join(
        f"{e.get('quarter')}: actual {_fmt(e.get('eps_actual'))} vs estimate {_fmt(e.get('eps_estimate'))} ({_fmt(e.get('surprise_pct'), '%')})"
        for e in eps_hist
    ) or "tidak tersedia"
    rev_est_txt = "; ".join(
        f"{r.get('period')}: avg {_fmt_money(r.get('avg'))}, growth {_fmt(r.get('growth'), '%')} ({_fmt(r.get('num_analysts'))} analis)"
        for r in rev_est
    ) or "tidak tersedia"

    return f"""Anda asisten analisis keuangan. Berdasarkan data di bawah untuk saham {evidence.get('ticker')} (sektor {fnd.get('sector') or 'tidak diketahui'}), hasilkan:
1. "qualitative": narasi 4-6 kalimat Bahasa Indonesia yang menjelaskan kondisi perusahaan secara objektif untuk investor pemula — gabungkan konteks bisnis, kinerja keuangan, dan berita/filing terbaru kalau relevan.
2. "quantitative_highlights": 5-6 angka PALING PENTING dari data di bawah (pilih sendiri mana yang paling menonjol/relevan untuk ticker ini spesifik — jangan selalu pilih urutan yang sama), format {{label, value}} singkat.

ATURAN WAJIB:
- HANYA gunakan angka/fakta yang diberikan di bawah. JANGAN mengarang data yang tidak ada.
- JANGAN kasih rekomendasi beli/jual/hold — ini penjelasan fakta, bukan saran investasi.
- Soal "Form 4 filing count": itu CUMA jumlah filing (termasuk grant/vesting rutin), BUKAN konfirmasi insider membeli saham — jangan disebut "insider membeli" atau "insider selling", cukup sebut faktanya sebagai "aktivitas filing".
- Kalau suatu data "tidak tersedia", jangan dipaksakan disebut.

DATA -- COMPANY PROFILE:
Nama: {cp.get('long_name')}, Karyawan: {_fmt(cp.get('employees'))}, Lokasi: {cp.get('city')}, {cp.get('country')}
Deskripsi: {cp.get('business_summary') or 'tidak tersedia'}

DATA -- PRICE MARKET:
Harga sekarang: ${_fmt(pm.get('last_price'))}, Market cap: {_fmt_money(pm.get('market_cap'))}, Volume: {_fmt(pm.get('volume'))}
52-week high/low: ${_fmt(pm.get('high_52w'))} / ${_fmt(pm.get('low_52w'))}, Beta: {_fmt(pm.get('beta'))}

DATA -- FUNDAMENTAL:
P/E: {_fmt(fnd.get('pe_ratio'))}, D/E: {_fmt(fnd.get('debt_to_equity'))}, Current ratio: {_fmt(fnd.get('current_ratio'))}, Quick ratio: {_fmt(fnd.get('quick_ratio'))}
ROE: {_fmt(fnd.get('roe'), '%')}, ROA: {_fmt(fnd.get('roa'), '%')}, Gross margin: {_fmt(fnd.get('gross_margin'), '%')}, Operating margin: {_fmt(fnd.get('operating_margin'), '%')}
Dividend yield: {_fmt(fnd.get('dividend_yield'), '%')}, Free cash flow: {_fmt_money(fnd.get('free_cash_flow'))}
4 kuartal terakhir (revenue, net income): {quarters_txt}

DATA -- TREN TERHITUNG (dari Knowledge, sudah akurat, jangan dihitung ulang):
Revenue YoY 4 kuartal: Q-3 {_fmt((knowledge.get('financial_health') or {}).get('revenue_trend', {}).get('yoy_q1'), '%')}, Q-2 {_fmt((knowledge.get('financial_health') or {}).get('revenue_trend', {}).get('yoy_q2'), '%')}, Q-1 {_fmt((knowledge.get('financial_health') or {}).get('revenue_trend', {}).get('yoy_q3'), '%')}, Q kini {_fmt((knowledge.get('financial_health') or {}).get('revenue_trend', {}).get('yoy_q4'), '%')}
Net margin kuartal kini: {_fmt((knowledge.get('financial_health') or {}).get('net_margin_trend', {}).get('q4'), '%')}
Return 1 tahun: {_fmt(ht.get('return_1y'), '%')}, Volatilitas harian: {_fmt(ht.get('volatility_daily'), '%')}
EPS beat/miss streak: {_fmt(ht.get('earnings_beat_miss_streak'))}

DATA -- KEPEMILIKAN:
Institutional ownership: {_fmt(io.get('percentage'), '%')}
Top pemegang saham: {holders_txt}
Aktivitas Form 4 filing (30 hari terakhir): {_fmt(ia.get('buy_count_30d'))} filing

DATA -- ANALYST ESTIMATES:
Target harga: low ${_fmt(ae.get('target_low'))}, mean ${_fmt(ae.get('target_mean'))}, high ${_fmt(ae.get('target_high'))}
Rating konsensus: {_fmt(ae.get('recommendation_key'))}, jumlah analis: {_fmt(ae.get('num_analyst_opinions'))}
Upside dari harga sekarang: {_fmt(pt.get('upside_pct'), '%')}
EPS surprise per kuartal: {eps_txt}
Revenue estimate forward: {rev_est_txt}

DATA -- BERITA TERBARU:
{news_txt}

DATA -- SEC FILINGS TERBARU:
{filings_txt}

DATA -- GOVERNANCE:
Perubahan saham beredar (12 bulan): {_fmt((knowledge.get('governance') or {}).get('shares_outstanding_change_12m'), '%')}
Auditor changes: {len((knowledge.get('governance') or {}).get('auditor_changes') or [])}, Restatements: {len((knowledge.get('governance') or {}).get('restatements') or [])}"""


def generate_narrative(evidence: dict, knowledge: dict) -> dict | None:
    """Call Gemini to narrate `evidence`+`knowledge`. Best-effort — returns
    None on missing key, SDK error, or invalid response; never raises
    (caller falls back to "AI summary not available")."""
    if not GEMINI_API_KEY:
        return None

    ticker = evidence.get("ticker", "?")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = _build_prompt(evidence, knowledge)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            ),
        )
        parsed = json.loads(response.text)
        if not parsed.get("qualitative"):
            return None
        return {
            "qualitative": parsed["qualitative"],
            "quantitative_highlights": parsed.get("quantitative_highlights") or [],
        }
    except Exception as exc:  # noqa: BLE001
        print(f"[ai_narrative:{ticker}] gagal generate: {exc}", file=sys.stderr)
        return None


def load_narrative_cache(path: str | Path) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_narrative_cache(cache: dict[str, dict], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def get_or_generate_narrative(evidence: dict, knowledge: dict, cache_path: str | Path) -> dict:
    """Cache-first lookup keyed by ticker + evidence_date. Returns
    {"qualitative": str|None, "quantitative_highlights": list, "cached": bool,
    "available": bool} — `available` is False only when GEMINI_API_KEY isn't
    configured at all (lets the frontend distinguish "not set up" from
    "generated nothing this time")."""
    ticker = evidence.get("ticker", "?")
    evidence_date = (knowledge.get("metadata") or {}).get("evidence_date")

    cache = load_narrative_cache(cache_path)
    entry = cache.get(ticker)
    if entry and entry.get("evidence_date") == evidence_date and entry.get("qualitative"):
        return {
            "qualitative": entry["qualitative"],
            "quantitative_highlights": entry.get("quantitative_highlights") or [],
            "cached": True,
            "available": True,
        }

    if not GEMINI_API_KEY:
        return {"qualitative": None, "quantitative_highlights": [], "cached": False, "available": False}

    result = generate_narrative(evidence, knowledge)
    if result:
        cache[ticker] = {
            "evidence_date": evidence_date,
            "qualitative": result["qualitative"],
            "quantitative_highlights": result["quantitative_highlights"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_narrative_cache(cache, cache_path)
        return {**result, "cached": False, "available": True}

    return {"qualitative": None, "quantitative_highlights": [], "cached": False, "available": True}
