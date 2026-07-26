import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import HBarChart from '../components/HBarChart'
import { stanceClass, stanceTier, prettyStance, MODULE_LABELS } from '../format'

// ReasoningBundle: 3 ModuleOutput independen (multibagger, quality_compound,
// speculative), MASING-MASING punya kosakata stance sendiri (D-09) — sengaja
// TIDAK ada satu skor/stance gabungan (D-04). Tabel menampilkan ketiganya
// berdampingan.
const MODULES = ['multibagger', 'quality_compound', 'speculative']

export default function ReasoningView({ onSelectTicker }) {
  const { data, error } = useStageData(api.reasoning)

  if (error) return <div className="empty">Gagal memuat data/reasoning_outputs.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const outs = data.reasoning_outputs || []

  // "Konvergensi" = ketiga modul se-tier (semua bull / semua bear). Bukan
  // skor tunggal — sekadar hitungan berapa ticker yang ketiga lensanya searah.
  const convergent = outs.filter((o) => {
    const tiers = MODULES.map((m) => stanceTier(o[m]?.stance))
    return tiers.every((t) => t === 'bull') || tiers.every((t) => t === 'bear')
  }).length
  const anyUnreadable = outs.filter((o) =>
    MODULES.some((m) => stanceTier(o[m]?.stance) === 'unreadable'),
  ).length

  const stats = [
    { label: 'Total', value: outs.length, icon: 'layers', accent: '#818CF8' },
    { label: 'Konvergen (3 lensa searah)', value: convergent, tone: 'good', icon: 'check', accent: '#4ADE80' },
    { label: 'Ada lensa tak-terbaca', value: anyUnreadable, tone: anyUnreadable ? 'warn' : undefined, icon: 'alert', accent: '#FBBF7A' },
  ]

  const moduleCol = (m) => ({
    key: m,
    label: MODULE_LABELS[m],
    render: (r) => {
      const mo = r[m]
      if (!mo) return '—'
      return (
        <span className={`pill ${stanceClass(mo.stance)}`} title={mo.stance_rationale}>
          {prettyStance(mo.stance)}
        </span>
      )
    },
    sortValue: (r) => r[m]?.stance,
  })

  const columns = [
    { key: 'ticker', label: 'Ticker', render: (r) => <span className="ticker">{r.ticker}</span> },
    moduleCol('multibagger'),
    moduleCol('quality_compound'),
    moduleCol('speculative'),
    {
      key: 'confidence',
      label: 'Confidence (min)',
      render: (r) => {
        const min = Math.min(...MODULES.map((m) => r[m]?.confidence?.score ?? 100))
        return Number.isFinite(min) ? min.toFixed(0) : '—'
      },
      sortValue: (r) => Math.min(...MODULES.map((m) => r[m]?.confidence?.score ?? 100)),
    },
  ]

  // Distribusi stance per modul (satu chart per modul — TIDAK digabung,
  // karena kosakatanya beda dan tidak sebanding).
  const stanceDist = (m) => {
    const counts = {}
    outs.forEach((o) => {
      const st = o[m]?.stance
      if (st) counts[st] = (counts[st] || 0) + 1
    })
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .map(([st, count]) => ({
        label: prettyStance(st),
        count,
        color:
          stanceTier(st) === 'bull' ? 'var(--good)'
          : stanceTier(st) === 'bear' ? 'var(--bad)'
          : stanceTier(st) === 'unreadable' ? 'var(--dim)'
          : 'var(--warn)',
      }))
  }

  return (
    <>
      <StatCards stats={stats} />
      <div className="chart-row">
        <HBarChart title="Multibagger — distribusi stance" data={stanceDist('multibagger')} />
        <HBarChart title="Quality/Compound — distribusi stance" data={stanceDist('quality_compound')} />
        <HBarChart title="Speculative — distribusi stance" data={stanceDist('speculative')} />
      </div>
      <DataTable columns={columns} rows={outs} onRowClick={(r) => onSelectTicker(r.ticker)} />
    </>
  )
}
