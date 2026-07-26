# AlphaForge v2: Complete Fund Manager Guide

**Platform untuk Investment Decision Intelligence**

---

## 📍 Pengenalan Cepat untuk Fund Manager

AlphaForge v2 adalah sistem analisis investasi yang mengintegrasikan:
- **Macro Market Context** (bagaimana kondisi pasar keseluruhan)
- **Individual Stock Analysis** (karakteristik & kesehatan setiap saham)
- **Comparative & Risk Assessment** (posisi relatif & warning signs)
- **Reasoning Engine** (argumen buy/hold/sell yang transparan)

**Tujuan:** Membantu fund manager membuat keputusan investasi yang data-driven dengan transparansi penuh tentang **bagaimana sistem sampai pada rekomendasi**.

---

## 🏗️ Arsitektur Sistem: 7 Layer

```
DATA SOURCES (Yahoo Finance, FRED, SEC, Finnhub)
        ↓
LAYER 1: Market Context Engine (Makro 13 Komponen)
        ↓
LAYER 2A: Per-Ticker Evidence Collection
        ↓
LAYER 2B: Knowledge Profile Building (7 Section)
        ↓
LAYER 2C-E: Peer Comparison, Risk Assessment, Confidence Scoring
        ↓
LAYER 2F: Reasoning Engine (3 Independent Investment Lenses)
        ↓
LAYER 3: Aggregator (Synthesis & Final Signals)
```

---

## 🌍 LAYER 1: Market Context Engine

### Apa Itu?
**Makro backbone** platform. Mengukur 13 dimensi kesehatan pasar global untuk menjawab:
- Apakah pasar sedang bullish atau bearish?
- Seberapa besar risiko sekarang?
- Sektor/saham apa yang sedang mendapat dukungan makro?

### 13 Komponen & Artinya untuk Fund Manager:

#### **A. Yield Curve (Kurva Hasil Obligasi)**
- **Apa:** Perbedaan suku bunga 10-year vs 2-year US Treasury
- **Range Normal:** +0.5% hingga +2%
- **Interpretasi:**
  - **Positif (curam):** Pasar percaya pertumbuhan jangka panjang stabil → **Risk-On, prefer equities**
  - **Flat/Inverted:** Sinyal resesi potensial → **Risk-Off, prefer bonds & defensive stocks**
- **Untuk Portfolio Anda:** Jika positif & curam, bias equity bisa lebih agresif. Jika mendatar/terbalik, pertahankan cash buffer.

#### **B. Credit Spread (OAS High-Yield)**
- **Apa:** Selisih yield antara obligasi korporat berisiko tinggi vs Treasury
- **Range Normal:** 300-600 bps (basis points)
- **Interpretasi:**
  - **Tight (< 350 bps):** Pasar optimis, bersedia ambil risiko → **Favor growth stocks, leverage okayed**
  - **Wide (> 600 bps):** Pasar khawatir, risk aversion tinggi → **Favor dividend/defensive, reduce leverage**
- **Untuk Portfolio Anda:** Tight spreads = good time to buy cyclicals (Tech, Discretionary). Wide spreads = rotate to Staples, Utilities.

#### **C. Business Cycle**
- **Apa:** Fase ekonomi (Early Recovery → Mid-cycle → Late → Contraction)
- **Ukuran:** GDP growth YoY, Industrial Production, Unemployment rate
- **Interpretasi per fase:**
  - **Early Recovery:** Growth accelerating, rates low → Favor small-cap growth, cyclical sectors
  - **Mid-Cycle:** Steady growth, rates rising → Prefer large-cap quality
  - **Late:** Growth slowing, margins compressing → Rotate to defensive, reduce leverage
  - **Contraction:** Recession → Bonds/cash, avoid cyclicals
- **Untuk Portfolio Anda:** Adjust sector weights & stock selection based on cycle.

