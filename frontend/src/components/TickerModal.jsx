import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { RiskBadge } from './ThesisProof'
import DecisionPath from './DecisionPath'
import KnowledgeDetailCards from './KnowledgeDetailCards'
import CandleChart from './CandleChart'
import { DataSpark } from './Sparkline'
import { drawPath, useCountUp } from '../anim'
import {
  fmtPct, fmtMoney, fmtNum, ratingClass, stanceClass, prettyStance, bandClass, prettyLabel,
  fmtMetricValue, firstSentence, fmtCompact, sparklinePoints, trendSpanLabel, MODULE_LABELS,
  AVAILABILITY_INFO, QUALITY_INFO, METRIC_DEFINITIONS,
  personalActionClass, prettyAction, horizonLabel, prettyHorizon, horizonStatusInfo, isEntryDueForReview,
  outcomeClass, prettyOutcome,
  factorLabel, splitFactors, isLegacyBreakdown, FACTOR_AXIS,
  nextFilingDeadline, NEW_POSITION_SENTINEL, quarterWindow,
  readHistoricalEntry, readingNote,
} from '../format'

const DILUTION_WARN_THRESHOLD_PCT = 10.0 // sama dengan risk.py DILUTION_THRESHOLD_PCT

// Preferensi "Cara baca". MENYALA otomatis di kunjungan pertama lalu diingat:
// keterangan permanen di sepuluh readout + sepuluh baris section akan
// melipatgandakan tinggi modal, tapi tooltip-saja juga salah -- yang tidak
// tahu ada penjelasan tidak akan pernah mengarahkan kursor. Jadi ditunjukkan
// sekali, sesudah itu terserah user.
const READING_PREF_KEY = 'montrva.readingNotes'

function loadReadingPref() {
  try {
    const raw = localStorage.getItem(READING_PREF_KEY)
    if (raw === null) return true      // kunjungan pertama
    return raw === '1'
  } catch {
    return true                        // localStorage diblokir -> tetap membantu
  }
}

function saveReadingPref(on) {
  try { localStorage.setItem(READING_PREF_KEY, on ? '1' : '0') } catch { /* diblokir, abaikan */ }
}

const MODULES = ['multibagger', 'quality_compound', 'speculative']

// Halaman asal klik menentukan section mana yang TERBUKA -- bukan lagi section
// mana yang ADA. Nama-nama di sini = key activeView di App.jsx.
//
// [BERUBAH 2026-08-16] Versi lama menggerbang render-nya: `showRisk = context
// === 'risk'` dst, jadi satu response /api/ticker (9 stage, 172 KB, sudah
// diambil utuh) cuma dipakai satu stage dan 8 sisanya dibuang tanpa dirender.
// Akibat nyatanya: dari Knowledge kamu melihat "18/18 field terisi" tanpa apa
// pun yang memberi tahu bahwa ticker itu sedang membawa flag risiko menyala.
//
// Fokus per stage TETAP dijaga, cuma pindah lapisan: section asal terbuka dan
// bertanda, delapan sisanya terlipat jadi satu baris ringkasan dan BARU
// dirender saat dibuka (lihat SectionRow -- body-nya thunk, bukan elemen).
// Yang dilarang D-04 adalah mengompres 3 lensa jadi satu vonis; menampilkan
// tahap-tahapnya berdampingan justru kebalikannya.
//
// personal_aggregator/personal_historical tetap beda endpoint
// (/api/personal/ticker/<t>, §3 draft personal layer: lapisan pribadi tidak
// nebeng ke response publik) -- jadi simpul "Aksi pribadi" di jejak rantai
// TIDAK muncul di modal non-pribadi, bukan muncul kosong.
const CONTEXT_ORIGIN = {
  reasoning: 'reasoning',
  aggregator: 'synthesis',
  risk: 'risk',
  confidence: 'confidence',
  catalyst: 'catalyst',
  knowledge: 'knowledge',
  historical: 'historical',
  peer: 'peer',
  personal_aggregator: 'personal',
  personal_historical: 'personal',
}
const PERSONAL_CONTEXTS = new Set(['personal_aggregator', 'personal_historical'])
// Section yang isinya butuh /api/ticker/<t>/ai-narrative. Panggilannya tetap
// malas: konteks aslinya memicu langsung, section lain baru memicu kalau
// user benar-benar membukanya (lihat needNarrative di bawah).
const NARRATIVE_CONTEXTS = new Set(['knowledge', 'catalyst', 'peer'])

