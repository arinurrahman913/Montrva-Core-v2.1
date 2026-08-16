import { useState, useEffect } from 'react'
import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import HBarChart from '../components/HBarChart'
import { bandClass } from '../format'

// "Stance terbaik per lensa" -- filter+sort dari stance/confidence yang
// SUDAH dihitung tiap lensa, BUKAN skor/ranking baru (D-04 melarang
// AggregatorOutput/Synthesis punya field verdict/score/rank/recommendation).
// Kategori "terbaik" di sini cuma kategori stance yang sudah ada di
// STANCE_VOCAB masing-masing modul (reasoning_contracts.py), diurut by
// confidence.score modul itu sendiri -- tidak menciptakan kriteria baru.
const BEST_STANCE = {
  multibagger: 'ruang_terbuka',
  quality_compound: 'compounding_kuat',
  speculative: 'asimetri_berkatalis',
}

const LENS_TITLES = {
  multibagger: 'Multibagger — Ruang Terbuka',
  quality_compound: 'Quality — Compounding Kuat',
  speculative: 'Speculative — Asimetri Berkatalis',
}

function findModuleOutput(rec, moduleName) {
  return (rec.module_outputs || []).find((m) => m.module === moduleName)
}

function topPicks(outs, moduleName, n = 3) {
  return outs
    .map((rec) => ({ rec, mo: findModuleOutput(rec, moduleName) }))
    .filter(({ rec, mo }) => !rec.halted && mo && mo.stance === BEST_STANCE[moduleName])
    .sort((a, b) => (b.mo.confidence?.score ?? 0) - (a.mo.confidence?.score ?? 0))
    .slice(0, n)
}

function PickCard({ ticker, mo, onSelectTicker }) {
  // AI narrative di-fetch lazy PAS user expand (bukan otomatis semua kartu
  // sekaligus) -- reuse endpoint yang sama dipakai modal ticker, ai_narrative.py
  // sudah punya aturan wajib "jangan kasih rekomendasi beli/jual/hold, jangan
  // mengarang data" sejak awal dibangun, jadi tidak perlu prompt/endpoint baru.
  const [expanded, setExpanded] = useState(false)
  const [narrative, setNarrative] = useState(null)

  useEffect(() => {
    if (!expanded || narrative !== null) return
    let cancelled = false
    api
      .aiNarrative(ticker)
      .then((d) => { if (!cancelled) setNarrative(d) })
      .catch(() => { if (!cancelled) setNarrative({ available: false }) })
    return () => { cancelled = true }
  }, [expanded, ticker, narrative])

  // Harga + sektor -- ringan (angka + label, bukan prosa AI) jadi tetap
  // di-fetch langsung buat semua kartu (max 9), bukan di belakang toggle
  // seperti narrative. Reuse endpoint /api/ticker/<X> yang sama dipakai
  // modal detail -- sudah warm di cache backend, bukan panggilan baru.
  const [profile, setProfile] = useState(null)
  useEffect(() => {
    let cancelled = false
    api
      .ticker(ticker)
      .then((d) => { if (!cancelled) setProfile(d) })
      .catch(() => { if (!cancelled) setProfile({}) })
    return () => { cancelled = true }
  }, [ticker])
  const price = profile?.evidence?.price_market?.last_price
  const sector = profile?.knowledge?.sector

  return (
    <div
      onClick={() => onSelectTicker(ticker)}
      style={{ background: 'var(--panel2)', border: '1px solid var(--rule)', borderRadius: 10, padding: 12, marginBottom: 8, cursor: 'pointer' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 }}>
          <span className="ticker" style={{ fontSize: 13, fontWeight: 600, flexShrink: 0 }}>{ticker}</span>
          {price != null && (
            <span style={{ fontSize: 11, color: 'var(--text)', flexShrink: 0 }}>${price.toFixed(2)}</span>
          )}
          {sector && (
            <span style={{ fontSize: 10, color: 'var(--faint)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {sector}
            </span>
          )}
        </div>
        <span style={{ fontSize: 10.5, color: 'var(--good)', flexShrink: 0 }}>conf {mo.confidence?.score?.toFixed(0) ?? '—'}</span>
      </div>
      {mo.stance_rationale && (
        <div style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.4 }}>{mo.stance_rationale}</div>
      )}
      <div
        onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v) }}
        style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 8, paddingTop: 6, borderTop: '1px solid var(--rule)' }}
      >
        <svg
          width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          style={{ color: expanded ? 'var(--accent)' : 'var(--faint)', transform: expanded ? 'rotate(180deg)' : 'none', transition: 'transform .15s' }}
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
        <span style={{ fontSize: 10, color: expanded ? 'var(--accent)' : 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em' }}>
          Penjelasan AI
        </span>
      </div>
      {expanded && (
        <div style={{ fontSize: 10.5, color: 'var(--faint)', lineHeight: 1.6, marginTop: 8, animation: 'fadein .2s ease' }}>
          {narrative === null ? (
            <span style={{ fontStyle: 'italic' }}>Memuat penjelasan AI…</span>
          ) : narrative.available === false || !narrative.qualitative ? (
            <span style={{ fontStyle: 'italic' }}>Belum ada penjelasan AI untuk ticker ini.</span>
          ) : (
            narrative.qualitative
          )}
        </div>
      )}
    </div>
  )
}