#### **D. Market Regime** 
- **Apa:** Tren utama pasar (Uptrend, Sideways, Downtrend) + Support/Resistance levels
- **Interpretasi:**
  - **Uptrend:** Momentum bullish → Favor momentum strategies, reduce hedges
  - **Sideways:** Mean-reversion opportunities → Buy dips, sell rallies
  - **Downtrend:** Momentum bearish → Increase hedges, reduce long exposure
- **Untuk Portfolio Anda:** Determines tactical allocation (over/underweight equities).

#### **E. Volatility Index (VIX)**
- **Apa:** Fear gauge pasar. 30-day implied volatility dari S&P 500 options
- **Range Normal:** 10-20
- **Interpretasi:**
  - **Low (< 15):** Complacency, potential for surprise moves
  - **Moderate (15-25):** Normal risk environment
  - **High (> 30):** Panic, capitulation. Historically good buying opportunity for long-term investors
- **Untuk Portfolio Anda:** Kalau VIX tinggi, aggressive investors buy dips. Kalau VIX rendah, take profits atau reduce size.

#### **F. Sector Rotation**
- **Apa:** Relative strength dari 11 sektor S&P 500 vs SPY
- **Kelompok:** Tech, Healthcare, Finance, Energy, Industrials, Consumer, Utilities, Real Estate, Materials, Communication, Defensive (Staples)
- **Interpretasi:** 
  - Leader sector (e.g., Tech +15% vs SPY) = flow ke sana. 
  - Laggard sector (e.g., Utilities -8% vs SPY) = atau oversold opportunity atau structural headwind
- **Untuk Portfolio Anda:** Overweight leaders, or contrarian play laggards kalau punya conviction.

#### **G-M. Liquidity, Currency (DXY), Commodities, Market Breadth, Money Flow, Macro Calendar, Market Sentiment**
- Masing-masing adalah supporting indicator untuk mengonfirmasi atau challenge thesis makro.

### **Layer 1 Output untuk Fund Manager:**
- **Layer Score (0-100):** Synthesized risk gauge
  - **60-100 (Risk-On):** Favor equities, growth sectors, leverage OK
  - **40-60 (Neutral):** Balanced portfolio
  - **0-40 (Risk-Off):** Defensive positioning, reduce exposure
- **Confidence Score:** Berapa persen komponen yang datanya "OK" (vs degraded/missing)

**⚠️ Keputusan Fund Manager:** Apakah allocation bucket saya (Equity %, Bond %, Cash %) sebaiknya bergeser?

---

## 🔍 LAYER 2A-B: Per-Ticker Evidence & Knowledge Profile

### Apa Itu?
Untuk setiap saham yang lolos screening, platform mengumpulkan:
1. **Evidence** (Data mentah: price, fundamentals, news)
2. **Knowledge Profile** (Data tersintesis jadi 7 section investor-friendly)

### 7 Sections Knowledge Profile:

#### **1️⃣ IDENTITY**
- Sektor, industri, size (micro/small/mid/large/mega)
- IPO status, ADR flag
- Saham apa yang sedang aku analisis?

#### **2️⃣ FINANCIAL HEALTH** 
Menjawab: "Apakah bisnisnya sehat?"
- **Margins:** Gross/Operating/Net margin trends (YoY)
  - Naik = margin power meningkat (good)
  - Turun = pricing power menurun atau cost naik (concern)
- **Balance Sheet:** Liquidity (current ratio), Solvency (D/E), Working capital
- **Cash Flow:** Operating cash flow, Free cash flow, CAPEX as % revenue

**Untuk Fund Manager:**
- Healthy margins + positive FCF = bisa bayar dividen/buyback, sustainable
- Declining margins + high debt = distress watch
- Strong FCF vs earnings = quality (cash = reality, earnings = accounting)

#### **3A️⃣ COMPETITIVE STRUCTURE**
- Business model & revenue streams
- TAM (Total Addressable Market) estimate
- Moat (competitive advantage): brand, switching costs, scale, IP
- vs competitors: market share, positioning

