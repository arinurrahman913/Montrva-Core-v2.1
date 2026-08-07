// Thin fetch wrappers around the Flask API. In dev, Vite proxies /api to
// http://localhost:5000 (see vite.config.js); in prod, Flask serves this
// same origin so relative paths just work.

// Dedup singkat per path (5s) -- audit 2026-07-29 menemukan satu kartu top
// pick personal bisa memanggil GET /api/ticker/<X> sampai 3x terpisah
// (useTickerMeta, useSectorConcentration, ThesisProof masing-masing fetch
// sendiri-sendiri) karena ketiganya mount hampir bersamaan. Cache ini di
// level getJSON (bukan per-caller) supaya berlaku otomatis buat semua
// endpoint, bukan cuma yang kebetulan ketahuan lewat audit ini. 5 detik
// dipilih lebih pendek dari cache server /live (30s) supaya tidak menambah
// staleness yang berarti, cuma menghindari duplikasi request yang mount
// dalam tick React yang sama.
const _CACHE_TTL_MS = 5000
const _cache = new Map() // path -> { expires, promise }

async function getJSON(path) {
  const now = Date.now()
  const cached = _cache.get(path)
  if (cached && cached.expires > now) return cached.promise
  const promise = fetch(path).then((resp) => {
    if (!resp.ok) throw new Error(`${path} -> HTTP ${resp.status}`)
    return resp.json()
  })
  _cache.set(path, { expires: now + _CACHE_TTL_MS, promise })
  // Kalau fetch gagal, jangan biarkan entry gagal nyangkut di cache -- buang
  // supaya percobaan berikutnya benar-benar fetch ulang, bukan re-throw promise lama.
  promise.catch(() => _cache.delete(path))
  return promise
}

export const api = {
  layer1: () => getJSON('/api/layer1'),
  layer1History: () => getJSON('/api/layer1_history'),
  screening: () => getJSON('/api/screening'),
  evidence: () => getJSON('/api/evidence'),
  evidenceSummary: () => getJSON('/api/evidence/summary'),
  knowledge: () => getJSON('/api/knowledge'),
  knowledgeSectorSummary: () => getJSON('/api/knowledge/sector-summary'),
  peer: () => getJSON('/api/peer'),
  catalyst: () => getJSON('/api/catalyst'),
  institutionalFlow: () => getJSON('/api/institutional_flow'),
  confidence: () => getJSON('/api/confidence'),
  risk: () => getJSON('/api/risk'),
  reasoning: () => getJSON('/api/reasoning'),
  aggregator: () => getJSON('/api/aggregator'),
  historical: () => getJSON('/api/historical'),
  historicalSummary: () => getJSON('/api/historical/summary'),
  sourceHealth: () => getJSON('/api/source_health'),
  ticker: (ticker) => getJSON(`/api/ticker/${encodeURIComponent(ticker)}`),
  liveQuote: (ticker) => getJSON(`/api/ticker/${encodeURIComponent(ticker)}/live`),
  aiNarrative: (ticker) => getJSON(`/api/ticker/${encodeURIComponent(ticker)}/ai-narrative`),
  sectors: () => getJSON('/api/sectors'),
  capabilities: () => getJSON('/api/capabilities'),
  consistency: () => getJSON('/api/consistency'),
  // Lapisan pribadi -- cuma ada kalau backend PERSONAL_ENABLED (lihat
  // capabilities di atas). Frontend menyembunyikan nav-nya kalau false,
  // tapi fungsi ini tetap aman dipanggil (404 di-throw seperti biasa).
  personalCalls: () => getJSON('/api/personal/calls'),
  personalTicker: (ticker) => getJSON(`/api/personal/ticker/${encodeURIComponent(ticker)}`),
  // Riwayat UTUH — 160 MB. TIDAK dipakai halaman mana pun lagi; tiga fungsi di
  // bawahnya menggantikannya. Dibiarkan ada untuk debugging manual, tapi jangan
  // dipanggil dari komponen: halaman Riwayat dulu butuh ~1 menit karena ini.
  personalHistory: () => getJSON('/api/personal/history'),
  // ~2,4 MB — hanya call yang berhak ikut perebutan top-3, tanpa isi timeline.
  // Perankingannya tetap di sini (rankPersonalPicks), backend cuma menyaring.
  personalHistoryCandidates: () => getJSON('/api/personal/history/candidates'),
  // Timeline penuh untuk beberapa ticker sekaligus (~0,9 MB untuk 35 ticker).
  personalHistoryTickers: (tickers) =>
    getJSON(`/api/personal/history/tickers?tickers=${encodeURIComponent(tickers.join(','))}`),
  // ~10 KB — dasar badge "BARU" di Agregator, dulu satu-satunya alasan halaman
  // itu ikut mengunduh riwayat 160 MB.
  personalHistoryPreviousPicks: () => getJSON('/api/personal/history/previous-picks'),
  personalDueForReview: () => getJSON('/api/personal/due-for-review'),
  // ~10 KB (sudah teragregasi backend), BUKAN personalHistory yang 127 MB.
  personalCalibration: () => getJSON('/api/personal/calibration'),
  // Trigger refresh pipeline dari dashboard. Tidak throw pada 409 (sudah jalan).
  // `sector` opsional — filter Screening ke satu sektor GICS (butuh sector_map,
  // lihat scripts/build_sector_map.py) supaya run jauh lebih cepat dari full-market.
  refresh: async (mode, sector) => {
    const qs = sector ? `?sector=${encodeURIComponent(sector)}` : ''
    const resp = await fetch(`/api/refresh/${mode}${qs}`, { method: 'POST' })
    return resp.json()
  },
  refreshStatus: () => getJSON('/api/refresh/status'),
}
