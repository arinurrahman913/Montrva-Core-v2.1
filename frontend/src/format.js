// Formatters ported from dashboard/alphaforge.html — keep behavior identical.

export function fmtPct(v, digits = 1) {
  return v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

export function fmtNum(v, digits = 1) {
  return v === null || v === undefined ? '—' : v.toFixed(digits)
}

export function fmtMoney(v) {
  if (v === null || v === undefined) return '—'
  const a = Math.abs(v)
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  return `$${v.toFixed(2)}`
}

const OK_VALUES = new Set(['ok', 'high', 'low', 'passed', 'buy', 'strong_buy'])
const WARN_VALUES = new Set(['medium', 'degraded', 'hold'])
const BAD_VALUES = new Set(['low_confidence', 'bad', 'critical', 'high_risk', 'excluded', 'sell', 'strong_sell'])

export function ratingClass(r) {
  const key = String(r).toLowerCase()
  if (OK_VALUES.has(r) || OK_VALUES.has(key)) return 'ok'
  if (WARN_VALUES.has(r) || WARN_VALUES.has(key)) return 'warn'
  if (BAD_VALUES.has(key)) return 'bad'
  return 'neutral'
}

export function clampPct(pct) {
  return Math.max(0, Math.min(100, pct || 0))
}

// Label + alasan detail untuk 4 kategori "kenapa field ini kosong"
// (contracts.py DataAvailability). Field yang None tapi tidak ada di
// field_availability map (data lama sebelum fitur ini) fallback ke
// "unavailable" — lihat AVAILABILITY_INFO.unavailable di bawah.
export const AVAILABILITY_INFO = {
  not_yet_reported: { label: 'Belum dilaporkan', reason: 'Periode berjalan belum dirilis oleh perusahaan.', dot: 'not_yet_reported' },
  unavailable: { label: 'Tidak tersedia', reason: 'Sumber data gagal mengembalikan field ini pada fetch terakhir.', dot: 'unavailable' },
  not_applicable: { label: 'Tidak berlaku', reason: 'Metrik ini memang tidak relevan untuk entity ini.', dot: 'not_applicable' },
  insufficient_data: { label: 'Data belum cukup', reason: 'Sebagian data ada, belum cukup untuk dihitung.', dot: 'insufficient_data' },
}

// Label untuk 4 tag kualitas data (contracts.py DataQuality) — dipakai
// hanya saat field ADA nilainya. Default "verified" kalau field tidak ada
// di field_quality map (mayoritas kasus — raw passthrough dari satu sumber).
// Definisi teknis netral per metrik (§1 Evidence audit notes) — murni
// "ini dihitung dari apa", TANPA interpretasi ("tinggi = bagus" dsb).
// Evidence = fakta, bukan opini — lihat prinsip yang sama di catalyst/peer
// AI prompt neutrality (ai_narrative.py).
export const METRIC_DEFINITIONS = {
  pe_ratio: 'Rasio harga saham terhadap laba per saham (EPS) 12 bulan terakhir.',
  eps: 'Laba bersih dibagi jumlah saham beredar.',
  book_value_per_share: 'Total ekuitas dibagi jumlah saham beredar.',
  gross_margin: 'Laba kotor dibagi revenue.',
  operating_margin: 'Laba operasional dibagi revenue.',
  roe: 'Laba bersih dibagi ekuitas pemegang saham.',
  roa: 'Laba bersih dibagi total aset.',
  current_ratio: 'Aset lancar dibagi liabilitas lancar.',
  quick_ratio: 'Aset lancar (tanpa persediaan) dibagi liabilitas lancar.',
  free_cash_flow: 'Kas dari operasi dikurangi belanja modal (capex).',
  payout_ratio: 'Persentase laba bersih yang dibagikan sebagai dividen.',
  dividend_yield: 'Dividen tahunan dibagi harga saham saat ini.',
  debt_to_equity: 'Total utang dibagi ekuitas pemegang saham.',
  shares_outstanding_change_12m: 'Perubahan jumlah saham beredar dalam 12 bulan terakhir — indikator dilusi.',
}

export const QUALITY_INFO = {
  verified: { label: 'verified', reason: 'Langsung dari sumber primer, tanpa kalkulasi tambahan.' },
  partial: { label: 'partial', reason: 'Sebagian komponen dihitung, sebagian diasumsikan.' },
  estimated: { label: 'estimated', reason: 'Hasil turunan/konsensus, bukan angka aktual dari sumber.' },
  unverified: { label: 'unverified', reason: 'Ada nilai, tapi belum lolos cross-check keandalan.' },
}

// --- Kosakata stance per-modul (Data Contracts D-09) ---
// Tiap modul reasoning punya kosakata sendiri yang TIDAK sebanding lintas
// modul — tapi DI DALAM satu modul urutannya jelas (bullish→bearish). Peta
// ini cuma untuk WARNA/label UI, bukan untuk membandingkan antar modul.
const STANCE_TIER = {
  // multibagger
  ruang_terbuka: 'bull', ruang_sempit: 'neutral', ruang_tertutup: 'bear', ruang_tak_terbaca: 'unreadable',
  // quality_compound
  compounding_kuat: 'bull', compounding_rapuh: 'neutral', bukan_compounder: 'bear', mesin_tak_terbaca: 'unreadable',
  // speculative
  asimetri_berkatalis: 'bull', asimetri_tanpa_katalis: 'neutral', tanpa_asimetri: 'bear', asimetri_tak_terbaca: 'unreadable',
}

export function stanceTier(stance) {
  return STANCE_TIER[stance] || 'neutral'
}

// Warna pill untuk stance: bull=ok(hijau), bear=bad(merah),
// unreadable=neutral(abu), neutral=warn(kuning netral).
export function stanceClass(stance) {
  const t = stanceTier(stance)
  if (t === 'bull') return 'ok'
  if (t === 'bear') return 'bad'
  if (t === 'unreadable') return 'neutral'
  return 'warn'
}

// "ruang_terbuka" -> "Ruang Terbuka"
export function prettyStance(stance) {
  if (!stance) return '—'
  return String(stance)
    .split('_')
    .map((w) => (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ')
}

// Warna pill untuk band confidence (low/medium/high).
export function bandClass(band) {
  if (band === 'high') return 'ok'
  if (band === 'medium') return 'warn'
  if (band === 'low') return 'bad'
  return 'neutral'
}

// Label modul reasoning.
export const MODULE_LABELS = {
  multibagger: 'Multibagger',
  quality_compound: 'Quality/Compound',
  speculative: 'Speculative',
}

// Label human-readable untuk key/field yang tersimpan snake_case di data.
// Mapping eksplisit untuk istilah yang butuh kapitalisasi/akronim khusus;
// selain itu fallback generik (pisah '_', Title Case, rapikan akronim umum).
const LABEL_MAP = {
  // komponen Layer 1
  yield_curve: 'Yield Curve',
  business_cycle_stage: 'Business Cycle',
  market_regime: 'Market Regime',
  liquidity_conditions: 'Liquidity',
  market_breadth: 'Market Breadth',
  volatility_index: 'Volatility Index',
  commodity_signals: 'Commodity Signals',
  sector_rotation: 'Sector Rotation',
  money_flow: 'Money Flow',
  currency_dxy: 'Currency (DXY)',
  macro_calendar: 'Macro Calendar',
  market_sentiment: 'Market Sentiment',
  credit_spread: 'Credit Spread',
  // field evidence yang sering muncul
  distance_to_ma200_pct: 'Distance to MA200 (%)',
  gold_change_30d_pct: 'Gold Change 30D (%)',
  wti_change_30d_pct: 'WTI Change 30D (%)',
  copper_change_30d_pct: 'Copper Change 30D (%)',
  change_30d_pct: 'Change 30D (%)',
  change_90d_pct: 'Change 90D (%)',
  percentile_3y: 'Percentile (3Y)',
  percentile_5y: 'Percentile (5Y)',
  oas_pct: 'OAS (%)',
  oas_bps: 'OAS (bps)',
  oas_change_30d_bps: 'OAS Change 30D (bps)',
  momentum_30d_bps: 'Momentum 30D (bps)',
  leaders: 'Top Leaders',
  laggards: 'Top Laggards',
  pattern: 'Pattern',
  spread_10y_2y: 'Spread 10Y–2Y',
  m2_yoy_pct: 'M2 YoY (%)',
  indpro_yoy_pct: 'Industrial Production YoY (%)',
  gdp_qoq_pct: 'GDP QoQ (%)',
  unemployment_rate: 'Unemployment Rate',
  fed_balance_sheet_change: 'Fed Balance Sheet Δ',
  pct_above_ma200: '% Above MA200',
  universe_size: 'Universe Size',
  median_5y: 'Median (5Y)',
  score_0_100: 'Score (0–100)',
  // key_metrics reasoning modules
  net_margin_q4: 'Net Margin',
  current_ratio: 'Current Ratio',
  debt_to_equity: 'Debt/Equity',
  return_1y: 'Return 1Y',
  pe_ratio: 'P/E',
  pb_ratio: 'P/B',
  pe_peer_percentile: 'P/E Peer Percentile',
  volatility_daily: 'Volatility (Daily)',
  institutional_pct: 'Institutional %',
  insider_transactions: 'Insider Transactions',
  next_catalyst: 'Next Catalyst',
  insider_filing_activity_30d: 'Insider Filing Activity (30D)',
  tam_estimate: 'TAM Estimate',
  segment_growth: 'Segment Growth',
  acceleration_signal: 'Acceleration Signal',
  revenue_growth_peer_percentile: 'Revenue Growth Peer Percentile',
}

// key_metrics values are a mix of number/string — format numbers compactly,
// leave strings (e.g. next_catalyst: "earnings@2026-08-05") as-is.
export function fmtMetricValue(v) {
  if (v === null || v === undefined) return '—'
  if (typeof v !== 'number') return String(v)
  return Number.isInteger(v) ? String(v) : v.toFixed(2)
}

// Kalimat pertama dari longBusinessSummary (data asli Yahoo, bukan
// parafrase) — dipakai sebagai teaser di header, full text tetap ada di
// balik "baca selengkapnya".
export function firstSentence(text) {
  if (!text) return null
  const match = text.match(/^.*?[.!?](?=\s|$)/)
  return match ? match[0] : text
}

export function fmtCompact(v) {
  return v === null || v === undefined ? '—' : v.toLocaleString()
}

// Polyline SVG points buat sparkline dari price_history (array {close}) —
// sample turun ke ~N titik biar path-nya nggak terlalu padat, normalized ke
// viewBox width x height.
export function sparklinePoints(bars, width = 300, height = 56, maxPoints = 30) {
  if (!bars || bars.length < 2) return ''
  const step = Math.max(1, Math.floor(bars.length / maxPoints))
  const sample = []
  for (let i = 0; i < bars.length; i += step) sample.push(bars[i].close)
  if (sample[sample.length - 1] !== bars[bars.length - 1].close) sample.push(bars[bars.length - 1].close)
  const lo = Math.min(...sample)
  const hi = Math.max(...sample)
  const range = hi - lo || 1
  return sample
    .map((c, i) => {
      const x = (i / (sample.length - 1)) * width
      const y = height - ((c - lo) / range) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

const ACRONYMS = { pct: '%', ma: 'MA', vix: 'VIX', dxy: 'DXY', wti: 'WTI', oas: 'OAS', gdp: 'GDP', m2: 'M2', spx: 'SPX', bps: 'bps', yoy: 'YoY', qoq: 'QoQ' }

export function prettyLabel(key) {
  if (key == null) return '—'
  const raw = String(key)
  if (LABEL_MAP[raw]) return LABEL_MAP[raw]
  return raw
    .split('_')
    .map((w) => ACRONYMS[w] || (w ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(' ')
}