**Untuk Fund Manager:**
- Wide moat = pricing power, defensible → OK untuk premium valuation
- No moat = commodity business, vulnerable to competition → Need cheaper valuation

#### **3B️⃣ COMPETITIVE MOMENTUM**
- Revenue growth per segment (last 4 quarters)
- Guidance vs reality (beat/miss track record)
- Growth acceleration/deceleration signals
- Market share trends

**Untuk Fund Manager:**
- Accelerating growth = momentum play (growth investors buy)
- Decelerating growth = value trap or mature company (need discount)
- Consistent beats = management credibility high

#### **4️⃣ HISTORICAL TREND**
- Returns: 1Y, 3Y, 5Y performance
- Volatility, drawdowns, Sharpe ratio
- Beta (systematic risk vs market)

**Untuk Fund Manager:**
- High volatility = risk, need compensating return
- Beta < 1 = defensive (good for risk-averse)
- Beta > 1 = aggressive (amplifies market moves)

#### **5️⃣ OWNERSHIP STRUCTURE**
- Institutional ownership % & top holders
- Insider ownership & recent insider buys/sells
- Insider confidence signal (insiders buying = bullish)

**Untuk Fund Manager:**
- High insider ownership = aligned incentives, founder-led often good
- Recent insider sells = potential warning (atau tax planning, context matters)

#### **6️⃣ VALUATION METRICS**
- P/E ratio (price per $1 earnings)
- P/S ratio (price per $1 sales)
- P/B ratio (price per $1 book value)
- FCF yield (free cash flow / market cap)
- PEG (P/E / earnings growth rate)
- vs historical average & vs peer group

**Untuk Fund Manager:**
- Low P/E + high growth = bargain
- High P/E + slowing growth = momentum fading
- FCF yield > bond yield = attractive for income

#### **7️⃣ GOVERNANCE & RED FLAGS**
- Auditor changes, restatements, litigation
- Insider selling, share dilution
- Board composition, CEO tenure
- Related party transactions

**Untuk Fund Manager:**
- Governance red flags = discount rate should increase (risk premium)
- Strong governance = trust score higher

---

## 📊 LAYER 2C: Peer Comparison

### Apa Itu?
Bandingkan setiap saham vs peer group (competitors di sektor sama).

### Metrics yang Dibandingkan:
- **Valuation:** P/E, P/S, P/B, FCF yield
- **Profitability:** Gross/Op/Net margin, ROE, ROA
- **Leverage:** Debt/Equity, Interest coverage
- **Growth:** Revenue growth %, earnings growth %

### Interpretasi untuk Fund Manager:

**Contoh: AAPL vs Tech Peers**
- AAPL P/E = 40x → **65th percentile** (pricier than 65% of peers)
- AAPL ROE = 85% → **92nd percentile** (lebih profitable dari 92% peers)
- AAPL D/E = 0.8 → **45th percentile** (lebih konservatif dari rata-rata)

**Artinya:** AAPL mahal tapi justified karena profitabilitas sangat tinggi & balance sheet solid.

**Fund Manager Decision:**
- Kalau peers juga sedang grow, expensive valuation masih OK (relative expensive = less bad)
- Kalau peers cheaper, AAPL lebih risky (margin of safety rendah)

---

## ⚠️ LAYER 2D: Risk & Red Flags Assessment

### Apa Itu?
Deteksi 6 jenis warning signs yang eksplisit:
1. **Dilution 12m** — Shares outstanding naik drastis (shareholder dilution)
2. **Auditor Change 3y** — Pergantian auditor (potential red flag: did old auditor disagree?)
3. **Restatement 2y** — Prior earnings restatement (earnings quality concern)
4. **Material Litigation** — Ongoing lawsuits (uncertain liability)
5. **Insider Selling 90d** — Officers/directors selling their own stock (lost confidence?)
6. **Fraud or Delisting Risk** — Company under regulatory scrutiny

