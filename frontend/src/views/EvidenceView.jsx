import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import SourceHealthCards, { SOURCE_META } from '../components/SourceHealthCards'
import { fmtMoney, fmtNum, ratingClass } from '../format'

// "ok"/"missing"/"degraded" datang langsung dari SourceMetadata.status yang
// sudah ditempel tiap fetcher (contracts.py) — dibaca ulang di sini (bukan
// backend) supaya kartu kesehatan + kolom Completeness ikut ter-update live
// begitu evidence.json baru selesai ditulis, tanpa endpoint tambahan.
function sourceStatus(pkg, field) {
  return pkg[field]?.metadata?.status || 'missing'
}

function computeSourceStats(pkgs) {
  const total = pkgs.length
  const out = {}
  for (const { key } of SOURCE_META) {
    const ok = pkgs.filter((p) => sourceStatus(p, key) === 'ok').length
    out[key] = { ok, total, pct: total ? +((100 * ok) / total).toFixed(1) : 0 }
  }
  return out
}

function completeness(pkg) {
  const okCount = SOURCE_META.filter(({ key }) => sourceStatus(pkg, key) === 'ok').length
  return { okCount, total: SOURCE_META.length }
}

const CSV_COLUMNS = [
  ['Ticker', (r) => r.ticker],
  ['Price', (r) => r.price_market?.close],
  ['Market Cap', (r) => r.price_market?.market_cap],
  ['Revenue', (r) => r.fundamental?.revenue],
  ['Net Income', (r) => r.fundamental?.net_income],
  ['P/E', (r) => r.fundamental?.pe_ratio],
  ['SEC Qtrly', (r) => r.fundamental?.quarterly_count || 0],
  ['SEC Filings Status', (r) => r.sec_filings?.metadata?.status || ''],
  ['Institutional %', (r) => r.institutional_ownership?.percentage],
  ['News Count', (r) => r.news?.count ?? 0],
  ['Insider Filings (30d)', (r) => r.institutional_activity?.buy_count_30d || 0],
  ['Completeness', (r) => `${completeness(r).okCount}/${completeness(r).total}`],
]

