import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import HBarChart from '../components/HBarChart'
import Bar from '../components/Bar'
import { bandClass, prettyLabel } from '../format'

// 7 section Knowledge yang di-skor Confidence (lihat confidence.py
// SECTION_WEIGHTS). Ditampilkan sebagai kolom ringkas + chart rata-rata.
const SECTIONS = [
  'financial_health', 'valuation', 'historical_trend',
  'competitive_structure', 'competitive_momentum', 'ownership', 'governance',
]

export default function ConfidenceView({ onSelectTicker }) {
  const { data, error } = useStageData(api.confidence)

  if (error) return <div className="empty">Gagal memuat data/confidence_scores.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const scores = data.scores || []
  const avg = scores.reduce((s, x) => s + (x.overall?.score || 0), 0) / (scores.length || 1)
  const highCount = scores.filter((s) => s.overall?.band === 'high').length

  const stats = [
    { label: 'Total', value: scores.length },
    { label: 'Avg Confidence', value: `${avg.toFixed(0)}%` },
    { label: 'High Band', value: highCount, tone: 'good' },
  ]

  const sectionCol = (name) => ({
    key: name,
    label: prettyLabel(name),
    render: (r) => {
      const sec = r.by_section?.[name]
      return sec ? `${sec.score.toFixed(0)}%` : '—'
    },
    sortValue: (r) => r.by_section?.[name]?.score,
  })

  const columns = [
    { key: 'ticker', label: 'Ticker', render: (r) => <span className="ticker">{r.ticker}</span> },
    { key: 'overall', label: 'Overall', render: (r) => <Bar pct={r.overall?.score} />, sortValue: (r) => r.overall?.score },
    {
      key: 'band',
      label: 'Band',
      render: (r) => <span className={`pill ${bandClass(r.overall?.band)}`}>{r.overall?.band || '—'}</span>,
    },
    sectionCol('financial_health'),
    sectionCol('valuation'),
    sectionCol('ownership'),
    sectionCol('governance'),
    {
      key: 'penalty',
      label: 'Penalti',
      render: (r) => {
        const tags = []
        if (r.peer_penalty?.applied) tags.push('peer')
        if (r.context_penalty?.applied) tags.push('context')
        if (r.recency_penalty?.applied) tags.push('recency')
        return tags.length ? tags.join(' · ') : '—'
      },
    },
  ]

  const bandDist = { high: 0, medium: 0, low: 0 }
  scores.forEach((s) => {
    const b = s.overall?.band
    if (b) bandDist[b] = (bandDist[b] || 0) + 1
  })
  const bandChart = [
    { label: 'High', count: bandDist.high, color: 'var(--good)' },
    { label: 'Medium', count: bandDist.medium, color: 'var(--warn)' },
    { label: 'Low', count: bandDist.low, color: 'var(--bad)' },
  ]

  const avgSection = (name) =>
    scores.reduce((s, x) => s + (x.by_section?.[name]?.score || 0), 0) / (scores.length || 1)
  const sectionChart = SECTIONS.map((name) => ({
    label: prettyLabel(name),
    count: Math.round(avgSection(name)),
    color: 'var(--accent)',
  }))

  return (
    <>
      <StatCards stats={stats} />
      <div className="chart-row">
        <HBarChart title="Distribusi Band Confidence" data={bandChart} />
        <HBarChart title="Rata-rata Kelengkapan per Section" data={sectionChart} />
      </div>
      <DataTable columns={columns} rows={scores} onRowClick={(r) => onSelectTicker(r.ticker)} />
    </>
  )
}
