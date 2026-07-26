import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import {
  fmtPct, fmtMoney, fmtNum, ratingClass, stanceClass, prettyStance, bandClass, prettyLabel,
  fmtMetricValue, firstSentence, fmtCompact, sparklinePoints, MODULE_LABELS,
  AVAILABILITY_INFO, QUALITY_INFO, METRIC_DEFINITIONS,
} from '../format'

const DILUTION_WARN_THRESHOLD_PCT = 10.0 // sama dengan risk.py DILUTION_THRESHOLD_PCT

const MODULES = ['multibagger', 'quality_compound', 'speculative']

// Kontext dari halaman asal klik menentukan section mana yang ditampilkan
// (D-04 "jangan campur" — user eksplisit minta modal fokus per stage,
// bukan dump semua sekaligus). Nama-nama di sini = key activeView di App.jsx.
const STAGE_CONTEXTS = new Set(['reasoning', 'aggregator', 'risk', 'confidence', 'catalyst', 'knowledge', 'historical', 'peer'])

export default function TickerModal({ ticker, context, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [live, setLive] = useState(null) // null = loading, {stale:true} = failed, else fresh quote
  const [aiNarrative, setAiNarrative] = useState(null) // null = loading/n.a., else {narrative, cached, available}

  useEffect(() => {
    let cancelled = false
    setData(null)
    setError(null)
    api
      .ticker(ticker)
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch((e) => {
        if (!cancelled) setError(String(e))
      })
    return () => {
      cancelled = true
    }
  }, [ticker])

  useEffect(() => {
    let cancelled = false
    setLive(null)
    // Independent from the main fetch above — this one can be slow (live
    // Yahoo lookup) or fail without blocking the rest of the modal.
    api
      .liveQuote(ticker)
      .then((d) => {
        if (!cancelled) setLive(d)
      })
      .catch((e) => {
        if (!cancelled) setLive({ stale: true, error: String(e) })
      })
    return () => {
      cancelled = true
    }
  }, [ticker])

  useEffect(() => {
    // Relevan buat modal Knowledge (qualitative + quantitative_highlights)
    // dan Catalyst (catalyst_note) — satu response yang sama dipakai dua
    // context sekaligus (1 API call, bukan 2). Context lain gak butuh ini,
    // jangan buang panggilan API walau ada cache.
    if (context !== 'knowledge' && context !== 'catalyst' && context !== 'peer') {
      setAiNarrative(null)
      return
    }
    let cancelled = false
    setAiNarrative(null)
    api
      .aiNarrative(ticker)
      .then((d) => {
        if (!cancelled) setAiNarrative(d)
      })
      .catch((e) => {
        if (!cancelled) setAiNarrative({ narrative: null, available: false, error: String(e) })
      })
    return () => {
      cancelled = true
    }
  }, [ticker, context])

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal" onClick={(e) => e.target.classList.contains('modal') && onClose()}>
      <div className="modal-box">
        <div className="modal-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <h2>{ticker}</h2>
            <LiveQuoteBadge live={live} />
          </div>
          <button className="x" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="modal-body">
          {error && <div className="empty">Gagal memuat detail: {error}</div>}
          {!error && !data && <div className="loading">Memuat detail…</div>}
          {data && <ModalBody data={data} context={context} aiNarrative={aiNarrative} />}
        </div>
      </div>
    </div>
  )
}

function LiveQuoteBadge({ live }) {
  if (!live) {
    return <span className="pill neutral">live …</span>
  }
  if (live.stale || live.last_price === undefined || live.last_price === null) {
    return <span className="pill neutral" title={live.error || 'live quote unavailable'}>live n/a</span>
  }
  const tone = live.change_pct >= 0 ? 'ok' : 'bad'
  return (
    <span className={`pill ${tone}`} title={`fetched ${live.fetched_at}`}>
      ${live.last_price.toFixed(2)} {fmtPct(live.change_pct)}
    </span>
  )
}

function AiNarrativeBlock({ aiNarrative }) {
  if (!aiNarrative) {
    return (
      <div className="msection" style={{ marginBottom: 12 }}>
        <div className="msection-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span>Ringkasan AI</span>
          <span className="pill neutral">memuat…</span>
        </div>
      </div>
    )
  }

  if (!aiNarrative.available) {
    return (
      <div className="msection" style={{ marginBottom: 12 }}>
        <div className="msection-title">Ringkasan AI</div>
        <p className="narrative" style={{ fontSize: 12, color: 'var(--faint)' }}>
          Belum dikonfigurasi — isi <code>GEMINI_API_KEY</code> di <code>.env</code> untuk mengaktifkan ringkasan naratif otomatis.
        </p>
      </div>
    )
  }

  if (!aiNarrative.qualitative) {
    return (
      <div className="msection" style={{ marginBottom: 12 }}>
        <div className="msection-title">Ringkasan AI</div>
        <p className="narrative" style={{ fontSize: 12, color: 'var(--faint)' }}>
          Gagal membuat ringkasan untuk ticker ini. Coba lagi nanti.
        </p>
      </div>
    )
  }

  const highlights = aiNarrative.quantitative_highlights || []

  return (
    <div className="msection" style={{ marginBottom: 12 }}>
      <div className="msection-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span>Ringkasan AI</span>
        {aiNarrative.cached && <span className="pill neutral" title="Dari cache, data belum berubah sejak terakhir dibuat">cache</span>}
      </div>

      <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--faint)', marginBottom: 6, letterSpacing: 0.3 }}>
        KUALITATIF
      </div>
      <p className="narrative" style={{ marginBottom: highlights.length ? 14 : 0 }}>{aiNarrative.qualitative}</p>

      {highlights.length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--faint)', marginBottom: 8, letterSpacing: 0.3 }}>
            SOROTAN KUANTITATIF <span style={{ fontWeight: 400 }}>— dipilih AI</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 8 }}>
            {highlights.map((h, i) => (
              <div key={i} style={{ background: 'var(--panel)', borderRadius: 6, padding: '8px 10px' }}>
                <div style={{ fontSize: 10, color: 'var(--faint)' }}>{h.label}</div>
                <div style={{ fontSize: 15, fontWeight: 500 }}>{h.value}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function DetailCard({ title, children }) {
  return (
    <div style={{ background: 'var(--panel2)', borderRadius: 10, padding: '12px 14px', border: '0.5px solid var(--rule)' }}>
      <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: 8 }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12.5 }}>{children}</div>
    </div>
  )
}

function DetailRow({ label, value, valueColor }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
      <span style={{ color: 'var(--dim)' }}>{label}</span>
      <span style={{ fontWeight: 600, color: valueColor, textAlign: 'right' }}>{value}</span>
    </div>
  )
}

