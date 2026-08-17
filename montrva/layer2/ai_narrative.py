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
cached to disk keyed by ticker + KnowledgeMetadata.evidence_date +
PROMPT_VERSION so re-opening the same ticker doesn't re-call the API unless
its underlying Evidence/Knowledge data — or the prompt itself — changed.

Free tier (aistudio.google.com/apikey). Gracefully degrades — returns None,
never raises — if GEMINI_API_KEY isn't set, same convention as
sources/finnhub.py's FINNHUB_API_KEY.
"""
from __future__ import annotations

import json
import os
from ..json_safe import write_text_atomic
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
        "catalyst_note": {"type": "STRING", "nullable": True},
        "peer_note": {"type": "STRING", "nullable": True},
    },
    "required": ["qualitative", "quantitative_highlights"],
}

PEER_METRIC_LABELS = {
    "pe_ratio": "P/E", "ps_ratio": "P/S", "pb_ratio": "P/B", "fcf_yield": "FCF Yield",
    "gross_margin": "Gross margin", "operating_margin": "Operating margin", "net_margin": "Net margin",
    "revenue_growth": "Revenue growth", "roe": "ROE", "roa": "ROA", "debt_to_equity": "Debt/Equity",
}


# Dinaikkan setiap kali isi/format _build_prompt berubah dengan cara yang
# membuat narasi lama SALAH (bukan sekadar beda gaya). Cache narasi dikunci
# ticker + evidence_date; tanpa penanda ini, entri lama tetap terpakai walau
# angkanya dihasilkan dari prompt yang keliru. v2: skala pecahan->persen
# (ROE/ROA/margin/institutional ownership) + kunci news/filings + arah Form 4.
# v3: Debt/Equity (Yahoo memberi satuan persen, 67.156 = 0.67x).
PROMPT_VERSION = 3


def _fmt(value, suffix: str = "") -> str:
    if value is None:
        return "tidak tersedia"
    if isinstance(value, float):
        return f"{value:.1f}{suffix}"
    return f"{value}{suffix}"


def _fmt_frac_pct(value) -> str:
    """Untuk field yang disimpan sebagai PECAHAN (0.486 = 48.6%).

    Evidence menyimpan dua skala berbeda dan itu tidak terlihat dari nama
    field-nya: `fundamental.roe/roa/gross_margin/operating_margin` dan
    `institutional_ownership.percentage` adalah pecahan mentah dari Yahoo,
    sementara `dividend_yield`, `analyst_estimates.surprise_pct/growth`, dan
    SEMUA turunan Knowledge (yoy_*, net_margin_trend, return_1y, upside_pct)
    sudah dalam satuan persen. Menempelkan "%" ke kelompok pertama tanpa
    dikali 100 pernah membuat prompt menyatakan ROE AAPL "1.5%" (aslinya
    148.8%) dan institutional ownership REPL "1.1%" (aslinya 105.7%) — model
    menyalinnya apa adanya, dan itu memang yang tertulis di prompt."""
    if value is None:
        return "tidak tersedia"
    return f"{value * 100:.1f}%"


def _fmt_de(value) -> str:
    """Debt/Equity: Yahoo melaporkannya dalam SATUAN PERSEN (67.156 = 0.67x),
    dan knowledge.py membaginya 100 sebelum dipakai — terukur pada 322 ticker
    sampel, rasio evidence/knowledge tepat 100.0. Tanpa pembagian yang sama di
    sini, prompt menulis "D/E: 67.2" untuk perusahaan yang kartunya menampilkan
    "0.67x": bukan cuma beda format, beda dua digit."""
    if value is None:
        return "tidak tersedia"
    return f"{value / 100:.2f}x"


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


def _nearest_catalyst_txt(catalyst: dict | None) -> str:
    upcoming = [
        c for c in ((catalyst or {}).get("catalysts") or [])
        if c.get("certainty") in ("scheduled", "expected")
    ]
    if not upcoming:
        return "tidak ada katalis (earnings/event) mendatang dalam 90 hari"
    nearest = min(upcoming, key=lambda c: c["expected_at"])
    return f"{nearest['kind']} pada {nearest['expected_at']} ({nearest['certainty']})"


def _peer_comparison_txt(peer: dict | None) -> str:
    if not peer:
        return "tidak tersedia"
    pg = peer.get("peer_group") or {}
    rows = []
    for key, label in PEER_METRIC_LABELS.items():
        comp = peer.get(f"{key}_comparison")
        if not comp or comp.get("percentile") is None:
            continue
        rows.append(
            f"{label}: {_fmt(comp.get('ticker_value'))} vs median peer {_fmt(comp.get('peer_group_median'))} "
            f"(percentile {_fmt(comp.get('percentile'))})"
        )
    if not rows:
        return f"data peer group ada ({pg.get('group_size', 0)} peer sektor {pg.get('sector') or 'tidak diketahui'}) tapi tidak ada metrik yang bisa dibandingkan"
    header = f"Dibanding {pg.get('group_size', 0)} peer sektor {pg.get('sector') or 'tidak diketahui'}"
    if peer.get("low_sample_size"):
        header += " (CATATAN: jumlah sampel peer kecil, bandingkan dengan hati-hati)"
    return header + ":\n" + "\n".join(rows)


def _insider_activity_txt(ia: dict) -> str:
    """Ringkas aktivitas Form 4 30 hari BESERTA ARAHNYA.

    Sebelumnya prompt cuma mengirim `buy_count_30d` dan melabelinya "filing",
    jadi REPL — 11 transaksi jual, net -175.204 lembar, cukup untuk menyalakan
    flag Risk `insider_selling_90d` — masuk ke model sebagai "0 filing".
    Arahnya sekarang nyata: sec_form4.py mem-parse XML Form 4 (sejak
    2026-08-15), `transaction_type` berisi buy/sell sungguhan."""
    buy = ia.get("buy_count_30d")
    sell = ia.get("sell_count_30d")
    if buy is None and sell is None:
        return "tidak tersedia"
    net = ia.get("net_shares_30d")
    parts = [f"{buy or 0} transaksi beli, {sell or 0} transaksi jual"]
    if net:
        arah = "neto BELI" if net > 0 else "neto JUAL"
        parts.append(f"{arah} {abs(net):,.0f} lembar".replace(",", "."))
    if ia.get("top_buyer"):
        parts.append(f"pembeli terbesar {ia['top_buyer']}")
    if ia.get("top_seller"):
        parts.append(f"penjual terbesar {ia['top_seller']}")
    return ", ".join(parts)


def _build_prompt(evidence: dict, knowledge: dict, catalyst: dict | None = None, peer: dict | None = None) -> str:
    pm = evidence.get("price_market") or {}
    fnd = evidence.get("fundamental") or {}
    quarters = (fnd.get("quarterly_data") or [])[:4]
    io = evidence.get("institutional_ownership") or {}
    top_holders = (io.get("top_holders") or [])[:3]
    ia = evidence.get("institutional_activity") or {}
    # KUNCI "items", bukan "news"/"filings": EvidencePackage.to_dict()
    # (contracts.py) menamai ulang dua list ini saat serialisasi, jadi nama
    # field dataclass-nya cuma berlaku untuk objek in-process. Sebelum ini
    # keduanya selalu terbaca kosong — 757 dari 800 ticker sampel sebenarnya
    # punya berita, dan tak satu pun pernah sampai ke prompt.
    news = ((evidence.get("news") or {}).get("items") or [])[:5]
    filings = ((evidence.get("sec_filings") or {}).get("items") or [])[:5]
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

    catalyst_txt = _nearest_catalyst_txt(catalyst)
    peer_txt = _peer_comparison_txt(peer)

    return f"""Anda asisten analisis keuangan. Berdasarkan data di bawah untuk saham {evidence.get('ticker')} (sektor {fnd.get('sector') or 'tidak diketahui'}), hasilkan:
1. "qualitative": narasi 4-6 kalimat Bahasa Indonesia yang menjelaskan kondisi perusahaan secara objektif untuk investor pemula — gabungkan konteks bisnis, kinerja keuangan, dan berita/filing terbaru kalau relevan.
2. "quantitative_highlights": 5-6 angka PALING PENTING dari data di bawah (pilih sendiri mana yang paling menonjol/relevan untuk ticker ini spesifik — jangan selalu pilih urutan yang sama), format {{label, value}} singkat.
3. "catalyst_note": KALAU ada katalis mendatang (lihat DATA -- KATALIS di bawah), tulis 1-2 kalimat kenapa katalis itu patut diperhatikan, dikaitkan dengan rekam jejak EPS/rating analis/momentum yang relevan. KALAU tidak ada katalis mendatang, isi null -- JANGAN mengarang katalis atau memaksakan catatan.
4. "peer_note": KALAU ada data Peer Comparison (lihat DATA -- PEER COMPARISON di bawah), tulis 2-3 kalimat yang MENYINTESIS pola percentile-nya -- mis. kalau murah di valuasi tapi lemah di profitabilitas dibanding peer, sebutkan trade-off itu secara eksplisit, jangan cuma sebutin ulang angka satu-satu. KALAU data peer tidak tersedia atau tidak ada metrik yang bisa dibandingkan, isi null.