### Severity Levels:
- **Ekstrem (triggered):** Problem confirmed, clear evidence
- **Tinggi (triggered):** Significant warning
- **Ekstrem/Tinggi (undetermined):** Data belum bisa dipastikan (might or might not be true)

### Interpretasi untuk Fund Manager:
- **Ekstrem + triggered = STOP.** Hard gate di level ini (tidak lanjut ke downstream analysis)
- **Tinggi + triggered = DISCOUNT.** Terima saja tapi naikkan discount rate / risk premium
- **Undetermined = WATCH.** Monitor, tapi jangan pass judgment sampai data jelas

---

## 💡 LAYER 2E: Confidence Scoring

### Apa Itu?
Berapa persentase **confidence** bahwa analysis di atas akurat & lengkap?

### Sumber Uncertainty:
- **Data Coverage:** % field yang filled vs total required
- **Data Freshness:** Apakah data segar (< 1 hari) atau stale (> 30 hari)?
- **Peer Penalty:** Kalau peer group kecil (< 3 tickers), percentile kurang reliable
- **Context Penalty:** Kalau Layer 1 market conditions degraded, micro-level analysis less trustworthy
- **Recency Penalty:** Kalau data > 90 hari old, discount confidence

### Score Interpretation:
- **80-100%:** High confidence, use for sizing decisions
- **60-80%:** Medium confidence, cross-check with other sources
- **< 60%:** Low confidence, avoid or use small position size

**Fund Manager Decision:** Confidence score determines position size & monitoring frequency.

---

## 🧠 LAYER 2F: Reasoning Engine - 3 Independent Lenses

### Apa Itu?
Platform mengevaluasi setiap saham dari **3 perspektif berbeda** (D-09: Independent Lenses):
1. **Multibagger Lens** ("Ada ruang pertumbuhan 3-10x?")
2. **Quality/Compounder Lens** ("Apakah ini mesin compounding andal?")
3. **Speculative Lens** ("Ada asimetri risiko dengan katalis?")

**Kenapa 3 lensa?** 
- Berbeda investor punya different objectives
- Multibagger cari home runs, Quality cari steady compounders, Speculative cari lottery tickets
- Platform objektif: biarkan investor pilih lens sesuai style mereka

### Lens 1: MULTIBAGGER

**Scope:** Memproyeksikan saham bisa grow 3x-10x dalam 3-5 tahun

**Menjawab:**
- Seberapa besar TAM (total addressable market)?
- Bisa saham achieve market leadership?
- Growth trajectory sustainable?
- Valuation give room untuk multiple expansion?

**Stance Options:**
- 🟢 **Ruang Terbuka** (highest growth potential)
- 🟡 **Ruang Sempit** (limited growth room, mature company)
- 🔴 **Ruang Tertutup** (no growth, declining industry)
- ⚫ **Ruang Tak Terbaca** (data insufficient)

**Input yang Dipakai:**
- Revenue growth trajectory
- TAM size
- Competitive position
- Valuation (P/E, PEG)
- Management execution track record
- Sector rotation (bias from Layer 1)

**Fund Manager Action If Ruang Terbuka:**
- Tailor position size untuk growth objective
- Monitor quarterly earnings for execution
- Set stop-loss jika thesis breaks down

### Lens 2: QUALITY/COMPOUNDER

**Scope:** Apakah saham bisa generate consistent, compounding returns 15-20% annually?

**Menjawab:**
- Apakah margins sustainable & stable?
- Bisa reinvest earnings dengan ROE > cost of capital?
- Balance sheet solid untuk weather downturns?
- Management bisa execute konsisten?

**Stance Options:**
- 🟢 **Compounding Kuat** (excellent compounder quality)
- 🟡 **Compounding Rapuh** (some compounding traits, but risks)
- 🔴 **Bukan Compounder** (cyclical or deteriorating quality)
- ⚫ **Mesin Tak Terbaca** (data insufficient)