function KnowledgeDetailCards({ knowledge, evidence }) {
  const fh = knowledge.financial_health || {}
  const rt = fh.revenue_trend || {}
  const bs = fh.balance_sheet || {}
  const ht = knowledge.historical_trend || {}
  const own = knowledge.ownership || {}
  const val = knowledge.valuation || {}
  const pt = val.price_target || {}
  const gov = knowledge.governance || {}
  const cs = knowledge.competitive_structure || {}

  const fundamental = evidence?.fundamental || {}
  const io = evidence?.institutional_ownership || {}
  const topHolder = (io.top_holders || [])[0]
  const ia = evidence?.institutional_activity || {}
  const news = (evidence?.news?.news || []).slice(0, 3)
  const filings = (evidence?.sec_filings?.filings || []).slice(0, 3)
  const revEst = (evidence?.analyst_estimates?.revenue_estimates || []).find((r) => r.period === '+1q')

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
      <DetailCard title="Business Profile">
        <DetailRow label="Sektor" value={knowledge.sector || '—'} />
        <DetailRow label="Business model" value={cs.business_model || '—'} />
        <DetailRow label="Karyawan" value={cs.employees_count != null ? fmtCompact(cs.employees_count) : '—'} />
        <DetailRow label="TAM estimate" value={cs.tam_estimate != null ? fmtMoney(cs.tam_estimate) : '—'} />
      </DetailCard>

      <DetailCard title="Growth">
        <DetailRow label="Revenue YoY (kini)" value={fmtPct(rt.yoy_q4)} />
        <DetailRow label="CAGR 3Y" value={fmtPct(rt.cagr_3y)} />
        <DetailRow label="CAGR 5Y" value={fmtPct(rt.cagr_5y)} />
      </DetailCard>

      <DetailCard title="Profitability">
        <DetailRow label="Net margin (kini)" value={fmtPct(fh.net_margin_trend?.q4)} />
        <DetailRow label="Gross margin (kini)" value={fmtPct(fh.gross_margin_trend?.q4)} />
        <DetailRow label="ROE · ROA" value={`${fmtPct(fh.roe != null ? fh.roe * 100 : null)} · ${fmtPct(fh.roa != null ? fh.roa * 100 : null)}`} />
      </DetailCard>

      <DetailCard title="Balance Sheet & Liquidity">
        <DetailRow label="Debt/Equity" value={fmtNum(bs.debt_to_equity)} />
        <DetailRow label="Current · Quick ratio" value={`${fmtNum(bs.current_ratio)} · ${fmtNum(bs.quick_ratio)}`} />
        <DetailRow label="Cash & equiv." value={bs.cash_and_equivalents != null ? fmtMoney(bs.cash_and_equivalents) : '—'} />
      </DetailCard>

      <DetailCard title="Valuation">
        <DetailRow label="P/E · P/S · P/B" value={`${fmtNum(val.pe_ratio_trailing)}x · ${fmtNum(val.ps_ratio)}x · ${fmtNum(val.pb_ratio)}x`} />
        <DetailRow
          label={`Target (${pt.num_analysts ?? '—'} analis)`}
          value={pt.target_mean ? `${fmtMoney(pt.target_mean)} (${fmtPct(pt.upside_pct)})` : '—'}
          valueColor="var(--accent)"
        />
        {revEst && <DetailRow label="Revenue est. Q depan" value={fmtPct(revEst.growth)} />}
      </DetailCard>

      <DetailCard title="Performa Historis">
        <DetailRow label="Return 1Y" value={fmtPct(ht.return_1y)} valueColor={ht.return_1y >= 0 ? 'var(--good)' : 'var(--bad)'} />
        <DetailRow label="Volatilitas harian" value={fmtPct(ht.volatility_daily)} />
        <DetailRow label="Beta" value={fmtNum(ht.beta)} />
      </DetailCard>

      <DetailCard title="Kepemilikan">
        <DetailRow label="Institutional own." value={fmtPct(own.institutional_pct != null ? own.institutional_pct * 100 : null)} />
        {topHolder && <DetailRow label="Top holder" value={`${topHolder.holder} ${fmtPct(topHolder.pct_held)}`} />}
        <DetailRow label="Form 4 filing (30d)" value={fmtCompact(ia.buy_count_30d ?? 0)} />
      </DetailCard>

      <DetailCard title="Governance">
        <DetailRow label="Saham beredar (12bln)" value={fmtPct(gov.shares_outstanding_change_12m)} />
        <DetailRow label="Auditor · Restatement" value={`${(gov.auditor_changes || []).length} · ${(gov.restatements || []).length}`} />
      </DetailCard>

      <DetailCard title="Berita & Filing Terbaru">
        {news.length === 0 && filings.length === 0 && <span style={{ color: 'var(--faint)' }}>Tidak ada data terbaru.</span>}
        {news.map((n, i) => (
          <div key={`n${i}`} style={{ fontSize: 11.5 }}>
            {n.headline} <span style={{ color: 'var(--faint)' }}>· {n.published_at?.slice(0, 10)}</span>
          </div>
        ))}
        {filings.map((f, i) => (
          <div key={`f${i}`} style={{ fontSize: 11.5 }}>
            {f.form_type} filed <span style={{ color: 'var(--faint)' }}>· {f.filing_date}</span>
          </div>
        ))}
      </DetailCard>
    </div>
  )
}

const CERTAINTY_TONE = { scheduled: 'ok', expected: 'warn', rumored: 'neutral' }

function ordinal(n) {
  const rem100 = n % 100
  if (rem100 >= 11 && rem100 <= 13) return `${n}th`
  switch (n % 10) {
    case 1: return `${n}st`
    case 2: return `${n}nd`
    case 3: return `${n}rd`
    default: return `${n}th`
  }
}

function daysAway(dateStr) {
  // dateStr ("2026-07-27") diparse sebagai UTC midnight -- "hari ini" juga
  // harus dinormalisasi ke UTC midnight, bukan Date.now() lokal, supaya
  // gak geser 1 hari tergantung timezone browser (lihat juga dateLabel di
  // bawah yang pakai timeZone:'UTC' untuk alasan yang sama).
  const target = new Date(dateStr + 'T00:00:00Z')
  const now = new Date()
  const todayUtc = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())
  return Math.max(0, Math.round((target.getTime() - todayUtc) / 86400000))
}