function TopPicksSection({ outs, onSelectTicker }) {
  const lenses = ['multibagger', 'quality_compound', 'speculative']
  const picksByLens = lenses.map((m) => ({ module: m, picks: topPicks(outs, m) }))
  if (picksByLens.every((p) => p.picks.length === 0)) return null

  return (
    <div style={{ marginBottom: 22 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>
        Contoh Ticker — Stance Terbaik per Lensa
      </div>
      <p style={{ fontSize: 11, color: 'var(--faint)', margin: '2px 0 14px', lineHeight: 1.5 }}>
        Filter dari stance + confidence tertinggi yang sudah dihitung tiap lensa — bukan rekomendasi beli/jual
        atau ranking tunggal (Data Contracts D-04). Tetap cek Risk Flags &amp; Confidence sebelum kesimpulan.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
        {picksByLens.map(({ module, picks }) => (
          <div key={module}>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
              {LENS_TITLES[module]}
            </div>
            {picks.length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--faint)' }}>Tidak ada ticker dengan stance ini saat ini.</div>
            ) : (
              picks.map(({ rec, mo }) => (
                <PickCard key={rec.ticker} ticker={rec.ticker} mo={mo} onSelectTicker={onSelectTicker} />
              ))
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// AggregatorOutput (Data Contracts §7, D-04): TIDAK ada rekomendasi/skor/
// ranking tunggal — sengaja. Yang ditampilkan: 3 modul berdampingan (via
// Synthesis: agreements/divergences), confidence terendah, surprise
// (keunikan kombinasi stance vs populasi sesi), dan status halted.
export default function AggregatorView({ onSelectTicker }) {
  const { data, error } = useStageData(api.aggregatorSummary)

  if (error) return <div className="empty">Gagal memuat data/final_recommendations.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const outs = data.recommendations || []
  const halted = outs.filter((o) => o.halted).length
  const fullConv = outs.filter((o) => o.synthesis?.full_convergence).length
  const withDivergence = outs.filter((o) => (o.synthesis?.divergences || 0) > 0).length

  const stats = [
    { label: 'Total', value: outs.length },
    { label: 'Konvergen penuh', value: fullConv, tone: 'good' },
    { label: 'Ada divergensi', value: withDivergence, tone: withDivergence ? 'warn' : undefined },
    { label: 'Halted (ekstrem)', value: halted, tone: halted ? 'bad' : undefined },
  ]

  const columns = [
    { key: 'ticker', label: 'Ticker', render: (r) => <span className="ticker">{r.ticker}</span> },
    {
      key: 'status',
      label: 'Status',
      render: (r) =>
        r.halted ? (
          <span className="pill bad" title={r.halt_reason || ''}>halted</span>
        ) : r.synthesis?.full_convergence ? (
          <span className="pill ok">konvergen</span>
        ) : (
          <span className="pill neutral">divergen</span>
        ),
    },
    {
      key: 'agreements',
      label: 'Kesepakatan',
      render: (r) => r.synthesis?.agreements ?? '—',
      sortValue: (r) => r.synthesis?.agreements ?? -1,
    },
    {
      key: 'divergences',
      label: 'Perbedaan',
      render: (r) => r.synthesis?.divergences ?? '—',
      sortValue: (r) => r.synthesis?.divergences ?? -1,
    },
    {
      key: 'confidence',
      label: 'Confidence',
      render: (r) => {
        const c = r.synthesis?.confidence
        return c ? <span className={`pill ${bandClass(c.band)}`}>{c.score.toFixed(0)} · {c.band}</span> : '—'
      },
      sortValue: (r) => r.synthesis?.confidence?.score,
    },
    {
      key: 'surprise',
      label: 'Surprise',
      render: (r) => (r.synthesis?.surprise != null ? r.synthesis.surprise.toFixed(2) : '—'),
      sortValue: (r) => r.synthesis?.surprise,
    },
    {
      key: 'flags',
      label: 'Risk Flags',
      render: (r) => {
        // Hitungannya sudah diringkas backend (/api/aggregator/summary) —
        // isi lengkap risk_flags tidak pernah dirender di halaman ini.
        const triggered = r.risk_flags_triggered ?? 0
        return triggered > 0 ? <span className="pill warn">{triggered} triggered</span> : (r.risk_flags_total ?? 0)
      },
      sortValue: (r) => r.risk_flags_triggered ?? 0,
    },
  ]

  // Surprise tertinggi = kombinasi stance paling tidak biasa di populasi sesi
  // ini — ini yang "menarik untuk dilihat", bukan skor rekomendasi.
  const sorted = [...outs].sort((a, b) => (b.synthesis?.surprise ?? -1) - (a.synthesis?.surprise ?? -1))

  const statusChart = [
    { label: 'Konvergen', count: fullConv, color: 'var(--good)' },
    { label: 'Divergen', count: withDivergence, color: 'var(--warn)' },
    { label: 'Halted', count: halted, color: 'var(--bad)' },
  ]

  return (
    <>
      <StatCards stats={stats} />
      <div className="chart-row">
        <HBarChart title="Distribusi Status Sintesis" data={statusChart} />
      </div>
      <TopPicksSection outs={outs} onSelectTicker={onSelectTicker} />
      <p className="narrative" style={{ margin: '0 0 12px', color: 'var(--dim)', fontSize: 13 }}>
        Diurutkan berdasarkan <strong>surprise</strong> — kombinasi stance paling tidak biasa di populasi sesi ini di atas.
        Tidak ada skor rekomendasi tunggal (Data Contracts D-04): buka satu ticker untuk melihat ketiga lensa + sintesisnya.
      </p>
      <DataTable columns={columns} rows={sorted} onRowClick={(r) => onSelectTicker(r.ticker)} />
    </>
  )
}