**Input yang Dipakai:**
- Net margin trend (4 quarter history)
- ROE/ROA absolute & trend
- Debt level & FCF
- Balance sheet ratios
- Historical valuation sustainability
- Earnings beat/miss rate

**Fund Manager Action If Compounding Kuat:**
- OK untuk hold long-term
- Lower monitoring frequency
- Less need for stop-loss (thesisnya compound, bukan momentum)

### Lens 3: SPECULATIVE

**Scope:** Ada event/catalyst yang bisa unlock value 2-3x dalam 6-18 bulan?

**Menjawab:**
- Seberapa jelas catalystnya (earnings catalyst, FDA approval, M&A, etc)?
- Market masih underestimate opportunity?
- Upside/downside asymmetry favorable?
- What could go wrong? (tail risks)

**Stance Options:**
- 🟢 **Asimetri Berkatalis** (clear catalyst, good upside asymmetry)
- 🟡 **Asimetri Tanpa Katalis** (asymmetry exists, but catalyst unclear)
- 🔴 **Tanpa Asimetri** (symmetric risk, no edge)
- ⚫ **Asimetri Tak Terbaca** (data insufficient)

**Input yang Dipakai:**
- Upcoming catalyst timing & certainty
- Volatility (options implied vol)
- Recent insider activity
- News/analyst sentiment
- Historical event-day moves
- Risk flags (litigation, FDA delays, etc)

**Fund Manager Action If Asimetri Berkatalis:**
- Position size bisa lebih aggressive (risk/reward favorable)
- Set catalyst date & exit plan
- Monitor news daily, not quarterly

---

## 🎯 LAYER 3: Aggregator - Synthesis & Final Signal

### Apa Itu?
Platform **mensintesis** 3 lensa independent jadi actionable investment recommendation.

### Output Aggregator:

#### **A. Synthesis Status**
- **Konvergen (3 lensa searah):** Semua lensa bullish → Strong signal
- **Divergen (lensa berlawanan):** Multibagger bullish tapi Quality bearish → Nuanced, need deeper research

#### **B. Bull Case & Bear Case**
Masing-masing lens contribute arguments:
- **Bull Case (consensus strongest positive):** Sintesis pro-argument terkuat
- **Bear Case (consensus strongest negative):** Sintesis contra-argument terkuat

**Fund Manager Use:** Jangan buta. Pahami kedua sisi sebelum keputusan.

#### **C. Confidence Score**
- **High (80%+):** Data lengkap, analysis solid → okayed untuk sizing
- **Low (< 60%):** Missing data, ambiguity → reduce size or pass

#### **D. Risk Profile**
- Summary dari red flags (audit issues, litigation, etc)
- Adjustments ke discount rate

#### **E. Surprise Factor (Optional)**
Kalau historical momentum vs current assessment diverges:
- Surprise positive: Stock beaten down but fundamentals strong → Contrarian upside
- Surprise negative: Stock run-up but momentum fading → Dangerous position

---

## 📋 LAYER 4: Tracking & Historical

### Apa Itu?
Platform tracks:
- Recommendation vs actual stock price
- Thesis breach (e.g., multibagger thesis broken if revenue growth turns negative)
- Portfolio performance under different market regimes

### Untuk Fund Manager:
- Backtest strategies: "Jika saya hanya beli saham dengan Multibagger + Strong Confidence, performance berapa?"
- Thesis management: Kapan harus exit karena thesis break?

---

## 🚀 How to Use AlphaForge as Fund Manager

### Step 1: Check Layer 1 (Macro Context)
- Navigate to **Market > Layer 1 — Context**
- Read Layer Score & band label (Risk-On / Neutral / Risk-Off)
- Scan the 13 components for drivers & drags
- **Decision:** Adjust top-level allocation (Equity %, Bond %, Cash %)