function CatalystCountdownCard({ catalyst, aiNarrative }) {
  const upcoming = (catalyst.catalysts || []).filter((c) => c.certainty !== 'rumored')
  if (upcoming.length === 0) {
    if (catalyst.status === 'missing') {
      return (
        <div className="mcell dim">
          <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, color: 'var(--dim)' }}>
            <span className="avail-dot unavailable" />Data catalyst tidak tersedia
          </span>
        </div>
      )
    }
    return (
      <div className="mcell dim">
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, color: 'var(--dim)' }}>
          <span className="avail-dot not_applicable" />Tidak ada catalyst terjadwal dalam horizon {catalyst.horizon_days || 90} hari
        </span>
      </div>
    )
  }

  const sorted = [...upcoming].sort((a, b) => a.expected_at.localeCompare(b.expected_at))
  const nearest = sorted[0]
  const others = sorted.slice(1)
  const days = daysAway(nearest.expected_at)
  const horizon = catalyst.horizon_days || 90
  const pct = Math.min(100, (days / horizon) * 100)
  const dateLabel = new Date(nearest.expected_at + 'T00:00:00Z').toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

  return (
    <div>
      <div className="mcell" style={{ marginBottom: others.length ? 10 : 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <div className="mcell-label" style={{ marginBottom: 0, textTransform: 'capitalize' }}>{nearest.kind}</div>
          <span style={{ display: 'flex', gap: 6 }}>
            {nearest.lifecycle_status === 'delayed' && (
              <span className="pill warn" title={`Sebelumnya diperkirakan ${nearest.previous_expected_at}`}>delayed</span>
            )}
            <span className={`pill ${CERTAINTY_TONE[nearest.certainty]}`}>{nearest.certainty}</span>
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12 }}>
          <span style={{ fontSize: 26, fontWeight: 700, fontFamily: 'var(--mono)' }}>{days}</span>
          <span style={{ fontSize: 12.5, color: 'var(--dim)' }}>hari lagi · {dateLabel}</span>
        </div>
        <div style={{ position: 'relative', height: 5, background: 'var(--panel3)', borderRadius: 3, marginBottom: 5 }}>
          <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${pct}%`, background: 'var(--accent)', borderRadius: 3 }} />
          <div style={{ position: 'absolute', left: `${pct}%`, top: -3, width: 11, height: 11, borderRadius: '50%', background: 'var(--accent)', transform: 'translateX(-50%)', border: '2px solid var(--panel2)' }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--faint)', marginBottom: 12 }}>
          <span>Hari ini</span><span>Horizon {horizon} hari</span>
        </div>

        {aiNarrative?.catalyst_note && (
          <div style={{ background: 'var(--panel3)', borderRadius: 6, padding: '8px 10px', marginBottom: 10, fontSize: 12, lineHeight: 1.5 }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 4 }}>Catatan AI</div>
            {aiNarrative.catalyst_note}
          </div>
        )}

        <div style={{ borderTop: '1px solid var(--rule)', paddingTop: 8, fontSize: 11, color: 'var(--dim)' }}>
          Dipakai lensa <strong style={{ color: 'var(--text)' }}>Speculative</strong> sebagai dasar stance "asimetri berkatalis".
        </div>
      </div>

      {others.map((c, i) => (
        <div className="factor" key={i} style={{ color: 'var(--faint)', fontSize: 11.5 }}>
          {c.kind} · {c.expected_at} ({c.certainty})
          {c.lifecycle_status === 'delayed' && (
            <span className="pill warn" style={{ marginLeft: 6, fontSize: 9 }} title={`Sebelumnya diperkirakan ${c.previous_expected_at}`}>delayed</span>
          )}
        </div>
      ))}
    </div>
  )
}

const RESOLVED_TONE = { completed: 'ok', cancelled: 'bad' }

function CatalystHistoryBar({ history }) {
  const [open, setOpen] = useState(false)
  if (!history || history.length === 0) return null
  const sorted = [...history].sort((a, b) => b.resolved_on.localeCompare(a.resolved_on))
  return (
    <div style={{ marginTop: 10 }}>
      <div className={`rawdata-bar${open ? ' open' : ''}`} onClick={() => setOpen(!open)}>
        <span>Histori Catalyst</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="rawdata-meta">{history.length} resolved</span>
          <span className="rawdata-chev">▾</span>
        </span>
      </div>
      {open && (
        <div className="rawdata-panel">
          <div className="rawdata-body">
            {sorted.map((r, i) => (
              <div className="rawdata-row" key={i}>
                <div className="rawdata-field" style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ textTransform: 'capitalize' }}>{r.kind} · {r.expected_at}</span>
                  <span className={`pill ${RESOLVED_TONE[r.lifecycle_status]}`} style={{ fontSize: 9.5 }}>{r.lifecycle_status}</span>
                </div>
                {r.outcome_note && <div className="rawdata-val" style={{ marginTop: 3 }}>{r.outcome_note}</div>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

const PEER_METRIC_GROUPS = [
  { title: 'Valuasi', metrics: [['pe_ratio_comparison', 'P/E'], ['ps_ratio_comparison', 'P/S'], ['pb_ratio_comparison', 'P/B'], ['fcf_yield_comparison', 'FCF Yield']] },
  { title: 'Profitabilitas', metrics: [['gross_margin_comparison', 'Gross margin'], ['operating_margin_comparison', 'Operating margin'], ['net_margin_comparison', 'Net margin'], ['roe_comparison', 'ROE'], ['roa_comparison', 'ROA']] },
  { title: 'Growth & Leverage', metrics: [['revenue_growth_comparison', 'Revenue growth'], ['debt_to_equity_comparison', 'Debt/Equity']] },
]

function PercentileBar({ label, comp, groupSize, benchmark }) {
  const has = comp && comp.percentile !== null && comp.percentile !== undefined
  // roe/roa disimpan sebagai fraksi (0.44 = 44%) di Knowledge, BUKAN persen
  // seperti margin lain -- lihat financial_health.roe docstring. Kalikan
  // 100 + tanda % di sini biar konsisten sama tampilan metrik lain.
  const isFraction = label === 'ROE' || label === 'ROA'
  const tickerVal = has ? (isFraction ? comp.ticker_value * 100 : comp.ticker_value) : null
  const medianVal = has ? (isFraction ? comp.peer_group_median * 100 : comp.peer_group_median) : null
  const suffix = isFraction ? '%' : ''

  // Flip percentile untuk metrik "lower is better" — higher percentile = better
  // P/E tinggi (mahal) → percentile rendah → di-flip jadi tinggi (signal "baik")
  const displayPercentile = has && comp.direction === 'lower_is_better'
    ? 100 - comp.percentile
    : comp.percentile

  // Tooltip: jelaskan direction logic
  const directionText = comp?.direction === 'lower_is_better'
    ? `Rendah adalah lebih baik`
    : `Tinggi adalah lebih baik`

  return (
    <div style={{ opacity: has ? 1 : 0.45 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
        <span>{label}</span>
        {has ? (
          <span style={{ color: 'var(--dim)' }}>
            {fmtNum(tickerVal)}{suffix} <span style={{ color: 'var(--faint)' }}>vs median {fmtNum(medianVal)}{suffix}</span>
          </span>
        ) : (
          <span style={{ color: 'var(--faint)' }}>tidak tersedia</span>
        )}
      </div>
      <div style={{ position: 'relative', height: 5, background: 'var(--panel3)', borderRadius: 3 }}>
        {has && <div style={{ position: 'absolute', left: '50%', top: -2, width: 1, height: 9, background: 'var(--rule-strong)' }} />}
        {has && (
          <div
            style={{
              position: 'absolute', left: `${Math.max(0, Math.min(100, displayPercentile))}%`, top: -2,
              width: 9, height: 9, borderRadius: '50%', background: 'var(--accent)',
              transform: 'translateX(-50%)', border: '2px solid var(--panel2)',
            }}
            title={directionText}
          />
        )}
      </div>
      {has && (
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--faint)', marginTop: 2 }}>
          <span>{comp.peer_group_count}/{groupSize ?? '—'} peer</span>
          <span title={directionText}>{ordinal(Math.round(displayPercentile))} percentile ({benchmark})</span>
        </div>
      )}
    </div>
  )
}

function PeerComparisonCard({ peer, aiNarrative }) {
  if (!peer) return <p className="narrative" style={{ fontSize: 12, color: 'var(--faint)' }}>Belum ada data peer comparison untuk ticker ini.</p>

  const pg = peer.peer_group || {}
  const [benchmark, setBenchmark] = useState('sector')

  return (
    <div className="mcell">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div className="mcell-label" style={{ marginBottom: 0 }}>vs {pg.group_size ?? 0} peer{pg.sector ? ` · ${pg.sector}` : ''}</div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select
            value={benchmark}
            onChange={(e) => setBenchmark(e.target.value)}
            style={{
              fontSize: 11,
              padding: '4px 8px',
              background: 'var(--panel2)',
              border: '0.5px solid var(--rule)',
              borderRadius: 4,
              color: 'var(--text)',
              cursor: 'pointer',
            }}
          >
            <option value="sector">Benchmark: Sector</option>
            <option value="universe" disabled>Benchmark: Universe (Phase 2)</option>
          </select>
          {peer.low_sample_size && <span className="pill warn">sampel kecil</span>}
        </div>
      </div>
      <p style={{ margin: '0 0 14px', fontSize: 10.5, color: 'var(--faint)' }}>
        Posisi diukur pakai percentile, bukan skala nilai mentah — beberapa metrik punya outlier ekstrem di peer group.
      </p>

      {aiNarrative?.peer_note && (
        <div style={{ background: 'var(--panel3)', borderRadius: 6, padding: '8px 10px', marginBottom: 14, fontSize: 12, lineHeight: 1.5 }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 4 }}>Catatan AI</div>
          {aiNarrative.peer_note}
        </div>
      )}

      {PEER_METRIC_GROUPS.map((group) => (
        <div key={group.title} style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
            {group.title}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {group.metrics.map(([key, label]) => (
              <PercentileBar key={key} label={label} comp={peer[key]} groupSize={pg.group_size} benchmark={benchmark} />
            ))}
          </div>
        </div>
      ))}

      <div style={{ borderTop: '1px solid var(--rule)', paddingTop: 8, fontSize: 11, color: 'var(--dim)' }}>
        Dipakai lensa <strong style={{ color: 'var(--text)' }}>Quality</strong> (P/E) &amp; <strong style={{ color: 'var(--text)' }}>Multibagger</strong> (revenue growth) sebagai sinyal tambahan.
      </div>
    </div>
  )
}

const RISK_SEVERITY_META = {
  ekstrem: { label: 'Ekstrem', color: 'var(--bad)', bg: 'rgba(251,113,133,.08)' },
  tinggi: { label: 'Tinggi', color: 'var(--warn)', bg: 'rgba(251,191,122,.08)' },
}

function flagLabel(flagId) {
  return String(flagId || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function RiskFlagCard({ flag, assessedAt }) {
  const meta = RISK_SEVERITY_META[flag.severity] || RISK_SEVERITY_META.tinggi
  const isUndetermined = flag.status === 'undetermined'
  return (
    <div style={{ padding: 10, background: meta.bg, borderRadius: 6, borderLeft: `3px solid ${meta.color}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 4 }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{flagLabel(flag.flag_id)}</div>
        <span style={{ background: meta.color, color: 'var(--ink)', fontSize: 10, padding: '2px 6px', borderRadius: 3, whiteSpace: 'nowrap' }}>
          {flag.status === 'triggered' ? 'Triggered' : 'Undetermined'}
        </span>
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--dim)', marginBottom: 6, lineHeight: 1.5 }}>
        {isUndetermined ? <><span style={{ color: 'var(--faint)' }}>ⓘ Data belum cukup —</span> {flag.evidence_note}</> : flag.evidence_note}
      </div>
      <div style={{ display: 'flex', gap: 12, fontSize: 10, color: 'var(--faint)' }}>
        {flag.source && <span>Sumber: {flag.source}</span>}
        {assessedAt && <span>Dicek: {fmtIdDate(new Date(assessedAt))}</span>}
      </div>
    </div>
  )
}

