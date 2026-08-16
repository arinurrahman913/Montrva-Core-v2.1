import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import Bar from '../components/Bar'
import Sparkline from '../components/Sparkline'
import Icon from '../components/Icon'
import KnowledgeDetailCards from '../components/KnowledgeDetailCards'
import { fmtPct } from '../format'

// Warna & ikon per sektor — sekadar variasi visual antar-card, tidak
// membawa arti finansial (beda dengan warna good/bad di tempat lain).
// Nama sektor mengikuti taksonomi Yahoo Finance (dari evidence.fundamental.sector
// mentah, bukan GICS baku) — mis. "Financial Services", "Consumer Cyclical",
// "Consumer Defensive", "Basic Materials" — bukan nama GICS yang mirip tapi beda.
const SECTOR_META = {
  'Technology': { color: '#818CF8', icon: 'activity' },
  'Healthcare': { color: '#4ADE80', icon: 'shield' },
  'Financial Services': { color: '#e8b84b', icon: 'dollar' },
  'Energy': { color: '#FBBF7A', icon: 'flame' },
  'Basic Materials': { color: '#22D3EE', icon: 'layers' },
  'Industrials': { color: '#94A3B8', icon: 'bars' },
  'Consumer Cyclical': { color: '#F472B6', icon: 'mood' },
  'Consumer Defensive': { color: '#4fd1e0', icon: 'droplet' },
  'Real Estate': { color: '#C4B5FD', icon: 'layers' },
  'Utilities': { color: '#60A5FA', icon: 'wave' },
  'Communication Services': { color: '#FB7185', icon: 'flow' },
  'Lainnya': { color: '#7c8595', icon: 'dot' },
}

function sectorMeta(sector) {
  return SECTOR_META[sector] || SECTOR_META['Lainnya']
}

function completion(p) {
  return (p.metadata.fields_completed / p.metadata.fields_expected) * 100
}

function groupProfilesBySector(profiles) {
  const groups = {}
  for (const p of profiles) {
    const key = p.sector || 'Lainnya'
    if (!groups[key]) groups[key] = []
    groups[key].push(p)
  }
  return groups
}

const columns = [
  { key: 'ticker', label: 'Ticker', render: (r) => <span className="ticker">{r.ticker}</span> },
  { key: 'size_category', label: 'Size', render: (r) => r.size_category || '—' },
  {
    key: 'return_1y',
    label: 'Return 1Y',
    render: (r) => fmtPct(r.historical_trend?.return_1y),
    sortValue: (r) => r.historical_trend?.return_1y,
  },
  {
    key: 'revenue_yoy',
    label: 'Rev YoY (Q terkini)',
    render: (r) => fmtPct(r.financial_health?.revenue_trend?.yoy_q4),
    sortValue: (r) => r.financial_health?.revenue_trend?.yoy_q4,
  },
  {
    key: 'gross_margin',
    label: 'Gross Margin',
    render: (r) => {
      const v = r.financial_health?.gross_margin_trend?.q4
      return v !== null && v !== undefined ? `${v.toFixed(1)}%` : '—'
    },
    sortValue: (r) => r.financial_health?.gross_margin_trend?.q4,
  },
  {
    key: 'pe_ratio',
    label: 'P/E',
    render: (r) => (r.valuation?.pe_ratio_trailing ? `${r.valuation.pe_ratio_trailing.toFixed(1)}x` : '—'),
    sortValue: (r) => r.valuation?.pe_ratio_trailing,
  },
  {
    key: 'insider',
    label: 'Insider (30d)',
    render: (r) => {
      const n = r.ownership?.insider_filing_activity_30d || 0
      return n > 0 ? <span className="pill ok" style={{ fontSize: 10 }}>{n} filing</span> : '—'
    },
    sortValue: (r) => r.ownership?.insider_filing_activity_30d || 0,
  },
  {
    key: 'completion',
    label: 'Completion',
    render: (r) => <Bar pct={completion(r)} />,
    sortValue: (r) => completion(r),
  },
]