### Step 2: Navigate to Your Watchlist / Screening Results
- Click **Fase A — Per Ticker > Screening** untuk lihat universe
- Atau click specific layer (Evidence, Knowledge, Catalyst, Peer Comparison, Risk/Red Flags, Reasoning)

### Step 3: Deep-Dive a Stock (Open Modal)
- Click any ticker row
- Modal opens with:
  - Decision Flow (which stages passed)
  - Bull Case / Bear Case from 3 lenses
  - Weighted arguments (apa faktor terberat?)
  - Traceability (knowledge field → evidence source)
  - Specific metrics (Peer percentile, Red flags, Confidence band)

### Step 4: Make Decision
Use the **Reasoning — 3 Lensa** page:
- Compare Multibagger / Quality / Speculative stances for each stock
- Align with fund objective:
  - Growth fund? Focus Multibagger stances
  - Value/Income fund? Focus Quality stances
  - Hedge fund? Focus Speculative stances
- Check confidence score & risk flags
- Size position accordingly

### Step 5: Monitor
- Set alert thresholds (e.g., if K/B D/E > 2.0, time to review)
- Track thesis: "If revenue growth turns negative, exit"
- Quarterly: Update Knowledge profile, reassess Peer percentile

---

## 📊 Key Metrics Reference for Fund Managers

### Valuation Metrics
| Metric | < Cheap | Normal | > Expensive |
|--------|----------|--------|------------|
| P/E Ratio | < 12x | 12-20x | > 20x |
| P/S Ratio | < 1.0x | 1-3x | > 3x |
| P/B Ratio | < 1.0x | 1-3x | > 3x |
| PEG Ratio | < 1.0 | ~1.0 | > 1.5 |
| FCF Yield | > 8% | 5-8% | < 5% |

### Quality Metrics
| Metric | Weak | Good | Strong |
|--------|------|------|--------|
| Gross Margin | < 20% | 20-40% | > 40% |
| Net Margin | < 5% | 5-15% | > 15% |
| ROE | < 10% | 10-20% | > 20% |
| D/E Ratio | > 2.0 | 0.5-1.5 | < 0.5 |
| Current Ratio | < 1.0 | 1.5-2.5 | > 2.5 |

### Growth Metrics (Good signs)
- Revenue growth > GDP growth (usually > 3%)
- Earnings growth > 15% annually
- FCF growing faster than earnings (quality)
- Expanding margins (operating leverage)

### Risk Metrics (Watch)
- Gross margin declining > 500 bps
- D/E creeping above 2.0
- FCF turning negative while earnings positive
- Insider selling acceleration
- Audit changes or restatements

---

## 🎓 Investment Framework Examples

### Framework 1: Growth at Reasonable Price (GARP)
1. Filter: Multibagger Ruang Terbuka + Quality Compounding Kuat
2. Valuation: P/E 20-35x, PEG < 1.5, FCF positive
3. Growth: Revenue > 15%, earnings > 20%
4. Confidence: > 75%
5. Risk: No Ekstrem red flags

### Framework 2: Deep Value
1. Filter: Quality Compounding Kuat + Valuation < 15x P/E
2. Low institutional ownership (< 30%)
3. Peer percentile: < 20th on valuation (deeply discounted)
4. Confidence: > 70%
5. Risk: Accept Tinggi red flags if thesis sound

### Framework 3: Catalyst Play
1. Filter: Speculative Asimetri Berkatalis
2. Catalyst clear & dated (e.g., "FDA decision in Q3 2026")
3. Risk/Reward: Minimum 2:1 (2% upside per 1% downside)
4. Position size: Smaller (higher risk)
5. Exit trigger: Catalyst date + 1-2 weeks (realized or failed)

### Framework 4: Dividend Compounder
1. Filter: Quality Compounding Kuat + dividend yield > 3%
2. Payout ratio < 50% (sustainable)
3. FCF > dividend (funded from operations, not debt)
4. Sector: Utilities, REITs, Staples, Healthcare
5. Monitor: D/E, dividend coverage ratio