function RiskFlagsBySeverity({ flags, assessedAt }) {
  const list = flags || []
  if (list.length === 0) return null

  const ekstrem = list.filter((f) => f.severity === 'ekstrem')
  const tinggi = list.filter((f) => f.severity === 'tinggi')
  const groups = [
    ['ekstrem', ekstrem],
    ['tinggi', tinggi],
  ].filter(([, items]) => items.length > 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginBottom: (list.length ? 10 : 0) }}>
      {groups.map(([severity, items]) => {
        const meta = RISK_SEVERITY_META[severity]
        return (
          <div key={severity}>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: meta.color, textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
              {meta.label} ({items.length})
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {items.map((f, i) => (
                <RiskFlagCard flag={f} assessedAt={assessedAt} key={`${severity}${i}`} />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function sectionBarColor(score) {
  if (score >= 70) return 'var(--good)'
  if (score >= 40) return 'var(--warn)'
  return 'var(--bad)'
}

function PenaltyRow({ label, applied, reasonText }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: applied ? 'var(--warn)' : 'var(--rule-strong)', flexShrink: 0 }} />
      <span style={{ color: applied ? 'var(--text)' : 'var(--dim)' }}>{label}</span>
      <span style={{ color: applied ? 'var(--warn)' : 'var(--faint)' }}>— {applied ? reasonText : 'tidak diterapkan'}</span>
    </div>
  )
}

function ConfidenceDetail({ confidence }) {
  const overall = confidence.overall || {}
  const sections = Object.entries(confidence.by_section || {})
  const peerP = confidence.peer_penalty || {}
  const contextP = confidence.context_penalty || {}
  const recencyP = confidence.recency_penalty || {}

  return (
    <div className="mcell">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
        <div>
          <div style={{ fontSize: 24, fontWeight: 700, fontFamily: 'var(--mono)' }}>{overall.score?.toFixed(1) ?? '—'}%</div>
          <div style={{ fontSize: 11, color: 'var(--dim)' }}>Confidence keseluruhan</div>
        </div>
        <span className={`pill ${bandClass(overall.band)}`}>{overall.band || '—'}</span>
      </div>

      <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
        Kelengkapan per Section
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
        {sections.map(([name, sec]) => (
          <div key={name}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 3 }}>
              <span>{prettyLabel(name)}</span>
              <span style={{ color: 'var(--dim)' }}>{sec.filled}/{sec.expected}</span>
            </div>
            <div style={{ height: 5, background: 'var(--panel3)', borderRadius: 3 }}>
              <div style={{ height: '100%', width: `${sec.score}%`, background: sectionBarColor(sec.score), borderRadius: 3 }} />
            </div>
          </div>
        ))}
      </div>

      <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
        Penalti Tambahan
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 14 }}>
        <PenaltyRow label="Peer penalty" applied={peerP.applied} reasonText={peerP.reason} />
        <PenaltyRow
          label="Context penalty (Layer 1)"
          applied={contextP.applied}
          reasonText={`degraded: ${(contextP.components_degraded || []).join(', ')}`}
        />
        <PenaltyRow label="Recency penalty" applied={recencyP.applied} reasonText={recencyP.reason} />
      </div>

      <div style={{ borderTop: '1px solid var(--rule)', paddingTop: 8, fontSize: 11, color: 'var(--dim)' }}>
        Evidence age: {confidence.evidence_age_days ?? '—'} hari · Dipakai semua 3 lensa Reasoning sebagai damper skor kalau confidence rendah.
      </div>
    </div>
  )
}