ATURAN WAJIB:
- HANYA gunakan angka/fakta yang diberikan di bawah. JANGAN mengarang data yang tidak ada.
- JANGAN kasih rekomendasi beli/jual/hold — ini penjelasan fakta, bukan saran investasi.
- Soal aktivitas insider Form 4: arah transaksinya (beli/jual) sekarang berasal dari XML Form 4 yang sudah di-parse, jadi boleh disebut apa adanya sebagai "insider menjual"/"insider membeli". TAPI jangan menafsirkannya jadi sinyal — penjualan insider sering karena rencana 10b5-1, pajak vesting, atau kebutuhan pribadi, bukan pandangan atas prospek perusahaan.
- Soal percentile peer: percentile TINGGI belum tentu "lebih baik" (mis. Debt/Equity tinggi = leverage lebih besar, bukan otomatis positif) -- deskripsikan posisi relatifnya, jangan otomatis nilai "bagus/jelek" kecuali arahnya memang jelas dari konteks (mis. FCF yield tinggi memang secara umum positif).
- Kalau suatu data "tidak tersedia", jangan dipaksakan disebut.

DATA -- COMPANY PROFILE:
Nama: {cp.get('long_name')}, Karyawan: {_fmt(cp.get('employees'))}, Lokasi: {cp.get('city')}, {cp.get('country')}
Deskripsi: {cp.get('business_summary') or 'tidak tersedia'}

DATA -- PRICE MARKET:
Harga sekarang: ${_fmt(pm.get('last_price'))}, Market cap: {_fmt_money(pm.get('market_cap'))}, Volume: {_fmt(pm.get('volume'))}
52-week high/low: ${_fmt(pm.get('high_52w'))} / ${_fmt(pm.get('low_52w'))}, Beta: {_fmt(pm.get('beta'))}

DATA -- FUNDAMENTAL:
P/E: {_fmt(fnd.get('pe_ratio'))}, D/E: {_fmt_de(fnd.get('debt_to_equity'))}, Current ratio: {_fmt(fnd.get('current_ratio'))}, Quick ratio: {_fmt(fnd.get('quick_ratio'))}
ROE: {_fmt_frac_pct(fnd.get('roe'))}, ROA: {_fmt_frac_pct(fnd.get('roa'))}, Gross margin: {_fmt_frac_pct(fnd.get('gross_margin'))}, Operating margin: {_fmt_frac_pct(fnd.get('operating_margin'))}
Dividend yield: {_fmt(fnd.get('dividend_yield'), '%')}, Free cash flow: {_fmt_money(fnd.get('free_cash_flow'))}
4 kuartal terakhir (revenue, net income): {quarters_txt}

DATA -- TREN TERHITUNG (dari Knowledge, sudah akurat, jangan dihitung ulang):
Revenue YoY 4 kuartal: Q-3 {_fmt((knowledge.get('financial_health') or {}).get('revenue_trend', {}).get('yoy_q1'), '%')}, Q-2 {_fmt((knowledge.get('financial_health') or {}).get('revenue_trend', {}).get('yoy_q2'), '%')}, Q-1 {_fmt((knowledge.get('financial_health') or {}).get('revenue_trend', {}).get('yoy_q3'), '%')}, Q kini {_fmt((knowledge.get('financial_health') or {}).get('revenue_trend', {}).get('yoy_q4'), '%')}
Net margin kuartal kini: {_fmt((knowledge.get('financial_health') or {}).get('net_margin_trend', {}).get('q4'), '%')}
Return 1 tahun: {_fmt(ht.get('return_1y'), '%')}, Volatilitas harian: {_fmt(ht.get('volatility_daily'), '%')}
EPS beat/miss streak: {_fmt(ht.get('earnings_beat_miss_streak'))}