---

## ⚠️ Common Pitfalls for Fund Managers

### Pitfall 1: Ignoring Confidence Score
- Position size on a Low Confidence stock same as High Confidence = reckless
- **Fix:** Confidence score should drive position sizing, not just thesis conviction

### Pitfall 2: Divergent 3 Lenses = Red Flag
- If Quality says "Compounder broken" but Multibagger says "10x growth room", something's wrong
- **Fix:** Use divergence as signal to dig deeper, not dismiss

### Pitfall 3: Trusting Peer Percentile Blindly
- Peer group might all be overvalued (sector-wide bubble)
- **Fix:** Compare peer percentile vs historical (5-year average)

### Pitfall 4: Missing Thesis Breach
- Multibagger thesis = "3x revenue growth". If growth turns to negative, still holding = mistake
- **Fix:** Set hard rules for thesis exit in advance

### Pitfall 5: Layer 1 Macro Ignored
- Buying growth stocks in Risk-Off regime = fighting macro headwind
- **Fix:** Respect Layer 1 signal even if stock-level bullish

---

## 🔄 Integration with Your Fund Process

**AlphaForge should be:**
- ✅ First pass filter (is this worth analyzing further?)
- ✅ Data aggregation (all relevant facts in one place)
- ✅ Devil's advocate (bull case + bear case forces balanced thinking)
- ✅ Monitoring dashboard (track thesis health over time)
- ❌ NOT replacement for manager judgment (final call always with human)

**Suggested workflow:**
1. **Idea generation:** Screening page + your networks / ideas
2. **Initial filter:** Does this pass Layer 1 (macro) + screening criteria?
3. **Deep dive:** Open modal, review all 7 knowledge sections + 3 lenses
4. **Position sizing:** Based on Confidence + Risk flags + your allocation
5. **Monitoring:** Set quarterly review dates, watch for thesis breach
6. **Exit:** When thesis breaks or risk/reward flips negative

---

## 📞 Questions to Ask When Evaluating a Stock in AlphaForge

### Pre-Purchase:
1. **Layer 1:** Is macro environment favorable for this thesis?
2. **Peers:** How does this compare to sector median on key metrics?
3. **Confidence:** Is data quality sufficient for my position size?
4. **Risk Flags:** Any Ekstrem red flags? Tinggi flags priced in?
5. **3 Lenses:** Which lens aligns with my fund objective?
6. **Bull vs Bear:** Have I truly considered the bear case?

### Quarterly Monitoring:
1. **Thesis check:** Revenue/earnings/margin trend vs expectation?
2. **Valuation:** P/E moved—justified by fundamentals or multiple expansion?
3. **Peer percentile:** Relative performance vs peers—moat widening or narrowing?
4. **Red flags:** Any new warnings emerged?
5. **Confidence:** Data still fresh? Or becoming stale?
6. **Allocation:** Market move changed position weight—rebalance?

### Exit Decision:
- Thesis broken (e.g., "growth compounder" now has negative earnings growth)
- Risk/reward flipped (downside > upside in current regime)
- Better opportunities elsewhere (opportunity cost)
- Conviction dropped below threshold
- Position reached profit target

---

## Summary: AlphaForge Powers Fund Managers With

✅ **Data Clarity:** All relevant facts aggregated, one place  
✅ **Macro Integration:** Stock-level analysis tied to market context  
✅ **Perspective Diversity:** 3 independent lenses = avoid groupthink  
✅ **Risk Awareness:** Red flags surfaced, confidence scored  
✅ **Thesis Transparency:** Bull + bear case laid out explicitly  
✅ **Peer Relativity:** Know if you're buying cheap or expensive vs peers  
✅ **Traceability:** Every claim traced to source (reproducible)  

**Goal:** Faster, better informed decisions. Systematic rather than anecdotal.

---

*Version 1.0 | Created after comprehensive AlphaForge v2 analysis*
