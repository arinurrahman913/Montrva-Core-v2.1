import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStageData } from '../useStageData'
import ThesisProof from '../components/ThesisProof'
import { prettyAction, horizonLabel, prettyHorizon, BEST_ACTION } from '../format'

const LENSES = ['multibagger', 'quality_compound', 'speculative']

const LENS_TITLES = {
  multibagger: 'Multibagger — Mulai Posisi',
  quality_compound: 'Quality — Akumulasi',
  speculative: 'Speculative — Masuk Spekulatif',
}

function topPicks(callSets, module, n = 3) {
  return callSets
    .map((cs) => ({ ticker: cs.ticker, call: cs[module] }))
    .filter(({ call }) => call && call.position_status === 'no_holding' && call.action === BEST_ACTION[module])
    .sort((a, b) => (b.call.source_confidence ?? 0) - (a.call.source_confidence ?? 0))
    .slice(0, n)
}

// Top pick "kemarin" = generate SEBELUM yang sekarang -- entry kedua dari
// belakang di personal_history per ticker (bukan snapshot baru, murni baca
// data yang sudah ada). Dipakai buat tandai apa yang BARU muncul/keluar,
// bukan bikin fitur notifikasi/push baru.
function previousTopPickTickers(historyData, module) {
  const set = new Set()
  let latestPrevDate = null
  for (const timeline of Object.values(historyData || {})) {
    const entries = timeline.entries || []
    if (entries.length < 2) continue
    const prev = entries[entries.length - 2]
    const call = prev?.personal_call_set?.[module]
    if (call && call.position_status === 'no_holding' && call.action === BEST_ACTION[module]) {
      set.add(timeline.ticker)
    }
    if (!latestPrevDate || (prev?.analyzed_at || '') > latestPrevDate) latestPrevDate = prev?.analyzed_at
  }
  return { tickers: set, asOf: latestPrevDate }
}

// Kenapa sebuah ticker keluar dari top pick -- dibedakan "sekarang holding"
// (kamu sudah masuk posisi, wajar hilang dari "ide baru") vs "action
// berubah" (tesisnya sendiri melemah) -- dua cerita yang beda, jangan
// disamakan jadi "keluar" generik.
function dropReason(callSets, ticker, module) {
  const cs = callSets.find((c) => c.ticker === ticker)
  const call = cs?.[module]
  if (!call) return 'tidak lagi ada di data run ini'
  if (call.position_status === 'holding') return 'sekarang berstatus holding'
  return `action berubah jadi ${prettyAction(call.action)}`
}

function computeTopPickDiff(callSets, historyData) {
  const byLens = {}
  for (const module of LENSES) {
    const current = topPicks(callSets, module, 99) // semua yang qualify, bukan cuma top 3, buat diff akurat
    const currentTickers = new Set(current.map((p) => p.ticker))
    const { tickers: prevTickers, asOf } = previousTopPickTickers(historyData, module)
    const added = current.filter((p) => !prevTickers.has(p.ticker))
    const removed = [...prevTickers].filter((t) => !currentTickers.has(t)).map((ticker) => ({
      ticker, reason: dropReason(callSets, ticker, module),
    }))
    byLens[module] = { added, removed, asOf }
  }
  return byLens
}

// Harga sekarang + sektor tampil sebelah ticker -- diambil sekali di sini
// (bukan dari dalam ThesisProof, yang fokus ke harga SEJAK tesis muncul,
// bukan harga sekarang) supaya kartu langsung kasih konteks "ini saham apa,
// harganya berapa sekarang" sebelum baca detail live di bawahnya.
function useTickerMeta(ticker) {
  const [meta, setMeta] = useState(null)
  useEffect(() => {
    let cancelled = false
    Promise.all([api.ticker(ticker), api.liveQuote(ticker)])
      .then(([t, l]) => {
        if (cancelled) return
        const sector = t?.evidence?.fundamental?.sector ?? null
        const livePrice = l && !l.stale && l.last_price != null ? l.last_price : null
        const fallbackPrice = t?.evidence?.price_market?.last_price ?? null
        setMeta({ sector, price: livePrice ?? fallbackPrice })
      })
      .catch(() => { if (!cancelled) setMeta({ sector: null, price: null }) })
    return () => { cancelled = true }
  }, [ticker])
  return meta
}

