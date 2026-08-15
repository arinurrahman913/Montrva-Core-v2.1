// Formatters ported from dashboard/montrva.html — keep behavior identical.

export function fmtPct(v, digits = 1) {
  return v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`
}

export function fmtNum(v, digits = 1) {
  return v === null || v === undefined ? '—' : v.toFixed(digits)
}

export function fmtMoney(v) {
  if (v === null || v === undefined) return '—'
  const a = Math.abs(v)
  // Tier triliun ditambah saat halaman Aliran Dana Institusi mulai
  // menjumlahkan seluruh populasi ($1.459,80B terbaca sebagai angka acak).
  // Kapitalisasi pasar raksasa (AAPL ~$3T) juga ikut kena dan jadi lebih
  // terbaca, bukan berubah arti.
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  return `$${v.toFixed(2)}`
}

// Rupiah dari nilai dolar + kurs. Mengembalikan null (bukan "Rp —") kalau
// salah satunya tidak ada, supaya pemanggil bisa MENGHILANGKAN barisnya
// daripada memasang tempat kosong yang terbaca seperti nilai nol.
//
// Ringkas di atas sejuta, sejalan dengan fmtMoney yang sudah memakai B/M untuk
// dolar: angka portofolio rupiah punya 8-10 digit, dan deretan digit sepanjang
// itu justru lebih sulit dibaca sekilas daripada "Rp 7,49 jt".
export function fmtIDR(usd, rate) {
  if (usd === null || usd === undefined || !rate) return null
  const v = usd * rate
  const a = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  if (a >= 1e9) return `${sign}Rp ${(a / 1e9).toFixed(2)} mlr`
  if (a >= 1e6) return `${sign}Rp ${(a / 1e6).toFixed(2)} jt`
  return `${sign}Rp ${Math.round(a).toLocaleString('id-ID')}`
}

// 13F wajib dilaporkan institusi (>$100M AUM) paling lambat 45 hari setelah
// kuartal tutup — jadi data kuartal berjalan belum akan ADA di manapun (SEC,
// Yahoo, siapapun) sampai deadline itu lewat, bukan soal cache basi di sisi
// kita. dateReportedStr = tanggal akhir kuartal yang datanya kita punya (mis.
// "2026-03-31"); fungsi ini hitung kapan kuartal BERIKUTNYA wajib dilaporkan.
//
// Tinggal di sini (bukan di dalam satu view) karena dipakai dua permukaan:
// tabel pemegang per ticker di TickerModal dan halaman Aliran Dana Institusi.
// Dua salinan akan diam-diam berbeda begitu salah satunya disentuh.
export function nextFilingDeadline(dateReportedStr) {
  const d = new Date(dateReportedStr + 'T00:00:00Z')
  if (isNaN(d.getTime())) return null
  // Date.UTC(year, month+4, 0) = hari terakhir bulan (month+3) — cara aman
  // hitung "3 bulan lagi, akhir bulan" tanpa overflow kalau tanggal asal
  // (mis. 31) tidak ada di bulan target (mis. Maret 31 -> Juni cuma 30 hari,
  // setUTCMonth naif akan overflow diam-diam ke 1 Juli).
  const nextQuarterEnd = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 4, 0))
  const deadline = new Date(nextQuarterEnd)
  deadline.setUTCDate(deadline.getUTCDate() + 45)
  return deadline
}

// 13F melaporkan POTRET kepemilikan di akhir kuartal, tanpa tanggal
// transaksi — jadi "kapan institusi ini masuk" hanya bisa dijawab sebagai
// JENDELA, bukan tanggal. Pemegang berlabel posisi baru di laporan
// 31 Mar 2026 berarti ia belum ada di laporan sebelumnya, jadi masuknya di
// suatu titik antara 1 Jan dan 31 Mar. Menampilkan satu tanggal tunggal di
// sini akan mengarang presisi yang tidak ada di sumbernya.
const QUARTER_MONTH_NAMES = [
  ['Jan', 'Mar'], ['Apr', 'Jun'], ['Jul', 'Sep'], ['Okt', 'Des'],
]

function parseUtcDate(dateStr) {
  if (!dateStr) return null
  const d = new Date(dateStr + 'T00:00:00Z')
  return isNaN(d.getTime()) ? null : d
}

export function quarterOf(dateStr) {
  const d = parseUtcDate(dateStr)
  if (!d) return null
  return { q: Math.floor(d.getUTCMonth() / 3) + 1, year: d.getUTCFullYear() }
}

export function quarterLabel(dateStr) {
  const q = quarterOf(dateStr)
  return q ? `Q${q.q} ${q.year}` : '—'
}

// "Jan–Mar 2026" — jendela masuk untuk pemegang baru.
export function quarterWindow(dateStr) {
  const q = quarterOf(dateStr)
  if (!q) return '—'
  const [from, to] = QUARTER_MONTH_NAMES[q.q - 1]
  return `${from}–${to} ${q.year}`
}

// Satu saham bisa memuat dua kuartal sekaligus saat laporan kuartal baru
// mulai berdatangan (1.090 saham per run 2026-08-01): sebagian institusinya
// sudah lapor, sebagian belum. Tahun yang sama diringkas ("Q1 + Q2 2026")
// supaya tidak mengulang tahun; tahun berbeda ditulis penuh.
export function quarterLabelSet(dateStrs) {
  const qs = (dateStrs || []).map(quarterOf).filter(Boolean)
  if (qs.length === 0) return '—'
  const seen = []
  for (const q of qs) {
    if (!seen.some((s) => s.q === q.q && s.year === q.year)) seen.push(q)
  }
  seen.sort((a, b) => a.year - b.year || a.q - b.q)
  if (seen.length === 1) return `Q${seen[0].q} ${seen[0].year}`
  const sameYear = seen.every((s) => s.year === seen[0].year)
  if (sameYear) return `${seen.map((s) => `Q${s.q}`).join(' + ')} ${seen[0].year}`
  return seen.map((s) => `Q${s.q} ${s.year}`).join(' + ')
}

// Yahoo pakai pct_change = 100.0 sebagai sentinel "posisi baru" (tidak bisa
// hitung % dari basis 0 saham), bukan literal +100%. Ambang yang sama dipakai
// backend di montrva/layer2/institutional_flow.py — kalau salah satu
// berubah, keduanya harus berubah bersama.
export const NEW_POSITION_SENTINEL = 99.5

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
  debt_to_equity: 'Total utang dibagi ekuitas pemegang saham. Yahoo melaporkannya dalam persen — 79,5 berarti 0,80x. Kartu Knowledge menampilkannya sebagai rasio (x).',
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

// --- Penggerak skor (ModuleOutput.score_breakdown) ---
//
// Kunci breakdown ditulis mentah oleh reasoning.py (`net_margin`, `pe_ratio`,
// `debt_to_equity`, ...). Peta ini dipakai bareng oleh kartu top pick di
// Agregator Pribadi dan panel "Bobot Faktor" di TickerModal supaya keduanya
// tidak pernah menyebut faktor yang sama dengan nama berbeda.
export const FACTOR_LABELS = {
  net_margin: 'Net margin',
  current_ratio: 'Current ratio',
  debt_to_equity: 'Debt/Equity',
  return_1y: 'Return 1 thn',
  pe_ratio: 'P/E',
  pb_ratio: 'P/B',
  pe_vs_peer: 'P/E vs peer',
  analyst_upside: 'Upside analis',
  analyst_rating: 'Rating analis',
  target_trend_3m: 'Tren target 3bln',
  eps_beat_rate: 'EPS beat',
  restatements: 'Restatement',
  auditor_changes: 'Ganti auditor',
  data_confidence: 'Confidence data',
  risk_flags: 'Flag risiko',
}

export function factorLabel(key) {
  return FACTOR_LABELS[key] || prettyLabel(key)
}

// Sumbu batang penggerak skor sengaja TETAP (-15..+15) untuk semua ticker,
// bukan diskalakan ke sumbangan terbesar milik ticker itu sendiri. Kalau
// diskalakan relatif, faktor terkuat selalu memenuhi setengah lebar dan
// panjang batang berhenti berarti apa-apa selain "terkuat di antara temannya
// sendiri" -- tidak bisa dibandingkan antar-ticker, dan faktor berbobot kecil
// terlihat sama besar dengan yang berbobot besar. 15 = bobot maksimum faktor
// terbesar di lensa mana pun (net margin / momentum).
export const FACTOR_AXIS = 15

// score_breakdown format LAMA berisi tepat satu kunci turunan dari totalnya
// sendiri (`{"fundamentals": (score-50)/50}`), bukan sumbangan per faktor.
// File stage di disk masih berformat itu sampai pipeline dijalankan ulang
// dengan reasoning.py yang baru -- kalau tidak dibedakan, blok penggerak akan
// menggambar satu batang tak berarti DAN salah menyimpulkan skornya terklamp
// (karena 50 + 0.64 jelas tidak sama dengan skor sebenarnya) di hampir semua
// ticker. Dideteksi dari bentuknya, bukan dari versi: satu kunci saja, bernama
// salah satu dari tiga kunci lama itu.
const LEGACY_BREAKDOWN_KEYS = new Set(['fundamentals', 'growth', 'momentum'])

export function isLegacyBreakdown(breakdown) {
  const keys = Object.keys(breakdown || {})
  return keys.length === 1 && LEGACY_BREAKDOWN_KEYS.has(keys[0])
}

// Penggerak & penahan terkuat dari score_breakdown, masing-masing sudah
// terurut menurut besar sumbangan. Faktor bersumbangan nol tidak pernah masuk
// breakdown (lihat _record di reasoning.py), jadi tidak perlu disaring lagi.
export function splitFactors(breakdown, maxPositive = 3, maxNegative = 1) {
  const entries = Object.entries(breakdown || {})
  const drivers = entries.filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1])
  const blockers = entries.filter(([, v]) => v < 0).sort((a, b) => a[1] - b[1])
  const shown = drivers.slice(0, maxPositive).concat(blockers.slice(0, maxNegative))
  return {
    shown,
    total: entries.reduce((s, [, v]) => s + v, 0),
    hidden: entries.length - shown.length,
    hasBlocker: blockers.length > 0,
  }
}

// --- Lapisan pribadi (personal layer) -- action/horizon per lens ---
// action TIDAK sebanding lintas modul (sama alasan dengan stance, D-09-
// style) -- kategorinya (warna) yang sebanding, bukan nilai literalnya.
const ACTION_CATEGORY = {
  // menambah eksposur (penuh atau bertahap)
  mulai_posisi: 'masuk', cicil_bertahap: 'masuk', akumulasi: 'masuk', akumulasi_saat_koreksi: 'masuk',
  masuk_spekulatif: 'masuk', tambah: 'masuk', tambah_bertahap: 'masuk',
  // pantau/tahan -- tidak menambah, tidak keluar
  pantau: 'netral', tahan: 'netral', tahan_sampai_katalis: 'netral', tunggu_katalis: 'netral',
  // keluar/hindari
  lewati: 'keluar', kurangi: 'keluar', jual: 'keluar',
}

// "Top pick" pribadi = action entry TERKUAT per lens (bukan skor/ranking
// baru -- cuma filter dari action yang sudah dihitung personal_reasoning.py,
// sama semangatnya dengan BEST_STANCE di AggregatorView.jsx publik/D-04).
// Cuma no_holding: begitu sudah dipegang, itu bukan "ide baru" lagi. Dipakai
// bareng oleh PersonalAggregatorView (nampilin top pick sekarang) dan
// PersonalHistoricalView (filter ke ticker yang PERNAH jadi top pick).
export const BEST_ACTION = {
  multibagger: 'mulai_posisi',
  quality_compound: 'akumulasi',
  // "masuk_spekulatif" -> "siaga_gerakan" (2026-08-05). Lensa ini terukur bisa
  // menemukan saham yang akan bergerak besar (+15,5pp di atas acak, di luar
  // derau) tapi TIDAK terbukti menebak arahnya (+4,2pp, di dalam derau), jadi
  // action-nya berhenti mengklaim arah. Lihat ACTION_CATEGORY_MAGNITUDE di
  // montrva/personal/personal_contracts.py untuk angka lengkapnya.
  speculative: 'siaga_gerakan',
}

// Action "terkuat" per lensa LINTAS WAKTU. Ini bukan shim transisi yang nanti
// dibuang: Riwayat Pribadi menampilkan snapshot historis, dan snapshot dari
// sebelum 2026-08-05 memakai "masuk_spekulatif" — mereka akan ada di riwayat
// selama bertahun-tahun. Frontend harus mengenali kedua nama SELAMANYA, kalau
// tidak seluruh tesis Spekulatif lama hilang dari halaman begitu kosakatanya
// berganti. BEST_ACTION di atas tetap ada untuk "apa yang diproduksi HARI INI".
export const BEST_ACTION_ALIASES = {
  multibagger: new Set(['mulai_posisi']),
  quality_compound: new Set(['akumulasi']),
  speculative: new Set(['siaga_gerakan', 'masuk_spekulatif']),
}

export function isBestAction(module, action) {
  return BEST_ACTION_ALIASES[module]?.has(action) ?? false
}

// Apakah dua action merujuk panggilan YANG SAMA, meski kosakatanya berganti di
// tengah rentang waktu yang dibandingkan. Perbandingan string mentah tidak cukup
// begitu ada penggantian nama: `masuk_spekulatif` -> `siaga_gerakan` (2026-08-05)
// membuat SETIAP streak Spekulatif terbaca "run ke-1" di run pertama sesudahnya,
// padahal panggilannya tidak berubah sama sekali -- terverifikasi atas riwayat
// nyata (AAOI: 9 run berturut-turut terbaca 1).
//
// Sengaja bersandar pada BEST_ACTION_ALIASES, bukan daftar sinonim kedua yang
// terpisah: dua daftar untuk fakta yang sama pasti berbeda cepat atau lambat.
// Konsekuensinya, penggantian nama action NON-terkuat (mis. `tunggu_katalis`)
// tidak tertangani -- kalau itu terjadi, kelompok aliasnya yang harus diperluas,
// di satu tempat ini.
export function sameAction(module, a, b) {
  if (a === b) return true
  if (!a || !b) return false
  const aliases = BEST_ACTION_ALIASES[module]
  return !!aliases && aliases.has(a) && aliases.has(b)
}

// Sejak kapan ACTION ini (bukan cuma ticker-nya) berlaku, DAN sudah berapa run
// berturut-turut: jalan MUNDUR dari entry terbaru di personal_history, berhenti
// begitu action lensa ini beda dari sekarang. Dipakai ThesisProof, yang tampil
// di DUA permukaan (kartu top pick Agregator Pribadi + panel expand Riwayat
// Pribadi) — satu fungsi, bukan dua salinan.
//
// `runs` ada karena lensa Spekulatif terukur sangat lengket: 85–98% pick hari
// ini sama dengan run sebelumnya (empat dari lima input skornya bergerak lambat
// — volatilitas, return 1 tahun, kepemilikan institusi yang cuma berubah
// kuartalan). Tanpa angka ini, tesis yang baru muncul hari ini dan tesis yang
// sudah nongkrong 9 run terbaca persis sama di kartu.
//
// `missing` butuh `runDates` (union tanggal run lintas ticker, dari
// calibration.json): timeline SATU ticker cuma memuat run yang ticker itu ikut
// diskrining, jadi run yang terlewat tidak kelihatan dari timeline itu sendiri.
// Streak sengaja TIDAK diputus oleh run yang terlewat — ticker yang tidak lolos
// screening sehari adalah ketiadaan data, bukan pembalikan sinyal; memutusnya
// akan menulis "run ke-1" untuk tesis yang sebenarnya sudah berumur dua minggu.
export function actionStreak(historyEntries, module, currentAction, runDates) {
  if (!historyEntries || historyEntries.length === 0) return null
  const sorted = [...historyEntries].sort((a, b) => (a.analyzed_at || '').localeCompare(b.analyzed_at || ''))
  let since = null
  let runs = 0
  for (let i = sorted.length - 1; i >= 0; i--) {
    const action = sorted[i]?.personal_call_set?.[module]?.action
    if (!sameAction(module, action, currentAction)) break
    since = sorted[i].analyzed_at
    runs++
  }
  if (!since) return null

  let missing = 0
  if (Array.isArray(runDates) && runDates.length > 0) {
    const from = since.slice(0, 10)
    const to = (sorted[sorted.length - 1].analyzed_at || '').slice(0, 10)
    missing = Math.max(0, runDates.filter((d) => d >= from && d <= to).length - runs)
  }
  return { since, runs, missing }
}

// Action yang mengklaim AMPLITUDO, bukan arah. Harus sama dengan
// ACTION_CATEGORY_MAGNITUDE di personal_contracts.py.
export const MAGNITUDE_ACTIONS = new Set(['siaga_gerakan'])

// Urutan field di dalam tiap elemen `lenses` pada entry historical TIPIS.
// KEMBARAN dari THIN_LENS_FIELDS di montrva/layer2/historical_contracts.py
// — dua-duanya harus berubah bersama. Bentuknya posisional karena nama kunci
// yang diulang 3x per entry sepanjang setahun berharga ratusan MB; harganya
// di sisi ini adalah satu fungsi baca di bawah, bukan urutan yang dihafal di
// tempat pemakaian.
export const THIN_LENS_FIELDS = ['module', 'stance', 'thesis_score', 'confidence_band']

export function thinLens(lens) {
  if (!Array.isArray(lens)) return null
  return Object.fromEntries(THIN_LENS_FIELDS.map((k, i) => [k, lens[i]]))
}

// Satu entry historical dibaca seragam, apa pun bentuknya: entry TERAKHIR tiap
// ticker menyimpan snapshot AggregatorOutput penuh, yang lebih tua cuma bentuk
// tipis (lihat montrva/layer2/historical.py). Tanpa fungsi ini, pemakainya
// harus tahu bentuk mana yang sedang dipegang — dan yang lupa akan menampilkan
// "divergen" untuk SETIAP entry lama, karena `aggregator_output` yang tidak ada
// membaca `undefined` sebagai "tidak konvergen".
export function readHistoricalEntry(entry) {
  if (!entry) return null
  const ao = entry.aggregator_output
  if (ao) {
    const syn = ao.synthesis || null
    return {
      thin: false,
      analyzedAt: entry.analyzed_at,
      halted: ao.halted,
      fullConvergence: syn ? syn.full_convergence : null,
      narrative: syn ? syn.narrative : null,
      surprise: syn ? syn.surprise : null,
      lenses: (ao.module_outputs || []).map((m) => ({
        module: m.module,
        stance: m.stance,
        thesis_score: m.thesis_score,
        confidence_band: m.confidence ? m.confidence.band : null,
      })),
      outcome: entry.outcome,
    }
  }
  return {
    thin: true,
    analyzedAt: entry.analyzed_at,
    halted: entry.halted,
    fullConvergence: entry.full_convergence,
    narrative: null,
    surprise: entry.surprise,
    lenses: (entry.lenses || []).map(thinLens).filter(Boolean),
    outcome: entry.outcome,
  }
}

// Lebar "deru" khas saham ini, dalam persen, untuk jendela sepanjang
// `windowBars` hari bursa. Cerminan window_sigma_pct() di
// personal_evaluation.py: sigma harian dari bar SEBELUM entry (tidak pernah
// dari jendela tesisnya sendiri — itu mengintip masa depan), diskalakan
// akar-waktu. Dipakai kartu tesis untuk menggambar pita ±deru sebagai ganti
// garis target, yang tidak punya arti untuk klaim amplitudo.
export function noiseBandPct(bars, windowBars) {
  if (!bars || bars.length < 21 || !windowBars) return null
  const closes = bars.slice(-60).map((b) => b.close).filter((c) => c > 0)
  if (closes.length < 21) return null
  const rets = []
  for (let i = 1; i < closes.length; i++) rets.push(((closes[i] - closes[i - 1]) / closes[i - 1]) * 100)
  const mean = rets.reduce((s, r) => s + r, 0) / rets.length
  const varr = rets.reduce((s, r) => s + (r - mean) ** 2, 0) / (rets.length - 1)
  const sd = Math.sqrt(varr)
  return sd > 0 ? sd * Math.sqrt(windowBars) : null
}

export function personalActionClass(action) {
  const cat = ACTION_CATEGORY[action] || 'netral'
  if (cat === 'masuk') return 'ok'
  if (cat === 'keluar') return 'bad'
  return 'neutral'
}

// Peringkat top pick -- SATU fungsi dipakai bareng PersonalAggregatorView
// (top pick hari ini) dan PersonalHistoricalView (rekonstruksi top-3 per hari).
// Dulu keduanya sort sendiri-sendiri cuma pakai thesis_score tanpa pemecah
// seri; karena Array.sort itu stabil, pemenang seri jadi ditentukan URUTAN
// ARRAY-nya -- dan dua halaman itu membaca file berbeda dengan urutan berbeda
// (personal_calls.json: NASDAQ dulu baru NYSE; personal_history.json: urutan
// ticker pertama kali tercatat). Hasilnya dua halaman bisa menyebut top-3 yang
// beda untuk hari yang sama: live 2026-07-31, Speculative menampilkan
// AEHR/ALAB/ALMS di Agregator tapi FLNC/AEHR/ALAB di Riwayat, ketiganya seri
// di skor 88.
//
// Serinya sendiri masif karena thesis_score kasar (live: 60 ticker seri PERSIS
// di 100.0 untuk Quality, 95 ticker di 88.0 untuk Speculative) -- itu akarnya,
// ada di reasoning.py (lapisan publik) dan sengaja TIDAK disentuh dari sini.
//
// Urutan kunci: skor tesis dulu (kekuatan argumen, tetap primer), baru risiko
// (kalau argumennya sama kuat, yang lebih bersih risikonya lebih layak tampil),
// baru kelengkapan data, terakhir ticker sebagai jaring supaya hasilnya
// deterministik dan bisa dijelaskan -- bukan "kebetulan urutan file".
// CATATAN: ini cuma mengurutkan TAMPILAN. `action` tetap murni dari
// stance+tingkat skor (ACTION_TABLE di personal_reasoning.py) -- risiko tidak
// pernah ikut menentukan action, dan D-04 tidak tersentuh.
export function comparePersonalPicks(a, b) {
  const ca = a.call || a
  const cb = b.call || b
  const scoreA = ca.thesis_score ?? ca.source_confidence ?? 50
  const scoreB = cb.thesis_score ?? cb.source_confidence ?? 50
  if (scoreB !== scoreA) return scoreB - scoreA
  const highA = ca.risk_flags_high ?? 0
  const highB = cb.risk_flags_high ?? 0
  if (highA !== highB) return highA - highB
  const medA = ca.risk_flags_medium ?? 0
  const medB = cb.risk_flags_medium ?? 0
  if (medA !== medB) return medA - medB
  const dataA = ca.source_confidence ?? 0
  const dataB = cb.source_confidence ?? 0
  if (dataB !== dataA) return dataB - dataA
  return String(a.ticker || '').localeCompare(String(b.ticker || ''))
}

export function rankPersonalPicks(picks) {
  return [...picks].sort(comparePersonalPicks)
}

// Berapa kandidat yang skor tesisnya SAMA PERSIS dengan pick terakhir yang
// tampil -- dipakai buat mengungkap "3 ini cuma 3 dari sekian yang seri",
// bukan membiarkan pembaca mengira mereka pemenang mutlak. Mengembalikan null
// kalau tidak ada seri di batas potong (biar UI-nya tidak bising).
export function tieAtCutoff(rankedPicks, shown) {
  if (rankedPicks.length <= shown) return null
  const cutoff = rankedPicks[shown - 1]
  if (!cutoff) return null
  const c = cutoff.call || cutoff
  const cutoffScore = c.thesis_score ?? c.source_confidence ?? 50
  const tied = rankedPicks.filter((p) => {
    const pc = p.call || p
    return (pc.thesis_score ?? pc.source_confidence ?? 50) === cutoffScore
  })
  if (tied.length <= shown) return null
  return { score: cutoffScore, count: tied.length }
}

// "cicil_bertahap" -> "Cicil Bertahap"
export function prettyAction(action) {
  return prettyStance(action)
}

// Label horizon kontekstual (draft §10) -- horizon TETAP satu field, cuma
// labelnya berubah tergantung kategori action: tesis (masuk/tahan), cek
// ulang (pantau/lewati/tunggu_katalis), atau keluar (kurangi/jual).
const HORIZON_REVIEW_ACTIONS = new Set(['pantau', 'lewati', 'tunggu_katalis'])
const HORIZON_EXIT_ACTIONS = new Set(['kurangi', 'jual'])

export function horizonLabel(action) {
  if (HORIZON_REVIEW_ACTIONS.has(action)) return 'Cek ulang dalam'
  if (HORIZON_EXIT_ACTIONS.has(action)) return 'Sarankan keluar dalam'
  return 'Estimasi tesis'
}

const HORIZON_RANGE = {
  mingguan: '1-4 minggu',
  bulanan: '1-6 bulan',
  enam_bulan: '6-12 bulan',
  satu_dua_tahun: '1-2 tahun',
  lima_tahun: '3-5 tahun+',
}

export function prettyHorizon(horizon) {
  return HORIZON_RANGE[horizon] || horizon || '—'
}

const HORIZON_STATUS_INFO = {
  dalam_horizon: null, // tidak perlu badge kalau normal
  horizon_terlewati: { label: 'horizon terlewati', tone: 'warn' },
  tidak_berlaku: null,
}

export function horizonStatusInfo(status) {
  return HORIZON_STATUS_INFO[status] || null
}

// Sama persis dengan HORIZON_UPPER_DAYS di montrva/personal/
// personal_reasoning.py -- dipakai FE buat highlight "layak ditinjau ulang"
// per snapshot histori (bukan cuma per ticker seperti /api/personal/
// due-for-review), tanpa perlu round-trip API tambahan per entry.
const HORIZON_UPPER_DAYS = {
  mingguan: 28,
  bulanan: 180,
  enam_bulan: 365,
  satu_dua_tahun: 730,
  lima_tahun: 1825,
}

// Outcome evaluasi (§12 lanjutan, disetujui 2026-07-27) -- cuma action
// berklaim arah yang dapat terbukti/meleset, sisanya "tidak_berlaku".
const OUTCOME_TONE = {
  terbukti: 'ok',
  meleset: 'bad',
  ambigu: 'warn',
  tidak_berlaku: 'neutral',
}

export function outcomeClass(classification) {
  return OUTCOME_TONE[classification] || 'neutral'
}

export function prettyOutcome(classification) {
  if (!classification) return 'menunggu evaluasi'
  return prettyStance(classification)
}

// Sama persis dengan HORIZON_OUTCOME_THRESHOLD_PCT + ACTION_CATEGORY_ENTRY di
// montrva/personal/personal_evaluation.py -- makin panjang horizon, makin
// besar target return-nya (horizon pendek tidak boleh "menang" cuma karena
// noise harian). Dipakai FE buat nunjukin target harga/return SEBELUM call
// itu jatuh tempo -- backend baru menghitung classification ini SETELAH due.
const HORIZON_OUTCOME_THRESHOLD_PCT = {
  mingguan: 3.0,
  bulanan: 5.0,
  enam_bulan: 10.0,
  satu_dua_tahun: 15.0,
  lima_tahun: 30.0,
}

const ACTION_CATEGORY_ENTRY = new Set([
  'mulai_posisi', 'cicil_bertahap', 'akumulasi', 'akumulasi_saat_koreksi',
  'masuk_spekulatif', 'tambah', 'tambah_bertahap',
])

// Target harga + return% supaya tesis ini dibilang "terbukti" -- cuma untuk
// action ENTRY (klaim harga naik); action pasif/keluar tidak punya target
// harga tunggal yang jujur (lihat _classify di personal_evaluation.py: exit
// dinilai dari "gak naik", bukan dari mencapai satu angka). `startPrice`
// harus harga SAAT since (bukan harga sekarang) -- sama basis dengan yang
// dipakai backend buat menilai outcome nanti.
export function horizonTargetPrice(startPrice, action, horizon) {
  if (!ACTION_CATEGORY_ENTRY.has(action) || startPrice == null) return null
  const thresholdPct = HORIZON_OUTCOME_THRESHOLD_PCT[horizon]
  if (thresholdPct == null) return null
  return { thresholdPct, targetPrice: startPrice * (1 + thresholdPct / 100) }
}

// Sama persis dengan ANCHOR_BUFFER_DAYS + call_due_date() di montrva/
// personal/personal_historical.py -- SATU-SATUNYA tempat "kapan sebuah call
// jatuh tempo" boleh dihitung di frontend. Sebelum ini ada 2 salinan logika
// yang beda (isEntryDueForReview + horizonProgress, dua-duanya cuma age vs
// bucket horizon) yang mengabaikan horizon_anchor sama sekali -- audit
// 2026-07-29 menemukan 97.7% entry Speculative (6796/6965) meleset >3 hari
// dari tanggal jatuh tempo backend yang sebenarnya (backend mematok ke
// tanggal katalis + buffer, bukan bucket generik 28 hari) -- progress bar
// & badge "layak ditinjau ulang" jadi menampilkan estimasi yang salah untuk
// hampir semua tesis Speculative, lensa yang justru sedang jadi top pick.
const ANCHOR_BUFFER_DAYS = 5

function callDueDateMs(call, analyzedAtIso) {
  if (!call) return null
  if (call.horizon_anchor) {
    const anchorMs = new Date(`${call.horizon_anchor}T00:00:00Z`).getTime()
    if (!isNaN(anchorMs)) return anchorMs + ANCHOR_BUFFER_DAYS * 86400000
  }
  const upperDays = HORIZON_UPPER_DAYS[call.horizon]
  if (upperDays == null || !analyzedAtIso) return null
  const analyzedMs = new Date(analyzedAtIso).getTime()
  if (isNaN(analyzedMs)) return null
  return analyzedMs + upperDays * 86400000
}

export function isEntryDueForReview(analyzedAt, callSet) {
  if (!analyzedAt || !callSet) return false
  const now = Date.now()
  return ['multibagger', 'quality_compound', 'speculative'].some((m) => {
    const due = callDueDateMs(callSet[m], analyzedAt)
    return due != null && now > due
  })
}

// Progres "hari ke-berapa dari horizon" untuk satu action -- dipakai buat
// progress bar live di Aggregator Pribadi + expand row di Riwayat Pribadi
// (dua tempat yang sama-sama butuh bar ini, lihat ThesisProof.jsx). `since`
// = firstSeenAt() action ini pertama muncul, BUKAN analyzed_at entry
// terakhir -- horizon dihitung sejak tesisnya mulai, bukan sejak snapshot
// terbaru. `anchor` (opsional, cuma Speculative) = call.horizon_anchor --
// kalau ada, jatuh tempo dipatok ke situ (+buffer), bukan ke bucket generik.
export function horizonProgress(sinceIso, horizon, anchor) {
  if (!sinceIso) return null
  const sinceMs = new Date(sinceIso).getTime()
  if (isNaN(sinceMs)) return null
  const dueMs = callDueDateMs({ horizon, horizon_anchor: anchor }, sinceIso)
  if (dueMs == null) return null
  const now = Date.now()
  const ageDays = Math.floor((now - sinceMs) / 86400000)
  const totalDays = Math.round((dueMs - sinceMs) / 86400000)
  if (totalDays <= 0) return null
  const pct = Math.min(Math.max((ageDays / totalDays) * 100, 0), 100)
  const status = now > dueMs ? 'over' : pct >= 80 ? 'near' : 'ok'
  return { ageDays, upperDays: totalDays, pct, status }
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

// Polyline SVG points buat sparkline dari price_history (array {close, date})
// — sample turun ke ~N titik biar path-nya nggak terlalu padat, normalized ke
// viewBox width x height.
//
// Sumbu X diposisikan berdasarkan TANGGAL, bukan indeks array. price_history
// produksi tidak berjarak seragam: ~1 tahun terakhir daily + ~4 tahun
// sebelumnya bulanan (lihat _downsample_price_history). Positioning by index
// akan memampatkan 4 tahun pertama ke ~16% lebar chart dan melebarkan 1 tahun
// terakhir ke 84% -- distorsi yang salah menampilkan pergerakan lama sebagai
// baru. Turun ke index-based cuma kalau ada bar tanpa `date` yang valid
// (mis. titik quote live yang belum dikasih tanggal oleh pemanggil).
export function sparklinePoints(bars, width = 300, height = 56, maxPoints = 30) {
  if (!bars || bars.length < 2) return ''
  const step = Math.max(1, Math.floor(bars.length / maxPoints))
  const sample = []
  for (let i = 0; i < bars.length; i += step) sample.push(bars[i])
  if (sample[sample.length - 1] !== bars[bars.length - 1]) sample.push(bars[bars.length - 1])

  const times = sample.map((b) => (b.date ? new Date(b.date).getTime() : NaN))
  const hasDates = times.every((t) => !Number.isNaN(t))
  const firstT = times[0]
  const spanT = hasDates ? (times[times.length - 1] - firstT || 1) : null

  const closes = sample.map((b) => b.close)
  const lo = Math.min(...closes)
  const hi = Math.max(...closes)
  const range = hi - lo || 1

  return sample
    .map((b, i) => {
      const x = hasDates ? ((times[i] - firstT) / spanT) * width : (i / (sample.length - 1)) * width
      const y = height - ((b.close - lo) / range) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

// Label chart yang mencerminkan rentang tanggal price_history yang SEBENARNYA
// (bisa sampai 5 tahun -- lihat _downsample_price_history di backend), bukan
// nilai "1 Tahun" yang hardcoded dan salah begitu histori diperpanjang.
export function trendSpanLabel(bars) {
  if (!bars || bars.length < 2) return 'Tren Harga'
  const first = new Date(bars[0].date).getTime()
  const last = new Date(bars[bars.length - 1].date).getTime()
  if (Number.isNaN(first) || Number.isNaN(last) || last <= first) return 'Tren Harga'
  const years = (last - first) / (365.25 * 24 * 3600 * 1000)
  return years >= 1.5 ? `Tren ${Math.round(years)} Tahun` : 'Tren 1 Tahun'
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