export default function TickerModal({ ticker, context, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [live, setLive] = useState(null) // null = loading, {stale:true} = failed, else fresh quote
  const [aiNarrative, setAiNarrative] = useState(null) // null = loading/n.a., else {narrative, cached, available}
  const [personalData, setPersonalData] = useState(null) // null = loading/n.a. (context bukan personal_*)
  const [narrativeAsked, setNarrativeAsked] = useState(false) // user membuka section yang butuh narasi
  const [notesOn, setNotesOn] = useState(loadReadingPref)

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
    setNarrativeAsked(false)
  }, [ticker, context])

  useEffect(() => {
    // Relevan buat modal Knowledge (qualitative + quantitative_highlights)
    // dan Catalyst (catalyst_note) — satu response yang sama dipakai dua
    // context sekaligus (1 API call, bukan 2). Context lain gak butuh ini,
    // jangan buang panggilan API walau ada cache.
    //
    // [2026-08-16] Ketiga section itu sekarang ADA di semua konteks, tapi
    // terlipat. Gerbangnya karena itu tidak dilebarkan ke semua konteks —
    // panggilannya nunggu section-nya benar-benar dibuka (narrativeAsked),
    // supaya membuka modal dari Risk tidak diam-diam membayar panggilan AI.
    if (!NARRATIVE_CONTEXTS.has(context) && !narrativeAsked) {
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
  }, [ticker, context, narrativeAsked])

  useEffect(() => {
    // Endpoint TERPISAH dari api.ticker() di atas (§3 draft personal layer:
    // lapisan pribadi tidak boleh ikut nebeng ke response publik). Cuma
    // di-fetch kalau modal dibuka dari salah satu view pribadi.
    if (!PERSONAL_CONTEXTS.has(context)) {
      setPersonalData(null)
      return
    }
    let cancelled = false
    setPersonalData(null)
    api
      .personalTicker(ticker)
      .then((d) => { if (!cancelled) setPersonalData(d) })
      .catch((e) => { if (!cancelled) setPersonalData({ error: String(e) }) })
    return () => { cancelled = true }
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
          <button
            className={`readbtn${notesOn ? ' on' : ''}`}
            onClick={() => { const next = !notesOn; setNotesOn(next); saveReadingPref(next) }}
            title="Keterangan cara membaca tiap angka — pilihanmu diingat"
          >
            <span className="readbtn-dot" />
            Cara baca
          </button>
          <button className="x" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="modal-body">
          {error && <div className="empty">Gagal memuat detail: {error}</div>}
          {!error && !data && <div className="loading">Memuat detail…</div>}
          {data && (
            <ModalBody
              data={data}
              context={context}
              aiNarrative={aiNarrative}
              personalData={personalData}
              onNeedNarrative={() => setNarrativeAsked(true)}
              notesOn={notesOn}
            />
          )}
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
  // Guard eksplisit di SEMUA cabang (bukan cuma yang lower_is_better) --
  // comp bisa null (bukan cuma comp.percentile) kalau peer group kurang
  // dari 3 buat metrik itu; tanpa guard ini "comp.percentile" di cabang
  // else meledak duluan sebelum sempat sampai ke `has && (...)` di JSX.
  const displayPercentile = !has
    ? null
    : comp.direction === 'lower_is_better'
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
  // DI ATAS early return: ticker tanpa data peer memang ada (cabang di bawah),
  // dan hook yang dipanggil bersyarat mengubah urutan hook antar render.
  const [benchmark, setBenchmark] = useState('sector')
  if (!peer) return <p className="narrative" style={{ fontSize: 12, color: 'var(--faint)' }}>Belum ada data peer comparison untuk ticker ini.</p>

  const pg = peer.peer_group || {}

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

function RiskLimitationSummary({ flags }) {
  const actualCount = flags.filter((f) => f.status === 'triggered').length
  const limitationCount = flags.filter((f) => f.status === 'undetermined').length
  if (actualCount === 0 && limitationCount === 0) return null

  return (
    <div style={{ marginBottom: 14, padding: '8px 10px', background: 'var(--panel3)', borderRadius: 6 }}>
      <div style={{ display: 'flex', gap: 16, fontSize: 11.5, marginBottom: 4 }}>
        <span style={{ color: 'var(--bad)' }}>⚠ {actualCount} Risiko Aktual</span>
        <span style={{ color: 'var(--faint)' }}>ⓘ {limitationCount} Keterbatasan Data</span>
      </div>
      <div style={{ fontSize: 10, color: 'var(--faint)', lineHeight: 1.5 }}>
        Tidak ada data ≠ tidak ada risiko — bagian "Keterbatasan Data" berarti belum bisa dinilai, bukan berarti aman.
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
    <div>
      <RiskLimitationSummary flags={list} />
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
  const consistencyP = confidence.consistency_penalty || {}

  return (
    <div className="mcell">
      <div className="conf-intro">
        <span className="ic">i</span>
        <span>
          Seberapa kuat <b>data</b> yang menopang ticker ini — beda dari confidence tiap lensa Reasoning di bawah
          (seberapa yakin modul itu pada kesimpulannya). Modul tidak pernah lebih yakin dari angka ini.
        </span>
      </div>
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
        <PenaltyRow
          label="Consistency penalty (antar-sumber)"
          applied={consistencyP.applied}
          reasonText={(consistencyP.notes || []).join('; ')}
        />
      </div>

      <div style={{ borderTop: '1px solid var(--rule)', paddingTop: 8, fontSize: 11, color: 'var(--dim)' }}>
        Evidence age: {confidence.evidence_age_days ?? '—'} hari · Dipakai semua 3 lensa Reasoning sebagai damper skor kalau confidence rendah.
      </div>
    </div>
  )
}

const ROOT_CAUSE_LABELS = {
  different_fields: 'Field data berbeda',
  different_weights: 'Bobot faktor berbeda',
  knowledge_gap: 'Data tidak lengkap',
  flag_response: 'Respons ke risk flag berbeda',
  context_reading: 'Interpretasi konteks makro berbeda',
}

// Panel penuh "Bobot Faktor" -- versi lengkap dari blok ringkas di kartu top
// pick Agregator Pribadi (ScoreDrivers). Di sini SEMUA faktor ditampilkan,
// bukan cuma 3+1, karena modal punya ruang dan ini tempat menelusuri skor
// baris per baris.
//
// Sebelumnya panel ini merender score_breakdown yang isinya SATU kunci
// turunan dari totalnya sendiri, diskalakan ke nilai maksimum di dalam dirinya
// sendiri -- artinya satu batang yang selalu selebar penuh, tidak pernah
// memberi informasi apa pun. Sejak reasoning.py mengisi sumbangan per faktor,
// panel ini akhirnya punya isi.
function FactorWeights({ breakdown, thesisScore }) {
  if (!breakdown || Object.keys(breakdown).length === 0 || isLegacyBreakdown(breakdown)) return null
  const { shown, total } = splitFactors(breakdown, Infinity, Infinity)
  const clamped = thesisScore != null && Math.abs(50 + total - thesisScore) > 0.5
  return (
    <div className="score-drivers" style={{ margin: '2px 0 10px' }}>
      <div className="sd-label">Bobot Faktor</div>
      {shown.map(([key, v]) => (
        <div key={key} className="sd-row">
          <span className="sd-name">{factorLabel(key)}</span>
          <span className="sd-track">
            <i
              style={{
                left: v > 0 ? '50%' : `${50 - (Math.abs(v) / FACTOR_AXIS) * 50}%`,
                width: `${Math.min(Math.abs(v) / FACTOR_AXIS, 1) * 50}%`,
                background: v > 0 ? 'var(--good)' : 'var(--bad)',
              }}
            />
          </span>
          <span className={`sd-val ${v > 0 ? 'pos' : 'neg'}`}>{v > 0 ? '+' : ''}{v.toFixed(1)}</span>
        </div>
      ))}
      <div className="sd-foot">
        total {total > 0 ? '+' : ''}{total.toFixed(1)} → skor {thesisScore != null ? thesisScore.toFixed(0) : '—'}
        {clamped && ' (terklamp)'}
      </div>
    </div>
  )
}

// Skor tesis tiap lensa sepanjang riwayat PUBLIK. Sumbernya
// historical.entries, yang sudah menyimpan thesis_score per modul di tiap
// snapshot -- jadi kolom Δ dan sparkline di bawah tidak menuntut data baru.
function publicSeries(historical) {
  const out = { multibagger: [], quality_compound: [], speculative: [] }
  for (const raw of historical?.entries || []) {
    const e = readHistoricalEntry(raw)
    for (const l of e?.lenses || []) {
      if (out[l.module] && l.thesis_score != null && Number.isFinite(l.thesis_score)) {
        out[l.module].push(l.thesis_score)
      }
    }
  }
  return out
}

// Tiga lensa sebagai GRID ANGKA, bukan tiga kartu prosa.
//
// Bentuk lamanya menaruh skor di dalam kalimat dan menyusun tiga kartu
// bersebelahan dengan tinggi mengikuti panjang prosa masing-masing -- angka
// ketiganya tidak pernah sejajar, jadi "mana yang paling kuat" harus dibaca,
// bukan dilihat. Isi lengkapnya tidak dibuang: semuanya pindah ke baris
// "detail" yang cuma dirender kalau dibuka.
function LensGrid({ reasoning, historical }) {
  const [open, setOpen] = useState(null)
  const series = useMemo(() => publicSeries(historical), [historical])

  return (
    <div className="numgrid-wrap">
      <table className="numgrid">
        <thead>
          <tr>
            <th className="l">Lensa</th>
            <ColHead label="Skor" note="skor" />
            <ColHead label="Δ run" note="delta" />
            <ColHead label="Riwayat" note="riwayat" align="c" />
            <ColHead label="Stance" note="stance" align="l" />
            <ColHead label="Keyakinan" note="keyakinan" align="l" />
            <ColHead label="Metrik teratas" note="metrik" align="l" />
            <th />
          </tr>
        </thead>
        <tbody>
          {MODULES.map((key, i) => (
            <LensRows
              key={key}
              module={key}
              output={reasoning[key]}
              series={series[key] || []}
              index={i}
              isOpen={open === key}
              onToggle={() => setOpen(open === key ? null : key)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function LensRows({ module, output: o, series, index, isOpen, onToggle }) {
  const sparkRef = useRef(null)
  const shown = useCountUp(o?.thesis_score ?? null, 2, index * 60)
  useEffect(() => { drawPath(sparkRef.current, { duration: 500, delay: index * 60 }) }, [index, series.length])

  if (!o) {
    return (
      <tr className="numgrid-empty">
        <td className="l">{MODULE_LABELS[module]}</td>
        <td colSpan={7} className="l faint">Belum ada data.</td>
      </tr>
    )
  }

  const metricEntries = Object.entries(o.key_metrics || {})
  const weightEntries = Object.entries(o.score_breakdown || {})
  const hasDetail =
    (o.positive_factors || []).length > 0 ||
    (o.negative_factors || []).length > 0 ||
    (o.knowledge_gaps || []).length > 0 ||
    (o.flag_responses || []).length > 0 ||
    weightEntries.length > 0 ||
    (o.context_used || []).length > 0 ||
    (o.fields_accessed || []).length > 0 ||
    !!o.stance_rationale

  // Δ terhadap snapshot sebelumnya. Deret riwayat SUDAH memuat skor run ini
  // (historical ditulis di run yang sama), jadi pembandingnya elemen kedua
  // dari belakang -- bukan yang terakhir, yang justru sama dengan skor kini.
  const prev = series.length >= 2 ? series[series.length - 2] : null
  const delta = prev != null && o.thesis_score != null ? +(o.thesis_score - prev).toFixed(2) : null

  return (
    <>
      <tr className="numgrid-row">
        <td className="l">
          <span className="ng-lens">{MODULE_LABELS[module]}</span>
        </td>
        <td className="ng-num" title="Skor kekuatan tesis lensa ini (0-100, netral 50)">{shown}</td>
        <td className={`ng-delta ${delta == null ? 'faint' : delta > 0 ? 'good' : delta < 0 ? 'bad' : 'faint'}`}>
          {delta == null ? '—' : delta === 0 ? '0,00' : `${delta > 0 ? '▲' : '▼'} ${Math.abs(delta).toFixed(2)}`}
        </td>
        <td className="c"><DataSpark values={series} pathRef={sparkRef} /></td>
        <td className="l"><span className={`pill ${stanceClass(o.stance)}`}>{prettyStance(o.stance)}</span></td>
        <td className="l">
          <span
            className="ng-stance"
            title={`Bukan Confidence Report. Ini seberapa yakin lensa ${MODULE_LABELS[module]} pada kesimpulannya sendiri — selalu ≤ Confidence Report.`}
          >
            {o.confidence?.score?.toFixed(0) ?? '—'} · {o.confidence?.band ?? '—'}
          </span>
        </td>
        <td className="l ng-stance">
          {metricEntries.length
            ? metricEntries.slice(0, 2).map(([k, v]) => `${prettyLabel(k)} ${fmtMetricValue(v)}`).join(' · ')
            : '—'}
        </td>
        <td>
          {hasDetail && <button className="ng-why" onClick={onToggle}>{isOpen ? 'tutup' : 'detail'}</button>}
        </td>
      </tr>
      {isOpen && hasDetail && (
        <tr className="numgrid-why">
          <td colSpan={8}>
            {o.stance_rationale && <p style={{ marginBottom: 8 }}>{o.stance_rationale}</p>}
            {metricEntries.length > 0 && (
              <div className="lens-card-metrics" style={{ marginBottom: 10 }}>
                {metricEntries.map(([k, v]) => (
                  <div className="lens-card-metric" key={k}>
                    <span>{prettyLabel(k)}</span>
                    <b>{fmtMetricValue(v)}</b>
                  </div>
                ))}
              </div>
            )}
            <FactorWeights breakdown={o.score_breakdown} thesisScore={o.thesis_score} />
                {(o.context_used || []).length > 0 && (
                  <div style={{ margin: '2px 0 10px' }}>
                    <div style={{ fontSize: 9.5, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 5 }}>
                      Konteks Layer 1
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                      {o.context_used.map((c, i) => (
                        <div key={i} style={{ fontSize: 10.5, color: 'var(--dim)' }}>
                          <span style={{ color: 'var(--accent)' }}>{prettyLabel(c.component)}</span> — {c.influence}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {(o.fields_accessed || []).length > 0 && (
                  <div style={{ margin: '2px 0 10px' }}>
                    <div style={{ fontSize: 9.5, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 5 }}>
                      Ditelusuri dari (Knowledge)
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                      {o.fields_accessed.map((f, i) => (
                        <span key={i} style={{ fontSize: 9.5, color: 'var(--dim)', background: 'var(--panel3)', padding: '2px 6px', borderRadius: 3 }}>
                          {prettyLabel(f)}
                        </span>
                      ))}
                    </div>
                    <div style={{ fontSize: 9.5, color: 'var(--faint)', marginTop: 4 }}>
                      Section Knowledge yang dibaca lensa ini — belum sampai ke field/Evidence individual.
                    </div>
                  </div>
                )}
                {(o.positive_factors || []).map((f, i) => (
                  <div className="factor pos" key={`p${i}`}>+ {f}</div>
                ))}
                {(o.negative_factors || []).map((f, i) => (
                  <div className="factor neg" key={`n${i}`}>− {f}</div>
                ))}
                {(o.knowledge_gaps || []).length > 0 && (
                  <div className="factor" style={{ color: 'var(--faint)' }}>
                    ⓘ Data belum tersedia (bukan sinyal negatif): {o.knowledge_gaps.join(', ')}
                  </div>
                )}
                {(o.flag_responses || []).map((fr, i) => (
                  <div className="factor" key={`fr${i}`} style={{ color: 'var(--warn)' }}>
                    ⚑ {fr.flag_id} ({fr.impact}): {fr.rationale}
                  </div>
                ))}
          </td>
        </tr>
      )}
    </>
  )
}

// Kepala kolom + aturan bacanya. Aturan kolom SELALU tampil (di luar tombol
// "Cara baca"): teksnya pendek, kepala tabel memang tempatnya, dan kolom
// tanpa aturannya justru yang paling sering disalahbaca -- "Δ run" dibaca
// "sejak awal", "Skor" dibaca sebagai probabilitas.
function ColHead({ label, note, align }) {
  return (
    <th className={align || ''}>
      {label}
      <span className="th-help">{readingNote('column', note)}</span>
    </th>
  )
}

// Deret skor tesis satu lensa dari riwayat pribadi, urut waktu.
//
// Datanya SUDAH tersimpan di personal_history.json sejak lama (14 snapshot
// untuk AAPL) tapi tidak pernah muncul di modal -- jadi skor Spekulatif yang
// naik 13 poin dalam satu run terbaca sama saja dengan skor yang diam sebulan.
// Entry paling tua tidak menyimpan thesis_score (lihat catatan di
// personal_checkpoint.py), jadi nilai kosong DIBUANG, bukan dijadikan 0 --
// nol akan menggambar jurang palsu di ujung kiri sparkline.
function thesisSeries(history, module) {
  return (history?.entries || [])
    .map((e) => (e.personal_call_set || {})[module]?.thesis_score)
    .filter((v) => v != null && Number.isFinite(v) && v > 0)
}

function PersonalCallTable({ callSet, history }) {
  const [open, setOpen] = useState(null)

  return (
    <div className="numgrid-wrap">
      <table className="numgrid">
        <thead>
          <tr>
            <th className="l">Lensa</th>
            <ColHead label="Skor" note="skor" />
            <ColHead label="Δ run" note="delta" />
            <ColHead label="Riwayat" note="riwayat" align="c" />
            <ColHead label="Stance" note="stance" align="l" />
            <ColHead label="Aksi" note="aksi" align="l" />
            <ColHead label="Horizon" note="horizon" align="l" />
            <th />
          </tr>
        </thead>
        <tbody>
          {MODULES.map((m, i) => (
            <PersonalCallRows
              key={m}
              module={m}
              call={callSet[m]}
              series={thesisSeries(history, m)}
              index={i}
              isOpen={open === m}
              onToggle={() => setOpen(open === m ? null : m)}
            />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PersonalCallRows({ module, call, series, index, isOpen, onToggle }) {
  const sparkRef = useRef(null)
  const score = call?.thesis_score
  const shown = useCountUp(score ?? null, 2, index * 60)

  useEffect(() => { drawPath(sparkRef.current, { duration: 500, delay: index * 60 }) }, [index, series.length])

  if (!call) {
    return (
      <tr className="numgrid-empty">
        <td className="l">{MODULE_LABELS[module]}</td>
        <td colSpan={7} className="l faint">Belum ada data.</td>
      </tr>
    )
  }

  // Δ dibandingkan ke snapshot SEBELUMNYA, bukan ke awal riwayat: yang ingin
  // dijawab kolom ini adalah "apa yang berubah di run ini", bukan "sudah
  // sejauh apa dari dulu" (itu tugas sparkline di sebelahnya).
  const prev = series.length >= 2 ? series[series.length - 2] : null
  const delta = prev != null && score != null ? +(score - prev).toFixed(2) : null
  const statusInfo = horizonStatusInfo(call.horizon_status)

  return (
    <>
      <tr className="numgrid-row">
        <td className="l">
          <span className="ng-lens">{MODULE_LABELS[module]}</span>
          <span className="ng-sub">conf {call.source_confidence?.toFixed(0) ?? '—'}</span>
        </td>
        <td className="ng-num" title="Skor kekuatan tesis lensa ini (0-100, netral 50) — beda dari confidence (kelengkapan data)">
          {shown}
        </td>
        <td className={`ng-delta ${delta == null ? 'faint' : delta > 0 ? 'good' : delta < 0 ? 'bad' : 'faint'}`}>
          {delta == null ? '—' : `${delta > 0 ? '▲' : delta < 0 ? '▼' : ''} ${Math.abs(delta).toFixed(2)}`}
        </td>
        <td className="c">
          <DataSpark values={series} pathRef={sparkRef} />
        </td>
        <td className="l ng-stance">{call.source_stance || '—'}</td>
        <td className="l">
          <span className={`pill ${personalActionClass(call.action)}`}>{prettyAction(call.action)}</span>
          {call.action_downgraded_from && <span className="pill warn" title={`Diturunkan dari ${prettyAction(call.action_downgraded_from)} — data lensa ini tipis`}>↓</span>}
        </td>
        <td className="l faint">
          {prettyHorizon(call.horizon)}
          {statusInfo && <span className="warn"> · {statusInfo.label}</span>}
        </td>
        <td>
          <button className="ng-why" onClick={onToggle}>{isOpen ? 'tutup' : 'kenapa'}</button>
        </td>
      </tr>
      {isOpen && (
        <tr className="numgrid-why">
          <td colSpan={8}>
            {call.action_downgraded_from && (
              <p className="warn" style={{ marginBottom: 6 }}>
                ⓘ Diturunkan dari <strong>{prettyAction(call.action_downgraded_from)}</strong> — data lensa ini tipis, belum cukup untuk eksposur penuh.
              </p>
            )}
            {call.action_rationale && <p>{call.action_rationale}</p>}
            {call.horizon_basis && <p className="faint" style={{ marginTop: 5 }}>{call.horizon_basis}</p>}
            {call.horizon_anchor && <p className="faint">Jangkar: {call.horizon_anchor}</p>}
            <div style={{ marginTop: 7 }}><RiskBadge call={call} /></div>
            <p className="faint" style={{ marginTop: 7 }}>
              {call.position_status === 'holding'
                ? <>Dipegang sejak {call.holding_since || '—'}
                    {call.unrealized_return_pct != null && (
                      <span className={call.unrealized_return_pct >= 0 ? 'good' : 'bad'}>
                        {' '}· {call.unrealized_return_pct >= 0 ? '+' : ''}{call.unrealized_return_pct.toFixed(1)}%
                      </span>
                    )}
                  </>
                : 'Belum dipegang'}
              {call.price_at_call != null && (
                <> · harga saat call ${call.price_at_call.toFixed(2)}
                  {call.benchmark_at_call != null && ` · S&P 500 ${call.benchmark_at_call.toFixed(0)}`}
                </>
              )}
            </p>
            <DecisionPath module={module} call={call} />
          </td>
        </tr>
      )}
    </>
  )
}

function PersonalDetailSection({ personalData, showHistory }) {
  if (!personalData) return <div className="loading">Memuat data pribadi…</div>
  if (personalData.error) return <div className="empty">Gagal memuat data pribadi: {personalData.error}</div>

  const callSet = personalData.call_set
  const history = personalData.history

  return (
    <>
      <div className="msection" id="sec-personal">
        <div className="msection-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span>Agregator Pribadi</span>
          <span className="pill neutral">privat · tidak dipublikasikan</span>
        </div>
        {!callSet ? (
          <p className="narrative" style={{ fontSize: 12, color: 'var(--faint)' }}>
            Belum ada rekomendasi pribadi untuk ticker ini — jalankan pipeline (Generate) dulu.
          </p>
        ) : (
          <PersonalCallTable callSet={callSet} history={history} />
        )}
      </div>

      {showHistory && history && (history.entries || []).length > 0 && (
        <div className="msection" id="sec-personal-history">
          <div className="msection-title">
            Riwayat Pribadi ({history.total_entries || history.entries.length} snapshot)
          </div>
          {history.entries.slice().reverse().map((e, i, arr) => {
            const cs = e.personal_call_set || {}
            const due = isEntryDueForReview(e.analyzed_at, cs)
            return (
              <div
                className="lens-box"
                key={i}
                style={due ? { background: 'rgba(251,191,122,.08)', border: '1px solid rgba(251,191,122,.25)' } : undefined}
              >
                <div className="lens-head">
                  <span>
                    {e.analyzed_at?.slice(0, 10) || '—'}
                    {i === 0 && <span style={{ color: 'var(--faint)', fontWeight: 400 }}> · terbaru</span>}
                    {i === arr.length - 1 && arr.length > 1 && (
                      <span style={{ color: 'var(--faint)', fontWeight: 400 }}> · snapshot pertama</span>
                    )}
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10.5 }}>
                    {due && !e.outcome && <span className="pill warn">layak ditinjau ulang</span>}
                    {!e.outcome && <span style={{ color: 'var(--faint)' }}>outcome: menunggu evaluasi</span>}
                  </span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 6 }}>
                  {MODULES.map((m) => {
                    if (!cs[m]) return null
                    const oc = e.outcome?.[m]
                    return (
                      <div key={m} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, flexWrap: 'wrap' }}>
                        <span style={{ color: 'var(--faint)', minWidth: 110 }}>{MODULE_LABELS[m]}</span>
                        <span className={`pill ${personalActionClass(cs[m].action)}`} style={{ fontSize: 10 }}>
                          {prettyAction(cs[m].action)}
                        </span>
                        <span style={{ color: 'var(--faint)' }}>{prettyHorizon(cs[m].horizon)}</span>
                        {oc && (
                          <span className={`pill ${outcomeClass(oc.classification)}`} style={{ fontSize: 10 }}>
                            {prettyOutcome(oc.classification)}
                            {oc.return_pct != null && ` (${oc.return_pct >= 0 ? '+' : ''}${oc.return_pct.toFixed(1)}%)`}
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </>
  )
}

function SynthesisSection({ synthesis }) {
  const agreements = synthesis?.agreements || []
  const divergences = synthesis?.divergences || []
  if (agreements.length === 0 && divergences.length === 0) return null

  return (
    <div style={{ marginBottom: 14 }}>
      {agreements.length > 0 && (
        <div style={{ marginBottom: divergences.length > 0 ? 12 : 0 }}>
          <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--good)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
            ✓ Kesepakatan ({agreements.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {agreements.map((a, i) => (
              <div key={`agr${i}`} style={{ background: 'rgba(74,222,128,.06)', border: '1px solid rgba(74,222,128,.2)', borderRadius: 8, padding: '9px 11px' }}>
                <div style={{ fontSize: 12, color: 'var(--text)' }}>{a.claim}</div>
                {(a.modules || []).length > 0 && (
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 5 }}>
                    {a.modules.map((m, j) => (
                      <span key={j} style={{ fontSize: 9, color: 'var(--faint)', background: 'var(--panel3)', padding: '1px 6px', borderRadius: 3, textTransform: 'uppercase' }}>
                        {MODULE_LABELS[m] || m}
                      </span>
                    ))}
                  </div>
                )}
                {(a.citations || []).length > 0 && (
                  <div style={{ fontSize: 9.5, color: 'var(--faint)', marginTop: 4 }}>
                    ↳ {a.citations.join(', ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {divergences.length > 0 && (
        <div>
          <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--warn)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
            ⚠ Perbedaan ({divergences.length})
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {divergences.map((d, i) => (
              <div key={`div${i}`} style={{ background: 'rgba(251,191,122,.06)', border: '1px solid rgba(251,191,122,.2)', borderRadius: 8, padding: '9px 11px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
                  <span style={{ fontSize: 12, color: 'var(--text)' }}>{d.claim}</span>
                  <span style={{ fontSize: 9.5, color: 'var(--warn)', background: 'rgba(251,191,122,.15)', padding: '2px 7px', borderRadius: 3, whiteSpace: 'nowrap' }}>
                    akar: {ROOT_CAUSE_LABELS[d.root_cause] || d.root_cause}
                  </span>
                </div>
                {(d.modules || []).map((m, j) => (
                  <div key={j} style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 2 }}>
                    <b style={{ color: 'var(--text)' }}>{MODULE_LABELS[m.module] || m.module}:</b> {m.position}
                  </div>
                ))}
                {(d.citations || []).length > 0 && (
                  <div style={{ fontSize: 9.5, color: 'var(--faint)', marginTop: 4 }}>
                    ↳ {d.citations.join(', ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function BullBearCase({ reasoning }) {
  const bulls = []
  const bears = []
  MODULES.forEach((key) => {
    const o = reasoning[key]
    if (!o) return
    ;(o.positive_factors || []).forEach((f) => bulls.push({ text: f, module: key }))
    ;(o.negative_factors || []).forEach((f) => bears.push({ text: f, module: key }))
  })
  if (bulls.length === 0 && bears.length === 0) return null

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 14 }}>
      <div style={{ background: 'rgba(74,222,128,.06)', border: '1px solid rgba(74,222,128,.2)', borderRadius: 8, padding: 10 }}>
        <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--good)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
          Bull Case ({bulls.length})
        </div>
        {bulls.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--faint)' }}>Tidak ada argumen positif dari 3 lensa.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {bulls.map((b, i) => (
              <div key={i} style={{ fontSize: 11.5, color: 'var(--text)', lineHeight: 1.4 }}>
                <span style={{ fontSize: 9.5, color: 'var(--faint)', textTransform: 'uppercase', marginRight: 4 }}>[{MODULE_LABELS[b.module] || b.module}]</span>
                {b.text}
              </div>
            ))}
          </div>
        )}
      </div>
      <div style={{ background: 'rgba(251,113,133,.06)', border: '1px solid rgba(251,113,133,.2)', borderRadius: 8, padding: 10 }}>
        <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--bad)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
          Bear Case ({bears.length})
        </div>
        {bears.length === 0 ? (
          <div style={{ fontSize: 11, color: 'var(--faint)' }}>Tidak ada argumen negatif dari 3 lensa.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {bears.map((b, i) => (
              <div key={i} style={{ fontSize: 11.5, color: 'var(--text)', lineHeight: 1.4 }}>
                <span style={{ fontSize: 9.5, color: 'var(--faint)', textTransform: 'uppercase', marginRight: 4 }}>[{MODULE_LABELS[b.module] || b.module}]</span>
                {b.text}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// Jejak rantai — MENGGANTIKAN DecisionFlow, yang menampilkan 7 tahap yang sama
// tapi cuma sebagai ✓/○ "stage ini ada datanya". Pertanyaan yang sebenarnya
// muncul saat membuka satu ticker bukan "tahapnya jalan semua?" melainkan
// "kenapa akhirnya jadi begitu" — dan itu butuh ANGKA keputusan tiap tahap,
// bukan tanda centang. Lebarnya sengaja tidak pernah menyempit jadi satu:
// Reasoning bertiga, Aksi bertiga, dan simpul Sintesis di antaranya adalah
// HITUNGAN titik temu, bukan vonis (D-04/D-07).
function ChainStep({ label, value, sub, tone, here, wide, note }) {
  return (
    <div
      className={`mchain-step${here ? ' here' : ''}${note ? ' with-note' : ''}`}
      style={wide ? { minWidth: wide } : undefined}
    >
      <div className="mchain-k">{label}</div>
      <div className={`mchain-v${tone ? ` ${tone}` : ''}`}>{value}</div>
      {sub && <div className="mchain-s">{sub}</div>}
      {note && <div className="mchain-help">{note}</div>}
    </div>
  )
}

function ChainTrace({ data, personalData, originId, notesOn }) {
  // Keterangan diambil dari satu peta di format.js, bukan ditulis di sini --
  // dan `synthesis` butuh besar populasi run ini, jadi konteksnya diteruskan.
  const note = (key) => (notesOn
    ? readingNote('chain', key, { populationSize: data.aggregator?.synthesis?.population_baseline?.sample_size })
    : null)
  return <ChainTraceBody data={data} personalData={personalData} originId={originId} note={note} />
}

function ChainTraceBody({ data, personalData, originId, note }) {
  const { evidence, knowledge, peer, confidence, risk, reasoning, aggregator } = data
  const meta = knowledge?.metadata
  const halted = !!(aggregator?.halted || risk?.halted)

  const screenFlags = knowledge?.screening_flags || []
  const sources = meta?.sources_used || []
  const gaps = meta?.missing_fields || []

  const flags = risk?.flags || []
  const triggered = flags.filter((f) => f.status === 'triggered')
  const legacyFlags = (risk?.red_flags || []).length

  const scores = MODULES.map((m) => reasoning?.[m]?.thesis_score).filter((s) => s != null)
  const syn = aggregator?.synthesis
  const calls = personalData?.call_set
    ? MODULES.map((m) => personalData.call_set[m]).filter(Boolean)
    : []

  const nextCat = (data.catalyst?.catalysts || [])[0]

  return (
    <div className="mchain">
      <ChainStep
        label="Screening" note={note('screening')} here={originId === 'screening'}
        value={knowledge ? (screenFlags.length ? `${screenFlags.length} flag` : 'lolos') : '—'}
        tone={knowledge && !screenFlags.length ? 'good' : screenFlags.length ? 'warn' : 'faint'}
        sub={knowledge ? [knowledge.sector, knowledge.size_category].filter(Boolean).join(' · ') || null : 'tidak ada profil'}
      />
      <ChainStep
        label="Evidence" note={note('evidence')} here={originId === 'evidence'}
        value={sources.length ? `${sources.length} sumber` : evidence ? 'ada' : '—'}
        tone={sources.length ? null : 'faint'}
        sub={confidence?.evidence_age_days != null ? `umur ${confidence.evidence_age_days} hari` : meta?.evidence_date?.slice(0, 10)}
      />
      <ChainStep
        label="Knowledge" note={note('knowledge')} here={originId === 'knowledge'}
        value={meta ? `${meta.fields_completed}/${meta.fields_expected}` : '—'}
        tone={meta ? (gaps.length ? 'warn' : 'good') : 'faint'}
        sub={meta ? (gaps.length ? `${gaps.length} field hilang` : '0 field hilang') : null}
      />
      <ChainStep
        label="Confidence" note={note('confidence')} here={originId === 'confidence'}
        value={confidence?.overall ? fmtNum(confidence.overall.score, 1) : '—'}
        tone={confidence?.overall ? bandTone(confidence.overall.band) : 'faint'}
        sub={confidence?.overall?.band || null}
      />
      <ChainStep
        label="Risk" note={note('risk')} here={originId === 'risk'} wide={halted ? 150 : undefined}
        value={halted ? 'HALTED' : risk ? (triggered.length ? `${triggered.length} menyala` : 'bersih') : '—'}
        tone={halted ? 'bad' : triggered.length ? 'warn' : risk ? 'good' : 'faint'}
        sub={
          halted
            ? (aggregator?.halt_reason || risk?.halt_reason || 'flag ekstrem terpicu')
            : risk
              ? `${flags.length} spec flag${legacyFlags ? ` · ${legacyFlags} lama` : ''}`
              : null
        }
      />
      <ChainStep
        label="Reasoning" note={note('reasoning')} here={originId === 'reasoning'} wide={168}
        value={scores.length ? scores.map((s) => fmtNum(s, 0)).join(' · ') : halted ? 'dilewati' : '—'}
        tone={scores.length ? null : 'faint'}
        sub={scores.length ? (chainDeltas(data) || `${scores.length} lensa, tidak dirata-rata`) : halted ? 'hard-gate Risk' : null}
      />
      <ChainStep
        label="Sintesis" note={note('synthesis')} here={originId === 'synthesis'} wide={150}
        value={syn ? `${(syn.agreements || []).length} searah` : halted ? 'dilewati' : '—'}
        tone={syn ? null : 'faint'}
        sub={
          syn
            ? `${(syn.divergences || []).length} divergen${syn.surprise != null ? ` · surprise ${fmtNum(syn.surprise, 2)}` : ''}`
            : null
        }
      />
      {nextCat && (
        <ChainStep
          label="Katalis" note={note('catalyst')} here={originId === 'catalyst'} wide={132}
          value={nextCat.kind || 'katalis'}
          sub={[nextCat.expected_at, daysUntilLabel(nextCat.expected_at)].filter(Boolean).join(' · ')}
        />
      )}
      {peer && (
        <ChainStep
          label="Peer" note={note('peer')} here={originId === 'peer'}
          value={`${countComparisons(peer)} metrik`}
          tone={peer.low_sample_size ? 'warn' : null}
          sub={peer.low_sample_size ? 'sampel tipis' : null}
        />
      )}
      {calls.length > 0 && (
        <ChainStep
          label="Aksi pribadi" note={note('personal')} here={originId === 'personal'} wide={168}
          value={`${calls.length} call`} tone="acc"
          sub={calls.map((c) => prettyAction(c.action)).join(' · ')}
        />
      )}
    </div>
  )
}

// Δ skor tiap lensa terhadap snapshot PUBLIK sebelumnya. Sumbernya
// historical.entries yang memang sudah menyimpan thesis_score per modul --
// jadi "berubah apa di run ini" bisa dijawab tanpa data tambahan. Null kalau
// riwayatnya belum punya dua titik, dan pemanggil jatuh ke teks lamanya.
function chainDeltas(data) {
  const entries = data.historical?.entries || []
  if (entries.length < 2) return null
  const prev = readHistoricalEntry(entries[entries.length - 2])
  if (!prev) return null
  const byModule = Object.fromEntries((prev.lenses || []).map((l) => [l.module, l.thesis_score]))
  const parts = MODULES.map((m) => {
    const now = data.reasoning?.[m]?.thesis_score
    const before = byModule[m]
    if (now == null || before == null) return '—'
    // Dibulatkan DULU, baru diputuskan arahnya: selisih −0,04 dicetak "0,0",
    // dan "▼0,0" membaca seperti turun padahal angkanya sendiri bilang tidak.
    const rounded = +(now - before).toFixed(1)
    if (rounded === 0) return '0'
    return `${rounded > 0 ? '▲' : '▼'}${Math.abs(rounded).toFixed(1)}`
  })
  return parts.every((p) => p === '—') ? null : `Δ ${parts.join(' · ')}`
}

function bandTone(band) {
  if (band === 'high') return 'good'
  if (band === 'low') return 'bad'
  return 'warn'
}

function countComparisons(peer) {
  return Object.entries(peer || {}).filter(([k, v]) => k.endsWith('_comparison') && v).length
}

function daysUntilLabel(dateStr) {
  if (!dateStr) return null
  const then = Date.parse(dateStr)
  if (Number.isNaN(then)) return null
  const days = Math.round((then - Date.now()) / 86400000)
  if (days < 0) return 'lewat'
  return `${days} hari`
}

// Satu section terlipat. `body` sengaja THUNK, bukan elemen: yang belum dibuka
// tidak pernah dirender sama sekali. Yang paling mahal justru yang paling
// jarang dibuka (RawDataTable + tabel kuartalan Evidence), jadi kalau body-nya
// elemen biasa, "terlipat" cuma menyembunyikan biaya yang tetap dibayar.
function SectionRow({ id, label, summary, pill, asal, hasData, isOpen, onToggle, body, note }) {
  if (!hasData) {
    return (
      <div className="msec kosong" id={`sec-${id}`}>
        <div className="msec-h">
          <span className="msec-caret">·</span>
          <span className="msec-t">{label}</span>
          <span className="msec-sum">tidak ada data</span>
        </div>
      </div>
    )
  }
  return (
    <div className={`msec${asal ? ' asal' : ''}`} id={`sec-${id}`}>
      <button className="msec-h" onClick={onToggle} aria-expanded={isOpen}>
        <span className="msec-caret">{isOpen ? '▾' : '▸'}</span>
        <span className="msec-t">{label}</span>
        {asal && <span className="msec-badge">asal</span>}
        {pill}
        <span className="msec-sum">{summary}</span>
      </button>
      {note && <div className="msec-help">{note}</div>}
      {isOpen && <div className="msec-body">{body()}</div>}
    </div>
  )
}

function ModalBody({ data, context, aiNarrative, personalData, onNeedNarrative, notesOn }) {
  const { aggregator, reasoning, risk, confidence, catalyst, peer, knowledge, evidence, historical } = data
  const originId = CONTEXT_ORIGIN[context] || 'evidence'
  const [open, setOpen] = useState(() => new Set([originId]))

  // Ganti ticker/halaman asal = kembali ke satu section terbuka. Tanpa ini
  // section yang dibuka di ticker sebelumnya ikut terbawa, dan modal berikutnya
  // membuka 5 section sekaligus tanpa diminta.
  useEffect(() => { setOpen(new Set([originId])) }, [originId, data.ticker])

  const anySection = aggregator || reasoning || risk || confidence || catalyst || knowledge || evidence || historical
  if (!anySection && !PERSONAL_CONTEXTS.has(context)) {
    return <div className="empty">Tidak ada detail untuk ticker ini.</div>
  }

  const synthesis = aggregator?.synthesis
  const halted = !!aggregator?.halted
  const meta = knowledge?.metadata

  function toggle(id) {
    const wasOpen = open.has(id)
    // Efek samping DI LUAR updater: React boleh memanggil updater dua kali
    // (StrictMode), dan panggilan AI tidak boleh ikut dobel.
    if (!wasOpen && NARRATIVE_CONTEXTS.has(id)) onNeedNarrative()
    const next = new Set(open)
    if (wasOpen) next.delete(id)
    else next.add(id)
    setOpen(next)
  }

  const nextCat = (catalyst?.catalysts || [])[0]
  const triggered = (risk?.flags || []).filter((f) => f.status === 'triggered')
  const stances = MODULES.map((m) => reasoning?.[m]?.stance).filter(Boolean)
  const price = evidence?.price_market?.last_price ?? evidence?.price_market?.close

  const defs = [
    {
      id: 'personal',
      label: 'Lapisan Pribadi',
      hasData: PERSONAL_CONTEXTS.has(context),
      summary: personalData?.call_set ? `${MODULES.filter((m) => personalData.call_set[m]).length} call` : 'memuat…',
      body: () => (
        <PersonalDetailSection personalData={personalData} showHistory={context === 'personal_historical'} />
      ),
    },
    {
      id: 'knowledge',
      label: 'Knowledge',
      hasData: !!knowledge,
      summary: meta ? `${meta.fields_completed}/${meta.fields_expected} field` : null,
      body: () => (
        <>
          <AiNarrativeBlock aiNarrative={aiNarrative} />
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--dim)', letterSpacing: '.04em', textTransform: 'uppercase', margin: '18px 0 10px' }}>
            Data Pendukung Lengkap
          </div>
          <KnowledgeDetailCards knowledge={knowledge} evidence={evidence} />
        </>
      ),
    },
    {
      id: 'risk',
      label: 'Risk / Red Flags',
      hasData: !!risk && ((risk.flags || []).length > 0 || (risk.red_flags || []).length > 0),
      pill: halted
        ? <span className="pill bad">halted</span>
        : triggered.length > 0
          ? <span className="pill warn">{triggered.length} menyala</span>
          : null,
      summary: risk
        ? [
            triggered.length ? triggered.map((f) => f.flag_id).join(', ') : 'tidak ada yang menyala',
            risk.risk_score != null ? `skor ${fmtNum(risk.risk_score, 0)}` : null,
          ].filter(Boolean).join(' · ')
        : null,
      body: () => (
        <>
          {halted && (
            <div className="flag">
              <strong>HALTED</strong> — {aggregator.halt_reason || 'red flag severity ekstrem terpicu'}.
              Saham ini tidak diteruskan ke modul reasoning (hard-gate keselamatan).
            </div>
          )}
          <RiskFlagsBySeverity flags={risk.flags} assessedAt={risk.assessed_at} />
          {(risk.red_flags || []).map((f, i) => (
            <div className={`flag${f.severity === 'medium' ? ' medium' : ''}`} key={`rf${i}`}>
              <strong>{f.flag_type}</strong> ({f.severity}) — {f.description}
            </div>
          ))}
        </>
      ),
    },
    {
      id: 'reasoning',
      label: 'Reasoning — 3 Lensa',
      hasData: !!reasoning && !halted,
      summary: stances.length ? stances.map(prettyStance).join(' · ') : null,
      body: () => (
        <>
          <BullBearCase reasoning={reasoning} />
          <LensGrid reasoning={reasoning} historical={historical} />
        </>
      ),
    },
    {
      id: 'synthesis',
      label: 'Sintesis',
      hasData: !!synthesis,
      pill: synthesis && (
        <span className={`pill ${synthesis.full_convergence ? 'ok' : 'neutral'}`}>
          {synthesis.full_convergence ? 'konvergen' : 'divergen'}
        </span>
      ),
      summary: synthesis
        ? [
            `${(synthesis.agreements || []).length} searah`,
            `${(synthesis.divergences || []).length} divergen`,
            synthesis.surprise != null ? `surprise ${fmtNum(synthesis.surprise, 2)}` : null,
          ].filter(Boolean).join(' · ')
        : null,
      body: () => (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
            {synthesis.confidence && (
              <span className={`pill ${bandClass(synthesis.confidence.band)}`}>
                confidence {synthesis.confidence.score.toFixed(0)} · {synthesis.confidence.band}
              </span>
            )}
            {synthesis.surprise != null && (
              <span className="pill neutral">surprise {synthesis.surprise.toFixed(2)}</span>
            )}
          </div>
          {synthesis.narrative && <p className="narrative" style={{ marginBottom: 12 }}>{synthesis.narrative}</p>}
          <SynthesisSection synthesis={synthesis} />
        </>
      ),
    },
    {
      id: 'confidence',
      label: 'Confidence Report',
      hasData: !!confidence,
      pill: confidence?.overall && <span className={`pill ${bandClass(confidence.overall.band)}`}>{confidence.overall.band}</span>,
      summary: confidence?.overall
        ? `${fmtNum(confidence.overall.score, 1)} · ${Object.keys(confidence.by_section || {}).length} section`
        : null,
      body: () => <ConfidenceDetail confidence={confidence} />,
    },
    {
      id: 'catalyst',
      label: 'Katalis',
      hasData: !!catalyst,
      summary: nextCat
        ? [nextCat.kind, nextCat.expected_at, daysUntilLabel(nextCat.expected_at)].filter(Boolean).join(' · ')
        : 'tidak ada katalis mendatang',
      body: () => (
        <>
          <CatalystCountdownCard catalyst={catalyst} aiNarrative={aiNarrative} />
          <CatalystHistoryBar history={catalyst.resolved_history} />
        </>
      ),
    },
    {
      id: 'peer',
      label: 'Peer Comparison',
      hasData: !!peer,
      pill: peer?.low_sample_size ? <span className="pill warn">sampel tipis</span> : null,
      summary: peer ? `${countComparisons(peer)} metrik dibanding` : null,
      body: () => <PeerComparisonCard peer={peer} aiNarrative={aiNarrative} />,
    },
    {
      id: 'evidence',
      label: 'Evidence — Sumber Data',
      hasData: !!evidence,
      summary: [
        price != null ? fmtMoney(price) : null,
        (meta?.sources_used || []).length ? `${meta.sources_used.length} sumber` : null,
      ].filter(Boolean).join(' · '),
      body: () => (
        <>
          <CompanyHeaderSection profile={evidence.company_profile} fundamental={evidence.fundamental} />

          {/* Bar harga dari cache pipeline. Dirender di sini, di dalam body
              section yang malas -- jadi permintaan /ohlc baru terjadi kalau
              bagian Evidence benar-benar dibuka. */}
          <CandleChart ticker={data.ticker} catalystDate={nextCat?.expected_at} />

          <PriceSnapshotBlock priceMarket={evidence.price_market} />

          {evidence.analyst_estimates && (
            <AnalystEstimatesBlock
              estimates={evidence.analyst_estimates}
              currentPrice={price}
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

          {evidence.institutional_activity && <InsiderActivitySection activity={evidence.institutional_activity} />}
          {evidence.institutional_ownership && <InstitutionalHoldersSection ownership={evidence.institutional_ownership} />}

          <RawDataTable evidence={evidence} />
        </>
      ),
    },
    {
      id: 'historical',
      label: 'Riwayat',
      hasData: !!historical && (historical.entries || []).length > 0,
      summary: historical
        ? [
            `${historical.total_entries || historical.entries.length} snapshot`,
            historical.first_entry_date && historical.last_entry_date
              ? `${historical.first_entry_date.slice(0, 10)} → ${historical.last_entry_date.slice(0, 10)}`
              : null,
          ].filter(Boolean).join(' · ')
        : null,
      body: () => (
        <>
          {historical.entries.slice().reverse().map((entry, i) => (
            <HistoricalEntryRow key={i} entry={entry} />
          ))}
        </>
      ),
    },
  ]

  // Section asal naik ke atas; sisanya tetap urutan pipeline. Lapisan pribadi
  // cuma ada kalau endpointnya memang dipanggil (konteks pribadi).
  const visible = defs.filter((d) => d.id !== 'personal' || PERSONAL_CONTEXTS.has(context))
  const ordered = [
    ...visible.filter((d) => d.id === originId),
    ...visible.filter((d) => d.id !== originId),
  ]

  return (
    <>
      <IdentityLine evidence={evidence} knowledge={knowledge} />
      <ChainTrace data={data} personalData={personalData} originId={originId} notesOn={notesOn} />
      {ordered.map((d) => (
        <SectionRow
          key={d.id}
          {...d}
          asal={d.id === originId}
          isOpen={open.has(d.id)}
          onToggle={() => toggle(d.id)}
          note={notesOn ? readingNote('section', d.id) : null}
        />
      ))}
    </>
  )
}

// Nama perusahaan dulu cuma muncul di konteks Evidence (CompanyHeaderSection
// ada di dalam grup itu). Sekarang grup Evidence bisa terlipat, jadi identitas
// dasarnya diangkat keluar — satu baris, selalu terlihat.
function IdentityLine({ evidence, knowledge }) {
  const name = evidence?.company_profile?.long_name
  const sector = evidence?.fundamental?.sector || knowledge?.sector
  const bits = [name, sector, knowledge?.exchange || evidence?.exchange].filter(Boolean)
  if (!bits.length) return null
  return <div className="mident">{bits.join(' · ')}</div>
}

// Satu baris riwayat publik. Entry TERAKHIR menyimpan snapshot penuh (ada
// narasi sintesisnya), entry lama cuma bentuk tipis — lihat
// montrva/layer2/historical.py. Tiga stance ditampilkan untuk KEDUANYA:
// data itu selama ini tersimpan penuh tapi tidak pernah ditampilkan, dan di
// entry tipis justru itulah isi utamanya.
function HistoricalEntryRow({ entry }) {
  const e = readHistoricalEntry(entry)
  if (!e) return null

  return (
    <div className="lens-box">
      <div className="lens-head">
        <span>{e.analyzedAt?.slice(0, 10) || '—'}</span>
        <span>
          {e.halted ? (
            <span className="pill bad">halted</span>
          ) : e.fullConvergence ? (
            <span className="pill ok">konvergen</span>
          ) : (
            <span className="pill neutral">divergen</span>
          )}
        </span>
      </div>
      {e.lenses.length > 0 && (
        <div className="factor" style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {e.lenses.map((l) => (
            <span key={l.module} className={`pill ${stanceClass(l.stance)}`} title={MODULE_LABELS[l.module] || l.module}>
              {(MODULE_LABELS[l.module] || l.module)}: {prettyStance(l.stance)}
              {l.thesis_score != null && ` ${fmtNum(l.thesis_score, 1)}`}
            </span>
          ))}
        </div>
      )}
      {e.narrative && (
        <div className="factor" style={{ color: 'var(--dim)' }}>{e.narrative}</div>
      )}
      <div className="factor" style={{ color: 'var(--faint)', fontSize: 11 }}>
        {e.outcome != null ? 'outcome tercatat' : 'outcome: menunggu evaluasi v2.1'}
        {e.thin && ' · snapshot ringkas (arsip)'}
      </div>
    </div>
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
  const trendLabel = trendSpanLabel(priceMarket.price_history)

  return (
    <div className="mrow" style={{ marginBottom: 4 }}>
      <div className="mcell">
        <div className="mcell-label">Close</div>
        <div className="mcell-val">${fmtNum(close, 2)}</div>
      </div>
      <div className="mcell">
        {/* Tanggalnya ikut ditulis karena OHLC berasal dari sesi LENGKAP
            terakhir, yang bisa lebih tua dari `last_price` di kartu Close
            sebelah kiri (Yahoo kadang baru memberi baris volume-saja untuk
            sesi terbaru). Tanpa tanggal, dua hari berbeda tampak satu. */}
        <div className="mcell-label">
          Open / High / Low{priceMarket.ohlc_date ? ` · ${priceMarket.ohlc_date}` : ''}
        </div>
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
          <div className="mcell-label">{trendLabel}</div>
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
  ['debt_to_equity', 'Debt/Equity (%)', (v) => fmtNum(v, 2)],
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

// nextFilingDeadline + NEW_POSITION_SENTINEL pindah ke format.js (dipakai
// juga oleh halaman Aliran Dana Institusi) — lihat import di atas file ini.

// dateReported ikut dipakai untuk pemegang BARU saja: 13F tidak punya tanggal
// transaksi, tapi "belum ada di laporan sebelumnya, ada di laporan ini" berarti
// masuknya di suatu titik SEPANJANG kuartal itu — jendela, bukan tanggal.
function holderSignal(pctChange, dateReported) {
  if (pctChange === null || pctChange === undefined) return { label: '—', tone: 'neutral' }
  if (pctChange >= NEW_POSITION_SENTINEL) {
    const window = dateReported ? quarterWindow(dateReported) : null
    return { label: window ? `🆕 Baru · ${window}` : '🆕 Baru Masuk', tone: 'good' }
  }
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
                const signal = holderSignal(h.pct_change, h.date_reported)
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