function PickCard({ ticker, module, call, isNew, onSelectTicker }) {
  const meta = useTickerMeta(ticker)
  return (
    <div
      onClick={() => onSelectTicker(ticker)}
      style={{ position: 'relative', background: 'var(--panel2)', border: `1px solid ${isNew ? 'var(--accent)' : 'var(--rule)'}`, borderRadius: 10, padding: 12, marginBottom: 8, cursor: 'pointer' }}
    >
      {isNew && (
        <span style={{ position: 'absolute', top: -8, right: 10, background: 'var(--accent)', color: 'var(--ink)', fontSize: 9, fontWeight: 700, padding: '2px 7px', borderRadius: 5, letterSpacing: '.03em' }}>
          BARU
        </span>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
        <span style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span className="ticker" style={{ fontSize: 13, fontWeight: 600 }}>{ticker}</span>
          <span style={{ fontSize: 11.5, fontFamily: 'var(--mono)', color: 'var(--dim)' }}>
            {meta?.price != null ? `$${meta.price.toFixed(2)}` : '—'}
          </span>
        </span>
        <span style={{ fontSize: 10.5, color: 'var(--good)' }}>conf {call.source_confidence?.toFixed(0) ?? '—'}</span>
      </div>
      {meta?.sector && (
        <div style={{ fontSize: 10, color: 'var(--faint)', marginBottom: 4 }}>{meta.sector}</div>
      )}
      <div style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 4 }}>
        {horizonLabel(call.action)}: <span style={{ color: 'var(--dim)' }}>{prettyHorizon(call.horizon)}</span>
      </div>
      {call.horizon_basis && (
        <div style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.4 }}>{call.horizon_basis}</div>
      )}
      <ThesisProof ticker={ticker} module={module} action={call.action} horizon={call.horizon} />
    </div>
  )
}

function TopPicksSection({ callSets, onSelectTicker }) {
  const picksByLens = LENSES.map((m) => ({ module: m, picks: topPicks(callSets, m) }))
  if (picksByLens.every((p) => p.picks.length === 0)) return null

  return (
    <div style={{ marginBottom: 22 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>
        Top Pick Pribadi — Action Terkuat per Lensa
      </div>
      <p style={{ fontSize: 11, color: 'var(--faint)', margin: '2px 0 14px', lineHeight: 1.5 }}>
        Filter dari <code>action</code> + confidence yang sudah dihitung tiap lensa, cuma yang belum dipegang — bukan
        ranking gabungan (D-04 tetap berlaku di lapisan pribadi). Tetap cek Risk Flags &amp; horizon_basis sebelum bertindak.
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
        {picksByLens.map(({ module, picks }) => (
          <div key={module}>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
              {LENS_TITLES[module]}
            </div>
            {picks.length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--faint)' }}>Tidak ada ticker dengan action ini saat ini.</div>
            ) : (
              picks.map(({ ticker, call }) => (
                <PickCard key={ticker} ticker={ticker} module={module} call={call} onSelectTicker={onSelectTicker} />
              ))
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// Halaman ini SENGAJA cuma nampilin Top Pick (action entry terkuat per
// lensa) -- bukan tabel semua ticker x 3 lensa (kepenuhan, mayoritas isinya
// pantau/lewati yang bukan "ide baru"). Ticker yang lewat dari sini (jadi
// holding, atau action-nya berubah) TETAP kesimpen -- itu tugas Riwayat
// Pribadi, bukan dobel di sini.
export default function PersonalAggregatorView({ onSelectTicker }) {
  const { data, error } = useStageData(api.personalCalls)

  if (error) return <div className="empty">Gagal memuat data/personal/personal_calls.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const callSets = data.call_sets || []

  return <TopPicksSection callSets={callSets} onSelectTicker={onSelectTicker} />
}