function csvEscape(v) {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

function exportEvidenceCSV(pkgs) {
  const header = CSV_COLUMNS.map(([label]) => csvEscape(label)).join(',')
  const rows = pkgs.map((r) => CSV_COLUMNS.map(([, get]) => csvEscape(get(r))).join(','))
  const csv = [header, ...rows].join('\n')
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `evidence_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

export default function EvidenceView({ onSelectTicker }) {
  // Versi ringan (bukan api.evidence mentah) — evidence.json penuh bisa
  // ~275MB di skala full-market (price_history/quarterly_data/news/trades
  // per ticker), terlalu besar untuk di-fetch+parse browser demi tabel
  // ringkasan. /api/evidence/summary sudah strip array besar itu di backend;
  // detail lengkap 1 ticker tetap via TickerModal (/api/ticker/<t>).
  const { data, error } = useStageData(api.evidenceSummary)
  const { data: healthHistory } = useStageData(api.sourceHealth)
  const [activeSource, setActiveSource] = useState(null)
  const filterNoteRef = useRef(null)

  // Card klik cuma filter `rows` DataTable di bawahnya — nggak ada perubahan
  // visual di area kartu sendiri selain border tipis, jadi dari layar yang
  // cuma nampilin kartu (tabel di luar viewport) kelihatannya "nggak ngapa-
  // ngapain". Auto-scroll ke filter note supaya efeknya langsung kelihatan.
  useEffect(() => {
    if (activeSource) filterNoteRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [activeSource])

  if (error) return <div className="empty">Gagal memuat data/evidence.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const pkgs = data.packages || []
  const avgMcap = pkgs.reduce((s, p) => s + (p.price_market?.market_cap || 0), 0) / (pkgs.length || 1)
  const withSecQ = pkgs.filter((p) => p.fundamental?.quarterly_count).length
  const withFilings = pkgs.filter((p) => p.sec_filings?.metadata?.status === 'ok').length
  const sourceStats = computeSourceStats(pkgs)
  const filteredPkgs = activeSource ? pkgs.filter((p) => sourceStatus(p, activeSource) !== 'ok') : pkgs

  const stats = [
    { label: 'Packages', value: pkgs.length },
    { label: 'Avg Market Cap', value: fmtMoney(avgMcap) },
    { label: 'SEC Quarterly OK', value: `${withSecQ}/${pkgs.length}`, tone: 'good' },
    { label: 'SEC Filings OK', value: `${withFilings}/${pkgs.length}`, tone: 'good' },
  ]

  const columns = [
    { key: 'ticker', label: 'Ticker', render: (r) => <span className="ticker">{r.ticker}</span> },
    {
      key: 'price',
      label: 'Price',
      render: (r) => `$${fmtNum(r.price_market?.close, 2)}`,
      sortValue: (r) => r.price_market?.close,
    },
    {
      key: 'market_cap',
      label: 'Market Cap',
      render: (r) => fmtMoney(r.price_market?.market_cap),
      sortValue: (r) => r.price_market?.market_cap,
    },
    {
      key: 'revenue',
      label: 'Revenue',
      render: (r) => fmtMoney(r.fundamental?.revenue),
      sortValue: (r) => r.fundamental?.revenue,
    },
    {
      key: 'net_income',
      label: 'Net Income',
      render: (r) => fmtMoney(r.fundamental?.net_income),
      sortValue: (r) => r.fundamental?.net_income,
    },
    {
      key: 'pe_ratio',
      label: 'P/E',
      render: (r) => (r.fundamental?.pe_ratio ? `${r.fundamental.pe_ratio.toFixed(1)}x` : '—'),
      sortValue: (r) => r.fundamental?.pe_ratio,
    },
    {
      key: 'quarterly',
      label: 'SEC Qtrly',
      render: (r) => (
        <span className={`pill ${r.fundamental?.quarterly_count ? 'ok' : 'neutral'}`}>
          {r.fundamental?.quarterly_count || 0} qtrs
        </span>
      ),
      sortValue: (r) => r.fundamental?.quarterly_count || 0,
    },
    {
      key: 'filings',
      label: 'SEC Filings',
      render: (r) => (
        <span className={`pill ${ratingClass(r.sec_filings?.metadata?.status)}`}>
          {r.sec_filings?.metadata?.status || '—'}
        </span>
      ),
    },
    {
      key: 'institutional_pct',
      label: 'Institutional %',
      render: (r) => {
        const pct = r.institutional_ownership?.percentage
        return pct !== null && pct !== undefined ? `${(pct * 100).toFixed(1)}%` : '—'
      },
      sortValue: (r) => r.institutional_ownership?.percentage,
    },
    {
      key: 'news_count',
      label: 'News',
      render: (r) => r.news?.count ?? 0,
      sortValue: (r) => r.news?.count ?? 0,
    },
    {
      key: 'insider_activity',
      label: 'Insider (30d)',
      render: (r) => {
        const n = r.institutional_activity?.buy_count_30d || 0
        return n > 0 ? <span className="pill ok" style={{ fontSize: 10 }}>{n} filing</span> : '—'
      },
      sortValue: (r) => r.institutional_activity?.buy_count_30d || 0,
    },
    {
      key: 'completeness',
      label: 'Completeness',
      render: (r) => {
        const { okCount, total } = completeness(r)
        const cls = okCount === total ? 'ok' : okCount >= total - 1 ? 'warn' : 'bad'
        return (
          <span className={`pill ${cls}`}>
            {okCount}/{total} {okCount === total ? '✓' : '⚠'}
          </span>
        )
      },
      sortValue: (r) => completeness(r).okCount,
    },
  ]

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <button className="btn-back" onClick={() => exportEvidenceCSV(filteredPkgs)}>
          Export CSV ({filteredPkgs.length})
        </button>
      </div>
      <StatCards stats={stats} />
      <SourceHealthCards
        stats={sourceStats}
        history={Array.isArray(healthHistory) ? healthHistory : []}
        activeSource={activeSource}
        onSelect={setActiveSource}
      />
      {activeSource && (
        <p className="sh-filter-note" ref={filterNoteRef}>
          Menampilkan {filteredPkgs.length} ticker dengan status bukan "ok" untuk{' '}
          {SOURCE_META.find((s) => s.key === activeSource)?.label} ·{' '}
          <a onClick={() => setActiveSource(null)}>reset filter</a>
        </p>
      )}
      <DataTable columns={columns} rows={filteredPkgs} onRowClick={(r) => onSelectTicker(r.ticker)} />
    </>
  )
}