// Isi baris yang dibuka: grid "DATA PENDUKUNG LENGKAP" yang sama persis dengan
// yang dipakai TickerModal — satu definisi, dua pemakai (lihat
// components/KnowledgeDetailCards.jsx).
//
// Knowledge-nya sudah ada di memori (knowledge.json sudah dimuat halaman ini),
// tapi Evidence TIDAK: berkasnya 242 MB dan cuma bisa diambil per-ticker. Jadi
// ia ditarik saat barisnya dibuka, bukan di muka — kartunya tampil langsung
// dengan bagian Knowledge terisi, lalu KEPEMILIKAN/BERITA/VALUASI menyusul
// begitu Evidence datang. Tanpa Evidence pun kartunya tidak pecah (seluruh
// pembacaannya optional chaining), cuma berisi "—".
function ExpandedProfile({ profile, onSelectTicker }) {
  const [evidence, setEvidence] = useState(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    setEvidence(null)
    setFailed(false)
    api.ticker(profile.ticker)
      .then((d) => { if (!cancelled) setEvidence(d.evidence || null) })
      .catch(() => { if (!cancelled) setFailed(true) })
    return () => { cancelled = true }
  }, [profile.ticker])

  return (
    <div style={{ padding: '4px 2px 10px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <span className="msection-title" style={{ margin: 0 }}>Data pendukung lengkap</span>
        {!evidence && !failed && <span style={{ fontSize: 11, color: 'var(--faint)' }}>memuat Evidence…</span>}
        {failed && <span style={{ fontSize: 11, color: 'var(--warn)' }}>Evidence gagal dimuat — bagian yang bergantung padanya kosong</span>}
        <button
          className="btn-back"
          style={{ marginLeft: 'auto', transform: 'none' }}
          onClick={(e) => { e.stopPropagation(); onSelectTicker?.(profile.ticker) }}
        >
          Buka detail penuh →
        </button>
      </div>
      <KnowledgeDetailCards knowledge={profile} evidence={evidence} />
    </div>
  )
}

export default function KnowledgeView({ onSelectTicker }) {
  const { data, error } = useStageData(api.knowledge)
  const { data: summaryData } = useStageData(api.knowledgeSectorSummary)
  const [selectedSector, setSelectedSector] = useState(null)

  if (error) return <div className="empty">Gagal memuat data/knowledge.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const profiles = data.profiles || []
  const avgCompletion = profiles.reduce((s, p) => s + completion(p), 0) / (profiles.length || 1)
  const byProfile = groupProfilesBySector(profiles)
  // Agregat (leader, avg return, opportunity/risk count, dll) datang dari
  // backend (/api/knowledge/sector-summary) yang sudah join reasoning + risk
  // per ticker — lebih murah dihitung sekali di server daripada di browser
  // (lihat komentar di backend/app.py get_knowledge_sector_summary). Kalau
  // belum siap (masih loading), grid sektor pakai fallback sederhana dari
  // profiles saja supaya halaman tidak kosong sambil menunggu.
  const summaries = summaryData?.sectors || null
  const sectorList = summaries
    ? summaries.filter((s) => byProfile[s.sector])
    : Object.keys(byProfile)
        .map((sector) => ({ sector, count: byProfile[sector].length }))
        .sort((a, b) => b.count - a.count)

  if (selectedSector) {
    const sectorProfiles = byProfile[selectedSector] || []
    const meta = sectorMeta(selectedSector)
    return (
      <>
        <button className="btn-back" onClick={() => setSelectedSector(null)} style={{ marginBottom: 14 }}>
          ← Kembali ke semua sektor
        </button>
        <div className="msection-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <span className="l1a-ic" style={{ '--c': meta.color, width: 30, height: 30 }}>
            <Icon name={meta.icon} size={15} />
          </span>
          {selectedSector} — {sectorProfiles.length} tickers
        </div>
        <DataTable
          columns={columns}
          rows={sectorProfiles}
          renderExpanded={(r) => <ExpandedProfile profile={r} onSelectTicker={onSelectTicker} />}
        />
      </>
    )
  }

  const stats = [
    { label: 'Profiles', value: profiles.length },
    { label: 'Sektor', value: sectorList.length },
    { label: 'Avg Completion', value: `${avgCompletion.toFixed(0)}%` },
  ]

  return (
    <>
      <StatCards stats={stats} />
      <div className="l1a-grid">
        {sectorList.map((s, idx) => (
          <SectorCard key={s.sector} summary={s} idx={idx} onClick={() => setSelectedSector(s.sector)} />
        ))}
      </div>
    </>
  )
}

function SectorCard({ summary, idx, onClick }) {
  const { sector, count, leader, avg_completion, median_return_1y, median_revenue_yoy, median_pe_ratio,
    avg_institutional_pct, insider_active_tickers, insider_total_filings_30d,
    opportunity_count, risk_flag_count } = summary
  const meta = sectorMeta(sector)

  // Median dipakai (bukan mean) karena return/PE/revenue-growth semuanya
  // fat-tailed — satu ticker naik ribuan persen bisa menyeret rata-rata
  // jauh dari kondisi ticker tipikal di sektor itu (lihat catatan di
  // backend/app.py _median).
  const trend = median_return_1y == null ? 'flat' : median_return_1y > 5 ? 'up' : median_return_1y < -5 ? 'down' : 'flat'

  return (
    <div className="l1a-card" onClick={onClick} style={{ '--c': meta.color, '--i': idx }}>
      <div className="l1a-top">
        <div className="l1a-idwrap">
          <span className="l1a-ic">
            <Icon name={meta.icon} size={18} />
          </span>
          <div>
            <p className="l1a-label">{sector}</p>
            <div className="l1a-val">
              {count}
              <span className="u">tickers</span>
            </div>
          </div>
        </div>
        {avg_completion != null && <span className="l1a-pill ok">{avg_completion.toFixed(0)}%</span>}
      </div>

      {leader && (
        <div className="l1a-mini">
          <div>
            <p className="l1a-mini-l">🏆 Pemimpin</p>
            <p className="l1a-mini-v">
              {leader.ticker} {fmtPct(leader.return_1y)}
            </p>
          </div>
          <div>
            <p className="l1a-mini-l">P/E</p>
            <p className="l1a-mini-v">{leader.pe_ratio ? `${leader.pe_ratio.toFixed(1)}x` : '—'}</p>
          </div>
        </div>
      )}

      {(median_revenue_yoy != null || median_pe_ratio != null) && (
        <div className="l1a-mini">
          <div>
            <p className="l1a-mini-l">Median Rev YoY</p>
            <p className="l1a-mini-v">{median_revenue_yoy != null ? fmtPct(median_revenue_yoy) : '—'}</p>
          </div>
          <div>
            <p className="l1a-mini-l">Median P/E</p>
            <p className="l1a-mini-v">{median_pe_ratio != null ? `${median_pe_ratio.toFixed(1)}x` : '—'}</p>
          </div>
        </div>
      )}

      {(opportunity_count != null || risk_flag_count != null) && (
        <div className="l1a-mini">
          <div>
            <p className="l1a-mini-l">🎯 Peluang Asimetris</p>
            <p className="l1a-mini-v" style={{ color: opportunity_count > 0 ? 'var(--good)' : undefined }}>
              {opportunity_count ?? 0} ticker
            </p>
          </div>
          <div>
            <p className="l1a-mini-l">⚠️ Risk Flag Tinggi</p>
            <p className="l1a-mini-v" style={{ color: risk_flag_count > 0 ? 'var(--bad)' : undefined }}>
              {risk_flag_count ?? 0} ticker
            </p>
          </div>
        </div>
      )}

      {(avg_institutional_pct != null || insider_total_filings_30d != null) && (
        <div className="l1a-mini">
          <div>
            <p className="l1a-mini-l">🏦 Institutional Own.</p>
            <p className="l1a-mini-v">
              {avg_institutional_pct != null ? `${(avg_institutional_pct * 100).toFixed(1)}%` : '—'}
            </p>
          </div>
          <div>
            <p className="l1a-mini-l">📋 Insider Activity (30d)</p>
            <p className="l1a-mini-v">
              {insider_total_filings_30d || 0} filing{(insider_total_filings_30d || 0) !== 1 ? 's' : ''}
              {insider_active_tickers ? ` · ${insider_active_tickers} ticker` : ''}
            </p>
          </div>
        </div>
      )}

      <div className="l1a-chart">
        <Sparkline trend={trend} color={meta.color} seed={sector} height={36} />
      </div>

      <p className="l1a-foot">
        Median return 1Y (tipikal sektor): {median_return_1y != null ? fmtPct(median_return_1y) : '—'} · klik untuk lihat semua {count} ticker
      </p>
    </div>
  )
}