function ModalBody({ data, context, aiNarrative }) {
  const { aggregator, reasoning, risk, confidence, catalyst, peer, knowledge, evidence, historical } = data
  const anySection = aggregator || reasoning || risk || confidence || catalyst || knowledge || evidence || historical

  if (!anySection) return <div className="empty">Tidak ada detail untuk ticker ini.</div>

  const synthesis = aggregator?.synthesis

  // Modal fokus per stage sesuai halaman asal klik — bukan dump semua
  // sekaligus (lihat catatan di STAGE_CONTEXTS). Konteks yang nggak
  // punya section khusus (screening/peer/dst) fallback ke grup Evidence,
  // karena itu paling relevan sebagai "profil dasar" ticker.
  const showReasoning = context === 'reasoning' || context === 'aggregator'
  const showRisk = context === 'risk'
  const showConfidence = context === 'confidence'
  const showCatalyst = context === 'catalyst'
  const showKnowledge = context === 'knowledge'
  const showHistorical = context === 'historical'
  const showPeer = context === 'peer'
  const showEvidence = !STAGE_CONTEXTS.has(context)

  return (
    <>
      {showEvidence && (
        <CompanyHeaderSection profile={evidence?.company_profile} fundamental={evidence?.fundamental} />
      )}

      {showReasoning && aggregator?.halted && (
        <div className="msection">
          <div className="flag">
            <strong>HALTED</strong> — {aggregator.halt_reason || 'red flag severity ekstrem terpicu'}.
            Saham ini tidak diteruskan ke modul reasoning (hard-gate keselamatan).
          </div>
        </div>
      )}

      {showReasoning && (synthesis || (reasoning && !aggregator?.halted)) && (
        <div className="msection" id="sec-reasoning">
          <div className="msection-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>Reasoning — 3 Lensa</span>
            {synthesis && (
              <span className={`pill ${synthesis.full_convergence ? 'ok' : 'neutral'}`}>
                {synthesis.full_convergence ? 'konvergen' : 'divergen'}
              </span>
            )}
            {synthesis?.confidence && (
              <span className={`pill ${bandClass(synthesis.confidence.band)}`}>
                confidence {synthesis.confidence.score.toFixed(0)} · {synthesis.confidence.band}
              </span>
            )}
            {synthesis?.surprise != null && (
              <span className="pill neutral">surprise {synthesis.surprise.toFixed(2)}</span>
            )}
          </div>

          {synthesis?.narrative && <p className="narrative" style={{ marginBottom: 12 }}>{synthesis.narrative}</p>}

          {reasoning && !aggregator?.halted && (
            <div className="lens-grid">
              {MODULES.map((key) => {
                const o = reasoning[key]
                if (!o) return null
                const metricEntries = Object.entries(o.key_metrics || {})
                const hasDetail =
                  (o.positive_factors || []).length > 0 ||
                  (o.negative_factors || []).length > 0 ||
                  (o.knowledge_gaps || []).length > 0 ||
                  (o.flag_responses || []).length > 0
                return (
                  <div className="lens-card" key={key}>
                    <div className="lens-card-mod">{MODULE_LABELS[key]}</div>
                    <span className={`pill ${stanceClass(o.stance)}`}>{prettyStance(o.stance)}</span>
                    {o.stance_rationale && <p className="lens-card-rationale">{o.stance_rationale}</p>}
                    {metricEntries.length > 0 && (
                      <div className="lens-card-metrics">
                        {metricEntries.slice(0, 3).map(([k, v]) => (
                          <div className="lens-card-metric" key={k}>
                            <span>{prettyLabel(k)}</span>
                            <b>{fmtMetricValue(v)}</b>
                          </div>
                        ))}
                      </div>
                    )}
                    <div style={{ marginTop: 8, fontSize: 11, color: 'var(--faint)' }}>
                      conf {o.confidence?.score?.toFixed(0) ?? '—'}/{o.confidence?.band ?? '—'}
                    </div>
                    {hasDetail && (
                      <details className="lens-details">
                        <summary>Detail lainnya</summary>
                        {(o.positive_factors || []).map((f, i) => (
                          <div className="factor pos" key={`p${i}`}>+ {f}</div>
                        ))}
                        {(o.negative_factors || []).map((f, i) => (
                          <div className="factor neg" key={`n${i}`}>− {f}</div>
                        ))}
                        {(o.knowledge_gaps || []).length > 0 && (
                          <div className="factor" style={{ color: 'var(--faint)' }}>
                            Data kurang: {o.knowledge_gaps.join(', ')}
                          </div>
                        )}
                        {(o.flag_responses || []).map((fr, i) => (
                          <div className="factor" key={`fr${i}`} style={{ color: 'var(--warn)' }}>
                            ⚑ {fr.flag_id} ({fr.impact}): {fr.rationale}
                          </div>
                        ))}
                      </details>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {(synthesis?.agreements || []).length > 0 && (
            <div style={{ marginBottom: 10 }}>
              {synthesis.agreements.map((a, i) => (
                <div className="agreement-item" key={`agr${i}`}>→ {a.claim}</div>
              ))}
            </div>
          )}

          {(synthesis?.divergences || []).map((d, i) => (
            <div className="lens-box" key={`div${i}`}>
              <div className="lens-head">
                <span>{d.claim}</span>
                <span style={{ color: 'var(--faint)', fontSize: 11 }}>akar: {d.root_cause}</span>
              </div>
              {(d.modules || []).map((m, j) => (
                <div className="factor" key={j} style={{ color: 'var(--dim)' }}>
                  {MODULE_LABELS[m.module] || m.module}: {m.position}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {showRisk && (risk?.flags?.length > 0 || risk?.red_flags?.length > 0) && (
        <div className="msection" id="sec-risk">
          <div className="msection-title">
            Risk / Red Flags{risk.risk_score != null ? ` (score ${risk.risk_score.toFixed(0)})` : ''}
          </div>
          <RiskFlagsBySeverity flags={risk.flags} assessedAt={risk.assessed_at} />
          {(risk.red_flags || []).map((f, i) => (
            <div className={`flag${f.severity === 'medium' ? ' medium' : ''}`} key={`rf${i}`}>
              <strong>{f.flag_type}</strong> ({f.severity}) — {f.description}
            </div>
          ))}
        </div>
      )}

      {showConfidence && confidence && (
        <div className="msection" id="sec-confidence">
          <div className="msection-title">Confidence Report</div>
          <ConfidenceDetail confidence={confidence} />
        </div>
      )}

      {showCatalyst && catalyst && (
        <div className="msection" id="sec-catalyst">
          <div className="msection-title">Katalis Mendatang</div>
          <CatalystCountdownCard catalyst={catalyst} aiNarrative={aiNarrative} />
          <CatalystHistoryBar history={catalyst.resolved_history} />
        </div>
      )}

      {showPeer && (
        <div className="msection" id="sec-peer">
          <div className="msection-title">Peer Comparison</div>
          <PeerComparisonCard peer={peer} aiNarrative={aiNarrative} />
        </div>
      )}

      {showKnowledge && knowledge && (
        <div className="msection" id="sec-knowledge">
          <AiNarrativeBlock aiNarrative={aiNarrative} />
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--dim)', letterSpacing: '.04em', textTransform: 'uppercase', margin: '18px 0 10px' }}>
            Data Pendukung Lengkap
          </div>
          <KnowledgeDetailCards knowledge={knowledge} evidence={evidence} />
        </div>
      )}

      {showEvidence && evidence && (
        <div className="msection" id="sec-evidence">
          <div className="msection-title">Evidence — Sumber Data</div>

          <PriceSnapshotBlock priceMarket={evidence.price_market} />

          {evidence.analyst_estimates && (
            <AnalystEstimatesBlock
              estimates={evidence.analyst_estimates}
              currentPrice={evidence.price_market?.last_price ?? evidence.price_market?.close}
            />
          )}

          <DilutionCallout changePct={evidence.fundamental?.shares_outstanding_change_12m} />

          <FundamentalRatiosGrid fundamental={evidence.fundamental} />

          <CrossReferenceCallout notes={evidence.fundamental?.cross_reference_notes} />

          {(evidence.fundamental?.quarterly_data || []).length > 0 && (
            <QuarterlyFinancialsTable quarters={evidence.fundamental.quarterly_data} />
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginTop: 4 }}>
            <SecFilingsBlock filings={evidence.sec_filings} />
            <NewsBlock news={evidence.news} />
          </div>

          <RawDataTable evidence={evidence} />
        </div>
      )}

      {showEvidence && evidence?.institutional_activity && (
        <InsiderActivitySection activity={evidence.institutional_activity} />
      )}

      {showEvidence && evidence?.institutional_ownership && (
        <InstitutionalHoldersSection ownership={evidence.institutional_ownership} />
      )}

      {showHistorical && historical && (historical.entries || []).length > 0 && (
        <div className="msection" id="sec-historical">
          <div className="msection-title">
            Historical Tracking ({historical.total_entries || historical.entries.length} snapshot)
          </div>
          {historical.entries.slice().reverse().map((e, i) => {
            const ao = e.aggregator_output || {}
            const syn = ao.synthesis
            return (
              <div className="lens-box" key={i}>
                <div className="lens-head">
                  <span>{e.analyzed_at?.slice(0, 10) || '—'}</span>
                  <span>
                    {ao.halted ? (
                      <span className="pill bad">halted</span>
                    ) : syn?.full_convergence ? (
                      <span className="pill ok">konvergen</span>
                    ) : (
                      <span className="pill neutral">divergen</span>
                    )}
                  </span>
                </div>
                {syn?.narrative && (
                  <div className="factor" style={{ color: 'var(--dim)' }}>{syn.narrative}</div>
                )}
                <div className="factor" style={{ color: 'var(--faint)', fontSize: 11 }}>
                  {e.outcome != null ? 'outcome tercatat' : 'outcome: menunggu evaluasi v2.1'}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}

function CompanyHeaderSection({ profile, fundamental }) {
  if (!profile?.long_name && !fundamental?.sector) return null
  const teaser = firstSentence(profile?.business_summary)
  const hasMoreText = profile?.business_summary && teaser && profile.business_summary.length > teaser.length

  return (
    <div className="company-header">
      {profile?.long_name && <div className="company-name">{profile.long_name}</div>}
      {(fundamental?.sector || fundamental?.industry) && (
        <div className="company-badges">
          {fundamental.sector && <span className="pill neutral">{fundamental.sector}</span>}
          {fundamental.industry && <span className="pill neutral">{fundamental.industry}</span>}
        </div>
      )}
      {teaser && <p className="company-desc">{teaser}</p>}
      {hasMoreText && (
        <details className="company-desc-toggle">
          <summary>Baca deskripsi lengkap dari Yahoo Finance</summary>
          <p className="company-desc-full">{profile.business_summary}</p>
        </details>
      )}
      {(profile?.employees != null || profile?.city || profile?.website) && (
        <div className="company-meta">
          {profile.employees != null && <span>{fmtCompact(profile.employees)} karyawan</span>}
          {(profile.city || profile.country) && <span>{[profile.city, profile.country].filter(Boolean).join(', ')}</span>}
          {profile.website && (
            <a href={profile.website} target="_blank" rel="noreferrer">
              {profile.website.replace(/^https?:\/\//, '')}
            </a>
          )}
        </div>
      )}
    </div>
  )
}

function PriceSnapshotBlock({ priceMarket }) {
  if (!priceMarket) return null
  const close = priceMarket.last_price ?? priceMarket.close
  const lo52 = priceMarket.low_52w
  const hi52 = priceMarket.high_52w
  const pos52 = lo52 != null && hi52 != null && hi52 > lo52 && close != null
    ? Math.max(0, Math.min(100, ((close - lo52) / (hi52 - lo52)) * 100))
    : null
  const points = sparklinePoints(priceMarket.price_history)

  return (
    <div className="mrow" style={{ marginBottom: 4 }}>
      <div className="mcell">
        <div className="mcell-label">Close</div>
        <div className="mcell-val">${fmtNum(close, 2)}</div>
      </div>
      <div className="mcell">
        <div className="mcell-label">Open / High / Low</div>
        <div className="mcell-val" style={{ fontSize: 13 }}>
          ${fmtNum(priceMarket.open, 2)} / ${fmtNum(priceMarket.high, 2)} / ${fmtNum(priceMarket.low, 2)}
        </div>
      </div>
      <div className="mcell">
        <div className="mcell-label">Market Cap</div>
        <div className="mcell-val">{fmtMoney(priceMarket.market_cap)}</div>
      </div>
      <div className="mcell">
        <div className="mcell-label">Volume</div>
        <div className="mcell-val" style={{ fontSize: 15 }}>{fmtCompact(priceMarket.volume)}</div>
      </div>
      <div className="mcell">
        <div className="mcell-label">Shares Outstanding</div>
        <div className="mcell-val" style={{ fontSize: 15 }}>{fmtCompact(priceMarket.shares_outstanding)}</div>
      </div>
      <div className="mcell">
        <div className="mcell-label">Beta</div>
        <div className="mcell-val">{priceMarket.beta != null ? fmtNum(priceMarket.beta, 2) : '—'}</div>
      </div>

      {pos52 != null && (
        <div className="mcell" style={{ gridColumn: '1 / -1' }}>
          <div className="mcell-label">52 Minggu</div>
          <div className="range-bar">
            <div className="range-bar-fill" style={{ width: `${pos52}%` }} />
            <div className="range-bar-mark" style={{ left: `${pos52}%`, background: 'var(--text)' }} />
          </div>
          <div className="range-bar-labels">
            <span>low ${fmtNum(lo52, 2)}</span>
            <span>high ${fmtNum(hi52, 2)}</span>
          </div>
        </div>
      )}

      {points && (
        <div className="mcell" style={{ gridColumn: '1 / -1' }}>
          <div className="mcell-label">Tren 1 Tahun</div>
          <svg viewBox="0 0 300 56" width="100%" height="40" preserveAspectRatio="none">
            <polyline points={points} fill="none" stroke="var(--good)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      )}
    </div>
  )
}

const REV_ESTIMATE_LABELS = { '0q': 'Kuartal Ini', '+1q': 'Kuartal Depan', '0y': 'Tahun Ini', '+1y': 'Tahun Depan' }

function AnalystEstimatesBlock({ estimates, currentPrice }) {
  const hasTargets = estimates.target_low != null && estimates.target_high != null
  const hasEps = (estimates.eps_surprise_history || []).length > 0
  const hasRevEst = (estimates.revenue_estimates || []).length > 0
  const priceHistory = estimates.price_target_history || []

  if (!hasTargets && !hasEps && !hasRevEst) {
    return (
      <p className="narrative" style={{ fontSize: 12, color: 'var(--faint)', marginBottom: 12 }}>
        Belum ada cakupan analis untuk ticker ini.
      </p>
    )
  }

  const range = hasTargets ? estimates.target_high - estimates.target_low : 0
  const targetPos = hasTargets && range > 0 && currentPrice != null
    ? Math.max(0, Math.min(100, ((currentPrice - estimates.target_low) / range) * 100))
    : null
  const meanPos = hasTargets && range > 0 && estimates.target_mean != null
    ? Math.max(0, Math.min(100, ((estimates.target_mean - estimates.target_low) / range) * 100))
    : null
  const upsidePct = currentPrice && estimates.target_mean != null
    ? ((estimates.target_mean - currentPrice) / currentPrice) * 100
    : null

  return (
    <div style={{ marginBottom: 14 }}>
      {hasTargets && (
        <div className="mcell" style={{ marginBottom: 10 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <div className="mcell-label" style={{ marginBottom: 0 }}>
              Price Target Konsensus{estimates.num_analyst_opinions ? ` · ${estimates.num_analyst_opinions} analis` : ''}
            </div>
            {estimates.recommendation_key && (
              <span className={`pill ${ratingClass(estimates.recommendation_key)}`}>{prettyStance(estimates.recommendation_key)}</span>
            )}
          </div>
          <div className="range-bar">
            {targetPos != null && <div className="range-bar-mark" style={{ left: `${targetPos}%`, background: 'var(--text)' }} />}
            {meanPos != null && <div className="range-bar-mark" style={{ left: `${meanPos}%`, background: 'var(--accent)' }} />}
          </div>
          <div className="range-bar-labels">
            <span>low ${fmtNum(estimates.target_low, 2)}</span>
            {currentPrice != null && <span>sekarang ${fmtNum(currentPrice, 2)}</span>}
            <span>mean ${fmtNum(estimates.target_mean, 2)}{upsidePct != null ? ` (${fmtPct(upsidePct, 1)})` : ''}</span>
            <span>high ${fmtNum(estimates.target_high, 2)}</span>
          </div>

          {priceHistory.length > 0 && (
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
              <div className="mcell-label" style={{ marginBottom: 8, fontSize: 11 }}>Historical Targets</div>
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 80 }}>
                {priceHistory.slice(-6).map((snap, i) => {
                  const maxVal = Math.max(...priceHistory.map(s => s.target_mean || 0))
                  const height = maxVal > 0 ? (snap.target_mean / maxVal) * 100 : 0
                  return (
                    <div key={i} style={{ flex: 1, textAlign: 'center', fontSize: 9 }}>
                      <div
                        style={{
                          height: `${height}%`,
                          background: 'linear-gradient(to top, var(--accent), rgba(100,150,200,0.3))',
                          borderRadius: '2px 2px 0 0',
                          minHeight: '2px',
                          marginBottom: 2,
                        }}
                        title={`$${fmtNum(snap.target_mean, 2)}`}
                      />
                      <div style={{ color: 'var(--faint)', fontSize: 8 }}>${fmtNum(snap.target_mean, 0)}</div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {hasEps && (
        <div style={{ marginBottom: 10 }}>
          <div className="mcell-label" style={{ marginBottom: 6 }}>EPS Surprise — 4 Kuartal Terakhir</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--sans)', fontSize: 12.5 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--rule)', color: 'var(--faint)', textAlign: 'right' }}>
                <th style={{ padding: '4px 8px 8px 0', fontWeight: 600, textAlign: 'left' }}>Kuartal</th>
                <th style={{ padding: '4px 8px 8px', fontWeight: 600 }}>Actual</th>
                <th style={{ padding: '4px 8px 8px', fontWeight: 600 }}>Estimate</th>
                <th style={{ padding: '4px 0 8px 8px', fontWeight: 600 }}>Surprise</th>
              </tr>
            </thead>
            <tbody>
              {estimates.eps_surprise_history.map((e, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--rule)' }}>
                  <td style={{ padding: '6px 8px 6px 0' }}>{e.quarter}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>${fmtNum(e.eps_actual, 2)}</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--faint)' }}>${fmtNum(e.eps_estimate, 2)}</td>
                  <td
                    style={{
                      padding: '6px 0 6px 8px', textAlign: 'right', fontFamily: 'var(--mono)', fontWeight: 600,
                      color: e.surprise_pct >= 0 ? 'var(--good)' : 'var(--bad)',
                    }}
                  >
                    {fmtPct(e.surprise_pct, 1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hasRevEst && (
        <div>
          <div className="mcell-label" style={{ marginBottom: 6 }}>Estimasi Revenue Ke Depan</div>
          <div className="mrow">
            {estimates.revenue_estimates.map((r) => (
              <div className="mcell" key={r.period}>
                <div className="mcell-label">{REV_ESTIMATE_LABELS[r.period] || r.period}</div>
                <div className="mcell-val" style={{ fontSize: 15 }}>
                  {fmtMoney(r.avg)}{' '}
                  {r.growth != null && <span style={{ fontSize: 11, color: 'var(--good)', fontWeight: 400 }}>{fmtPct(r.growth, 1)}</span>}
                </div>
                <div style={{ fontSize: 10.5, color: 'var(--faint)', marginTop: 2 }}>
                  range {fmtMoney(r.low)}–{fmtMoney(r.high)}{r.num_analysts ? ` · ${r.num_analysts} analis` : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <p className="narrative" style={{ fontSize: 10.5, color: 'var(--faint)', marginTop: 8 }}>
        Histori "revenue actual vs estimate" bertahun-tahun tidak tersedia gratis dari Yahoo — cuma EPS 4 kuartal terakhir + estimasi forward yang ada.
      </p>
    </div>
  )
}

// Ratakan object evidence yang nested jadi baris {field, value} dot-path.
// Array (quarterly_data, news.items, dst) SENGAJA tidak di-drill — sudah
// punya tampilan sendiri di block lain modal ini, drill di sini cuma bikin
// tabel meledak baris-nya tanpa tambahan info.
function flattenEvidence(obj, prefix = '') {
  const rows = []
  for (const [k, v] of Object.entries(obj || {})) {
    const path = prefix ? `${prefix}.${k}` : k
    if (Array.isArray(v)) {
      rows.push({ field: path, value: v.length > 0 ? `[${v.length} item]` : null })
    } else if (v !== null && typeof v === 'object') {
      rows.push(...flattenEvidence(v, path))
    } else {
      rows.push({ field: path, value: v })
    }
  }
  return rows
}

function formatFlatValue(v) {
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString() : String(Number(v.toFixed(4)))
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}

function RawDataTable({ evidence }) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const rows = useMemo(() => (open ? flattenEvidence(evidence) : []), [open, evidence])
  const filtered = useMemo(() => {
    if (!search.trim()) return rows
    const q = search.toLowerCase()
    return rows.filter((r) => r.field.toLowerCase().includes(q))
  }, [rows, search])

  function copyRows() {
    const text = filtered.map((r) => `${r.field}\t${r.value == null ? '' : formatFlatValue(r.value)}`).join('\n')
    navigator.clipboard?.writeText(text)
  }

  return (
    <div style={{ marginTop: 14 }}>
      <div className={`rawdata-bar${open ? ' open' : ''}`} onClick={() => setOpen(!open)}>
        <span>Lihat Semua Data (Tabel)</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {open && <span className="rawdata-meta">{rows.length} field</span>}
          <span className="rawdata-chev">▾</span>
        </span>
      </div>
      {open && (
        <div className="rawdata-panel">
          <div className="rawdata-panel-head">
            <input
              className="rawdata-search"
              placeholder='Cari field… misal "roe"'
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <button className="rawdata-btn" onClick={copyRows}>Salin</button>
          </div>
          <div className="rawdata-body">
            {filtered.map((r) => (
              <div className="rawdata-row" key={r.field}>
                <div className="rawdata-field">{r.field}</div>
                <div className={`rawdata-val${r.value == null ? ' empty' : ''}`}>
                  {r.value == null ? '(kosong)' : formatFlatValue(r.value)}
                </div>
              </div>
            ))}
          </div>
          <div className="rawdata-foot">Menampilkan {filtered.length} dari {rows.length} field</div>
        </div>
      )}
    </div>
  )
}

function DilutionCallout({ changePct }) {
  if (changePct == null || changePct <= DILUTION_WARN_THRESHOLD_PCT) return null
  return (
    <div className="flag medium" style={{ marginBottom: 14 }}>
      <strong>Shares outstanding naik {fmtNum(changePct, 1)}% dalam 12 bulan</strong> — field yang dipakai risk flag{' '}
      <code style={{ fontFamily: 'var(--mono)' }}>dilution_12m</code> (threshold {fmtNum(DILUTION_WARN_THRESHOLD_PCT, 0)}%).
    </div>
  )
}

function CrossReferenceCallout({ notes }) {
  if (!notes || notes.length === 0) return null
  return (
    <div className="flag medium" style={{ marginBottom: 14 }}>
      <strong>Cross-reference antar sumber tidak cocok</strong>
      {notes.map((n, i) => (
        <div key={i} style={{ marginTop: i === 0 ? 4 : 2 }}>{n}</div>
      ))}
    </div>
  )
}

const RATIO_FIELDS = [
  ['pe_ratio', 'P/E', (v) => `${fmtNum(v, 2)}x`],
  ['eps', 'EPS', (v) => `$${fmtNum(v, 2)}`],
  ['book_value_per_share', 'Book Value/Share', (v) => `$${fmtNum(v, 2)}`],
  ['gross_margin', 'Gross Margin', (v) => fmtPct(v * 100, 1)],
  ['operating_margin', 'Operating Margin', (v) => fmtPct(v * 100, 1)],
  ['roe', 'ROE', (v) => fmtPct(v * 100, 1)],
  ['roa', 'ROA', (v) => fmtPct(v * 100, 1)],
  ['current_ratio', 'Current Ratio', (v) => fmtNum(v, 2)],
  ['quick_ratio', 'Quick Ratio', (v) => fmtNum(v, 2)],
  ['free_cash_flow', 'Free Cash Flow', (v) => fmtMoney(v)],
  ['payout_ratio', 'Payout Ratio', (v) => fmtPct(v * 100, 1)],
  ['dividend_yield', 'Dividend Yield', (v) => fmtPct(v * 100, 1)],
  ['debt_to_equity', 'Debt/Equity', (v) => fmtNum(v, 2)],
  ['shares_outstanding_change_12m', 'Shares Out. Δ12m', (v) => fmtPct(v, 1)],
]

function MetricLabel({ field, label }) {
  const def = METRIC_DEFINITIONS[field]
  if (!def) return <div className="mcell-label">{label}</div>
  return (
    <div className="mcell-label tip-wrap">
      <span className="label-def">{label}</span>
      <span className="avail-tip" style={{ textTransform: 'none', letterSpacing: 'normal' }}>{def}</span>
    </div>
  )
}

function FundamentalRatiosGrid({ fundamental }) {
  if (!fundamental) return null
  const availability = fundamental.field_availability || {}
  const quality = fundamental.field_quality || {}
  return (
    <div className="mrow" style={{ marginTop: 4, marginBottom: 4 }}>
      {RATIO_FIELDS.map(([key, label, fmt]) => {
        const v = fundamental[key]
        if (v == null) {
          const info = AVAILABILITY_INFO[availability[key]] || AVAILABILITY_INFO.unavailable
          return (
            <div className="mcell dim" key={key}>
              <MetricLabel field={key} label={label} />
              <span className="tip-wrap">
                <span className="reason-tag has-tip">
                  <span className={`avail-dot ${info.dot}`} />
                  {info.label}
                </span>
                <span className="avail-tip">{info.reason}</span>
              </span>
            </div>
          )
        }
        const q = QUALITY_INFO[quality[key]] || QUALITY_INFO.verified
        return (
          <div className="mcell" key={key}>
            <span className={`quality-tag ${quality[key] || 'verified'}`} title={q.reason}>{q.label}</span>
            <MetricLabel field={key} label={label} />
            <div className="mcell-val" style={{ fontSize: 15 }}>{fmt(v)}</div>
          </div>
        )
      })}
    </div>
  )
}

function QuarterlyFinancialsTable({ quarters }) {
  const sorted = [...quarters].sort((a, b) => (b.fiscal_date || b.period || '').localeCompare(a.fiscal_date || a.period || ''))
  return (
    <div style={{ marginTop: 4, marginBottom: 4 }}>
      <div className="mcell-label" style={{ marginBottom: 6 }}>Kuartal Terakhir ({sorted.length} kuartal SEC)</div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--sans)', fontSize: 12.5 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--rule)', color: 'var(--faint)', textAlign: 'right' }}>
            <th style={{ padding: '4px 8px 8px 0', fontWeight: 600, textAlign: 'left' }}>Kuartal</th>
            <th style={{ padding: '4px 8px 8px', fontWeight: 600 }}>Revenue</th>
            <th style={{ padding: '4px 8px 8px', fontWeight: 600 }}>Gross Profit</th>
            <th style={{ padding: '4px 8px 8px', fontWeight: 600 }}>Net Income</th>
            <th style={{ padding: '4px 0 8px 8px', fontWeight: 600 }}>Cash From Ops</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((q, i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--rule)' }}>
              <td style={{ padding: '6px 8px 6px 0' }}>{q.fiscal_date || q.period}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>{fmtMoney(q.revenue)}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>{fmtMoney(q.gross_profit)}</td>
              <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>{fmtMoney(q.net_income)}</td>
              <td style={{ padding: '6px 0 6px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>{fmtMoney(q.cash_from_operations)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SecFilingsBlock({ filings }) {
  const items = filings?.items || []
  if (items.length === 0) return null
  const sorted = [...items].sort((a, b) => (b.filing_date || '').localeCompare(a.filing_date || ''))
  return (
    <div>
      <div className="mcell-label" style={{ marginBottom: 8 }}>SEC Filings ({items.length} total)</div>
      {sorted.slice(0, 5).map((f, i) => (
        <a key={i} href={f.url} target="_blank" rel="noreferrer" className="filing-row">
          <span>{f.filing_date ? fmtIdDate(new Date(f.filing_date + 'T00:00:00Z')) : '—'}</span>
          <span className="pill neutral">{f.form_type}</span>
        </a>
      ))}
      {items.length > 5 && <div style={{ fontSize: 11, color: 'var(--faint)' }}>+ {items.length - 5} filing lainnya</div>}
    </div>
  )
}

function NewsBlock({ news }) {
  const items = news?.items || []
  if (items.length === 0) return null
  const sorted = [...items].sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''))
  return (
    <div>
      <div className="mcell-label" style={{ marginBottom: 8 }}>Berita Terbaru ({items.length})</div>
      {sorted.slice(0, 5).map((n, i) => (
        <a key={i} href={n.url} target="_blank" rel="noreferrer" className="news-row">
          <div>{n.headline}</div>
          <div className="news-row-meta">{n.source}{n.published_at ? ` · ${fmtIdDate(new Date(n.published_at))}` : ''}</div>
        </a>
      ))}
    </div>
  )
}

const ID_MONTHS = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']

function fmtIdDate(d) {
  return `${d.getUTCDate()} ${ID_MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

// 13F wajib dilaporkan institusi (>$100M AUM) paling lambat 45 hari setelah
// kuartal tutup — jadi data kuartal berjalan belum akan ADA di manapun
// (SEC, Yahoo, siapapun) sampai deadline itu lewat, bukan soal cache basi
// di sisi kita. dateReportedStr = tanggal akhir kuartal yang datanya kita
// punya (mis. "2026-03-31"); fungsi ini hitung kapan kuartal BERIKUTNYA
// wajib dilaporkan.
function nextFilingDeadline(dateReportedStr) {
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

// Yahoo pakai pct_change=100.0 sebagai sentinel "posisi baru" (nggak bisa
// hitung % kenaikan dari basis 0 saham sebelumnya) — bukan literal "naik
// 100%" dari posisi lama.
function holderSignal(pctChange) {
  if (pctChange === null || pctChange === undefined) return { label: '—', tone: 'neutral' }
  if (pctChange >= 99.5) return { label: '🆕 Baru Masuk', tone: 'good' }
  if (pctChange > 0) return { label: `▲ +${pctChange.toFixed(1)}%`, tone: 'good' }
  if (pctChange < 0) return { label: `▼ ${pctChange.toFixed(1)}%`, tone: 'bad' }
  return { label: '— Tetap', tone: 'neutral' }
}

function InstitutionalHoldersSection({ ownership }) {
  const holders = ownership.top_holders || []
  const pct = ownership.percentage

  if ((pct === null || pct === undefined) && holders.length === 0) return null

  // Urutkan: posisi baru/nambah paling banyak duluan — lebih actionable
  // daripada urutan default Yahoo (yang cuma berdasar % kepemilikan).
  const sorted = [...holders].sort((a, b) => (b.pct_change ?? -Infinity) - (a.pct_change ?? -Infinity))
  const newCount = holders.filter((h) => h.pct_change >= 99.5).length
  const addedCount = holders.filter((h) => h.pct_change > 0 && h.pct_change < 99.5).length
  const reducedCount = holders.filter((h) => h.pct_change < 0).length

  const latestReportDate = holders.find((h) => h.date_reported)?.date_reported
  const deadline = latestReportDate ? nextFilingDeadline(latestReportDate) : null

  return (
    <div className="msection" id="sec-holders">
      <div className="msection-title">
        Institutional Holders
        {pct !== null && pct !== undefined && ` — ${(pct * 100).toFixed(1)}% dari total saham dipegang institusi`}
      </div>
      {holders.length === 0 ? (
        <p className="narrative">Detail per-institusi tidak tersedia (data mentah dari Yahoo Finance).</p>
      ) : (
        <>
          <div className="mrow" style={{ marginBottom: 10 }}>
            <div className="mcell">
              <div className="mcell-label">🆕 Baru Masuk</div>
              <div className="mcell-val" style={{ color: 'var(--good)' }}>{newCount}</div>
            </div>
            <div className="mcell">
              <div className="mcell-label">▲ Nambah Posisi</div>
              <div className="mcell-val" style={{ color: 'var(--good)' }}>{addedCount}</div>
            </div>
            <div className="mcell">
              <div className="mcell-label">▼ Kurangi Posisi</div>
              <div className="mcell-val" style={{ color: 'var(--bad)' }}>{reducedCount}</div>
            </div>
            <div className="mcell" style={{ opacity: 0.6 }}>
              <div className="mcell-label">⋯ Keluar Total dari Top 10</div>
              <div className="mcell-val" style={{ fontSize: 13 }}>tidak diketahui</div>
            </div>
          </div>
          <p className="narrative" style={{ fontSize: 10.5, color: 'var(--faint)', marginBottom: 10 }}>
            "Keluar total" belum bisa dideteksi — cache institutional_ownership cuma nyimpen snapshot kuartal terakhir, belum ada history buat dibandingkan.
          </p>
          {deadline && (
            <p className="narrative" style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 8 }}>
              Data 13F per {fmtIdDate(new Date(latestReportDate + 'T00:00:00Z'))} — ini yang terbaru tersedia di manapun (SEC, Yahoo, dll).
              13F wajib dilaporkan institusi maks. 45 hari setelah kuartal tutup, jadi kuartal berikutnya baru akan muncul
              sekitar {fmtIdDate(deadline)}, bukan karena data kita basi.
            </p>
          )}
          <p className="narrative" style={{ marginBottom: 10 }}>
            {newCount > 0 && <span style={{ color: 'var(--good)' }}>{newCount} institusi baru masuk</span>}
            {newCount > 0 && (addedCount > 0 || reducedCount > 0) && ' · '}
            {addedCount > 0 && <span style={{ color: 'var(--good)' }}>{addedCount} nambah posisi</span>}
            {addedCount > 0 && reducedCount > 0 && ' · '}
            {reducedCount > 0 && <span style={{ color: 'var(--bad)' }}>{reducedCount} kurangi posisi</span>}
            {newCount === 0 && addedCount === 0 && reducedCount === 0 && 'Tidak ada perubahan posisi signifikan dari laporan sebelumnya.'}
            {' '}(dari {holders.length} institusi terbesar, laporan 13F kuartalan terakhir)
          </p>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--sans)', fontSize: 12.5 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--rule)', color: 'var(--faint)', textAlign: 'left' }}>
                <th style={{ padding: '4px 8px 8px 0', fontWeight: 600 }}>Institusi</th>
                <th style={{ padding: '4px 8px 8px', fontWeight: 600, textAlign: 'right' }}>% Held</th>
                <th style={{ padding: '4px 8px 8px', fontWeight: 600, textAlign: 'right' }}>Shares</th>
                <th style={{ padding: '4px 8px 8px', fontWeight: 600, textAlign: 'right' }}>Value</th>
                <th style={{ padding: '4px 8px 8px', fontWeight: 600, textAlign: 'right' }}>Aktivitas</th>
                <th style={{ padding: '4px 0 8px 8px', fontWeight: 600, textAlign: 'right' }}>Dilaporkan</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((h, i) => {
                const signal = holderSignal(h.pct_change)
                const toneColor = signal.tone === 'good' ? 'var(--good)' : signal.tone === 'bad' ? 'var(--bad)' : 'var(--dim)'
                return (
                  <tr key={i} style={{ borderBottom: '1px solid var(--rule)' }}>
                    <td style={{ padding: '6px 8px 6px 0' }}>{h.holder}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>
                      {h.pct_held !== null && h.pct_held !== undefined ? `${h.pct_held.toFixed(2)}%` : '—'}
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>
                      {h.shares !== null && h.shares !== undefined ? h.shares.toLocaleString() : '—'}
                    </td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)' }}>{fmtMoney(h.value_usd)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'var(--mono)', fontWeight: 600, color: toneColor }}>
                      {signal.label}
                    </td>
                    <td style={{ padding: '6px 0 6px 8px', textAlign: 'right', color: 'var(--faint)', fontSize: 11 }}>
                      {h.date_reported || '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

function InsiderActivitySection({ activity }) {
  if (!activity || activity.status === 'missing') return null

  const filings = activity.recent_trades || []
  const count30d = activity.buy_count_30d || 0

  if (count30d === 0 && filings.length === 0) return null

  const statusColor = activity.status === 'ok' ? 'var(--good)' : 'var(--faint)'
  const convictionLevel = count30d >= 3 ? 'tinggi' : count30d === 2 ? 'sedang' : 'rendah'
  const convictionTone = count30d >= 3 ? 'good' : count30d === 2 ? 'neutral' : 'faint'

  return (
    <div className="msection" id="sec-insider">
      <div className="msection-title">
        Insider Activity (Form 4 Filings)
      </div>
      <div style={{ marginBottom: 12 }}>
        <p className="narrative">
          Insider/executive Form 4 filings dalam 30 hari terakhir sebagai indikator konviksi management terhadap prospek perusahaan.
        </p>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 8 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: statusColor }}>
              {count30d} Form 4 Filing{count30d !== 1 ? 's' : ''}
            </div>
            <div style={{ fontSize: 11, color: 'var(--faint)', marginTop: 2 }}>
              Conviction Level: <span style={{ color: convictionTone, fontWeight: 600 }}>{convictionLevel}</span>
            </div>
          </div>
          {count30d >= 2 && (
            <div className="pill" style={{ backgroundColor: 'var(--good)', color: 'var(--text-on-good)' }}>
              ✓ Insider aktif
            </div>
          )}
        </div>
      </div>

      <div className="mrow" style={{ marginBottom: 4 }}>
        <div className="mcell">
          <div className="mcell-label">Beli (30d)</div>
          <div className="mcell-val" style={{ color: 'var(--good)' }}>{activity.buy_count_30d ?? 0}</div>
        </div>
        <div className="mcell">
          <div className="mcell-label">Jual (30d)</div>
          <div className="mcell-val" style={{ color: (activity.sell_count_30d || 0) > 0 ? 'var(--bad)' : 'var(--dim)' }}>
            {activity.sell_count_30d ?? 0}
          </div>
        </div>
        <div className="mcell">
          <div className="mcell-label">Net Shares (30d)</div>
          <div className="mcell-val" style={{ fontSize: 15 }}>{fmtCompact(activity.net_shares_30d)}</div>
        </div>
        <div className="mcell">
          <div className="mcell-label">Top Buyer / Seller</div>
          <div className="mcell-val" style={{ fontSize: 12 }}>
            {activity.top_buyer || '—'} / {activity.top_seller || '—'}
          </div>
        </div>
      </div>

      {filings.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--faint)', marginBottom: 8 }}>
            Recent Filings:
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {filings.slice(0, 5).map((filing, i) => (
              <div key={i} style={{
                padding: '8px',
                backgroundColor: 'var(--bg-faint)',
                borderRadius: 4,
                fontSize: 12,
                borderLeft: '3px solid var(--dim)'
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontFamily: 'var(--mono)', color: 'var(--dim)' }}>
                    {filing.transaction_date}
                  </span>
                  <span style={{ fontSize: 10, color: 'var(--faint)' }}>
                    Form {filing.form_type}
                  </span>
                </div>
                <div style={{ marginTop: 4, color: 'var(--text-dim)' }}>
                  {filing.trader_name} ({filing.relationship})
                </div>
              </div>
            ))}
            {filings.length > 5 && (
              <div style={{ fontSize: 11, color: 'var(--faint)', fontStyle: 'italic' }}>
                … dan {filings.length - 5} filing lainnya
              </div>
            )}
          </div>
        </div>
      )}

      {count30d === 0 && (
        <p className="narrative" style={{ color: 'var(--faint)', fontSize: 11 }}>
          Tidak ada Form 4 filings dalam 30 hari terakhir.
        </p>
      )}
    </div>
  )
}