DATA -- KEPEMILIKAN:
Institutional ownership: {_fmt_frac_pct(io.get('percentage'))}
Top pemegang saham: {holders_txt}
Aktivitas insider Form 4 (30 hari terakhir): {_insider_activity_txt(ia)}

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
Auditor changes: {len((knowledge.get('governance') or {}).get('auditor_changes') or [])}, Restatements: {len((knowledge.get('governance') or {}).get('restatements') or [])}

DATA -- KATALIS:
{catalyst_txt}

DATA -- PEER COMPARISON:
{peer_txt}"""


def generate_narrative(
    evidence: dict, knowledge: dict, catalyst: dict | None = None, peer: dict | None = None
) -> dict | None:
    """Call Gemini to narrate `evidence`+`knowledge`(+`catalyst`+`peer`).
    Best-effort — returns None on missing key, SDK error, or invalid
    response; never raises (caller falls back to "AI summary not
    available")."""
    if not GEMINI_API_KEY:
        return None

    ticker = evidence.get("ticker", "?")
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = _build_prompt(evidence, knowledge, catalyst, peer)
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
            "catalyst_note": parsed.get("catalyst_note"),
            "peer_note": parsed.get("peer_note"),
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
    write_text_atomic(p, json.dumps(cache, indent=2, ensure_ascii=False))


_EMPTY_RESULT = {"qualitative": None, "quantitative_highlights": [], "catalyst_note": None, "peer_note": None}


def get_or_generate_narrative(
    evidence: dict, knowledge: dict, cache_path: str | Path,
    catalyst: dict | None = None, peer: dict | None = None,
) -> dict:
    """Cache-first lookup keyed by ticker + evidence_date. Returns
    {"qualitative": str|None, "quantitative_highlights": list,
    "catalyst_note": str|None, "peer_note": str|None, "cached": bool,
    "available": bool} — `available` is False only when GEMINI_API_KEY isn't
    configured at all (lets the frontend distinguish "not set up" from
    "generated nothing this time"). `catalyst`/`peer` are optional — their
    notes stay null if omitted or not applicable; both stages refresh
    together with Evidence/Knowledge in the same pipeline run, so
    evidence_date alone is still a valid cache fingerprint for them too."""
    ticker = evidence.get("ticker", "?")
    evidence_date = (knowledge.get("metadata") or {}).get("evidence_date")

    cache = load_narrative_cache(cache_path)
    entry = cache.get(ticker)
    # `prompt_version` ikut jadi bagian kunci: entri yang dibuat sebelum
    # perbaikan skala/kunci data di atas angkanya salah, dan evidence_date-nya
    # TIDAK berubah karena datanya memang sama — cuma prompt-nya yang dulu
    # keliru membacanya. Entri lama (tanpa field ini) otomatis dianggap basi.
    if (
        entry
        and entry.get("evidence_date") == evidence_date
        and entry.get("prompt_version") == PROMPT_VERSION
        and entry.get("qualitative")
    ):
        return {
            "qualitative": entry["qualitative"],
            "quantitative_highlights": entry.get("quantitative_highlights") or [],
            "catalyst_note": entry.get("catalyst_note"),
            "peer_note": entry.get("peer_note"),
            "cached": True,
            "available": True,
        }

    if not GEMINI_API_KEY:
        return {**_EMPTY_RESULT, "cached": False, "available": False}

    result = generate_narrative(evidence, knowledge, catalyst, peer)
    if result:
        cache[ticker] = {
            "evidence_date": evidence_date,
            "prompt_version": PROMPT_VERSION,
            "qualitative": result["qualitative"],
            "quantitative_highlights": result["quantitative_highlights"],
            "catalyst_note": result.get("catalyst_note"),
            "peer_note": result.get("peer_note"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_narrative_cache(cache, cache_path)
        return {**result, "cached": False, "available": True}

    return {**_EMPTY_RESULT, "cached": False, "available": True}
