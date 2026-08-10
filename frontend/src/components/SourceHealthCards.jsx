import Icon from './Icon'

// Urutan + tampilan tetap dari 6 field EvidencePackage (kunci harus sama
// dengan montrva/layer2/source_health.py SOURCES supaya `history` cocok).
// cadence = seberapa sering data jenis ini SEHARUSNYA berubah (properti
// dari jenis sumbernya sendiri, sama untuk semua ticker — bukan hasil
// fetch), dipakai buat label freshness indicator di footer card.
export const SOURCE_META = [
  { key: 'price_market', label: 'Price/Market', icon: 'dollar', color: '#e8b84b', cadence: 'Harian' },
  { key: 'fundamental', label: 'Fundamental', icon: 'chartline', color: '#4fd1e0', cadence: 'Kuartalan' },
  { key: 'institutional_ownership', label: 'Institutional Own', icon: 'bond', color: '#C4B5FD', cadence: 'Kuartalan' },
  { key: 'institutional_activity', label: 'Insider Activity', icon: 'clipboard', color: '#FBBF7A', cadence: 'Event-based' },
  { key: 'news', label: 'News', icon: 'newspaper', color: '#60A5FA', cadence: 'Event-based' },
  { key: 'sec_filings', label: 'SEC Filings', icon: 'filedoc', color: '#4ADE80', cadence: 'Event-based' },
]

// Sparkline data ASLI (bukan dekoratif seperti components/Sparkline.jsx) —
// memplot pct history harian per source. Ditulis manual (bukan reuse
// Sparkline.jsx) karena API-nya seed/trend sintetis, tidak menerima deret
// angka nyata.
function MiniTrend({ values, color }) {
  if (!values || values.length < 2) {
    return <div className="sh-trend-empty">belum ada histori tren</div>
  }
  const W = 320
  const H = 34
  const pad = 4
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const x = (i) => pad + (W - 2 * pad) * (i / (values.length - 1))
  const y = (v) => H - pad - (H - 2 * pad) * ((v - min) / span)
  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block' }}>
      <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(values[values.length - 1])} r="2.6" fill={color} />
    </svg>
  )
}

// stats: { [sourceKey]: {ok, total, pct} } — dihitung client-side dari
// packages saat ini (lihat EvidenceView). history: array snapshot harian
// dari /api/source_health (bisa kosong kalau belum pernah full-pipeline
// refresh sejak fitur ini ada).
export default function SourceHealthCards({ stats, history, activeSource, onSelect }) {
  return (
    <div className="l1a-grid sh-grid">
      {SOURCE_META.map((meta, idx) => {
        const s = stats[meta.key] || { ok: 0, total: 0, pct: 0 }
        const trendValues = (history || [])
          .map((h) => h.sources?.[meta.key]?.pct)
          .filter((v) => v !== null && v !== undefined)
        const active = activeSource === meta.key
        return (
          <div
            key={meta.key}
            className={`l1a-card sh-card${active ? ' active' : ''}`}
            style={{ '--c': meta.color, '--i': idx }}
            onClick={() => onSelect(active ? null : meta.key)}
            role="button"
            tabIndex={0}
          >
            <div className="l1a-top">
              <div className="l1a-idwrap">
                <span className="l1a-ic">
                  <Icon name={meta.icon} size={18} />
                </span>
                <div>
                  <p className="l1a-label">{meta.label}</p>
                  <div className="l1a-val">
                    {s.ok}
                    <span className="u">/{s.total}</span>
                  </div>
                </div>
              </div>
              <span className="l1a-pill ok">{s.pct}%</span>
            </div>
            <div className="l1a-chart">
              <MiniTrend values={trendValues} color={meta.color} />
            </div>
            <p className="l1a-foot">
              {meta.cadence} · {meta.key === 'institutional_activity' ? 'ada filing insider (30 hari)' : 'status ok'} · klik
              untuk filter tabel
            </p>
          </div>
        )
      })}
    </div>
  )
}
