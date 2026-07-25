// Thin fetch wrappers around the Flask API. In dev, Vite proxies /api to
// http://localhost:5000 (see vite.config.js); in prod, Flask serves this
// same origin so relative paths just work.

async function getJSON(path) {
  const resp = await fetch(path)
  if (!resp.ok) {
    throw new Error(`${path} -> HTTP ${resp.status}`)
  }
  return resp.json()
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
  confidence: () => getJSON('/api/confidence'),
  risk: () => getJSON('/api/risk'),
  reasoning: () => getJSON('/api/reasoning'),
  aggregator: () => getJSON('/api/aggregator'),
  historical: () => getJSON('/api/historical'),
  sourceHealth: () => getJSON('/api/source_health'),
  ticker: (ticker) => getJSON(`/api/ticker/${encodeURIComponent(ticker)}`),
  liveQuote: (ticker) => getJSON(`/api/ticker/${encodeURIComponent(ticker)}/live`),
  aiNarrative: (ticker) => getJSON(`/api/ticker/${encodeURIComponent(ticker)}/ai-narrative`),
  sectors: () => getJSON('/api/sectors'),
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
