import { useEffect, useState } from 'react'
import { api } from '../api'
import { sparklinePoints, horizonProgress, horizonTargetPrice } from '../format'

// Sejak kapan ACTION ini (bukan cuma ticker-nya) muncul: jalan MUNDUR dari
// entry terbaru di personal_history, berhenti begitu action lensa ini beda
// dari sekarang. Dipakai bareng oleh PersonalAggregatorView (kartu top pick)
// dan PersonalHistoricalView (expand row) -- diekstrak ke sini supaya dua
// tempat itu tidak menduplikasi logika yang sama persis.
export function firstSeenAt(historyEntries, module, currentAction) {
  if (!historyEntries || historyEntries.length === 0) return null
  const sorted = [...historyEntries].sort((a, b) => (a.analyzed_at || '').localeCompare(b.analyzed_at || ''))
  let firstMatch = null
  for (let i = sorted.length - 1; i >= 0; i--) {
    const action = sorted[i]?.personal_call_set?.[module]?.action
    if (action !== currentAction) break
    firstMatch = sorted[i].analyzed_at
  }
  return firstMatch
}

function HorizonTrack({ since, horizon, action, startPrice }) {
  const progress = horizonProgress(since, horizon)
  if (!progress) return null
  const { ageDays, upperDays, pct, status } = progress
  const fillClass = status === 'over' ? 'over' : status === 'near' ? 'near' : 'ok'
  const valueColor = status === 'over' ? 'var(--bad)' : status === 'near' ? 'var(--warn)' : 'var(--gold)'
  const target = horizonTargetPrice(startPrice, action, horizon)
  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed var(--rule)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 5 }}>
        <span style={{ fontSize: 10, color: 'var(--faint)' }}>Progres ke estimasi</span>
        <span style={{ fontSize: 10.5, fontFamily: 'var(--mono)', fontWeight: 700, color: valueColor }}>
          {status === 'over' ? `Lewat ${ageDays - upperDays}h dari estimasi (${upperDays}h)` : `Hari ${ageDays} / ${upperDays}`}
        </span>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: 'var(--panel3)', overflow: 'hidden' }}>
        <div
          className={`track-fill-${fillClass}`}
          style={{
            height: '100%',
            borderRadius: 3,
            width: `${pct}%`,
            background: status === 'over' ? 'var(--bad)' : status === 'near' ? 'var(--warn)' : 'var(--gold)',
          }}
        />
      </div>
      {target && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 6 }}>
          <span style={{ fontSize: 10, color: 'var(--faint)' }}>Target buat "terbukti"</span>
          <span style={{ fontSize: 10.5, fontFamily: 'var(--mono)', fontWeight: 700, color: 'var(--gold)' }}>
            ${target.targetPrice.toFixed(2)} <span style={{ color: 'var(--faint)', fontWeight: 400 }}>(+{target.thresholdPct.toFixed(0)}%)</span>
          </span>
        </div>
      )}
    </div>
  )
}

// "Bukti" (fitur diminta user): grafik harga SEJAK tesis/action ini pertama
// kali tercatat, plus quote live buat titik terakhir, plus progress bar
// "hari ke-berapa dari horizon" -- supaya "live dari tesis muncul sampai
// estimasi" kelihatan literal, bukan cuma klaim di teks.
export default function ThesisProof({ ticker, module, action, horizon, historyEntries: historyEntriesProp }) {
  const [priceHistory, setPriceHistory] = useState(null)
  const [historyEntries, setHistoryEntries] = useState(historyEntriesProp || null)
  const [live, setLive] = useState(null)

  useEffect(() => {
    let cancelled = false
    const needsHistory = !historyEntriesProp
    Promise.all([
      api.ticker(ticker),
      needsHistory ? api.personalTicker(ticker) : Promise.resolve(null),
      api.liveQuote(ticker),
    ])
      .then(([t, p, l]) => {
        if (cancelled) return
        setPriceHistory(t?.evidence?.price_market?.price_history || [])
        if (needsHistory) setHistoryEntries(p?.history?.entries || [])
        setLive(l)
      })
      .catch(() => {
        if (!cancelled) { setPriceHistory([]); if (needsHistory) setHistoryEntries([]); setLive(null) }
      })
    return () => { cancelled = true }
  }, [ticker])

  if (priceHistory === null) return <div style={{ fontSize: 10, color: 'var(--faint)', marginTop: 8 }}>Memuat bukti harga…</div>

  const since = firstSeenAt(historyEntries, module, action)
  let bars = since ? priceHistory.filter((b) => b.date >= since) : priceHistory.slice(-5)
  if (bars.length === 0) bars = priceHistory.slice(-2)

  // Tambahkan quote live sebagai titik "sekarang" -- lebih segar dari
  // last_price harian pipeline, ini yang bikin grafiknya kerasa live, bukan
  // cuma snapshot kemarin.
  const liveClose = live && !live.stale && live.last_price != null ? live.last_price : null
  const chartBars = liveClose != null ? [...bars, { close: liveClose }] : bars

  if (chartBars.length < 2) {
    return <div style={{ fontSize: 10, color: 'var(--faint)', marginTop: 8 }}>Belum cukup data harga untuk grafik.</div>
  }

  const startClose = bars[0].close
  const endClose = liveClose ?? bars[bars.length - 1].close
  const pct = startClose ? ((endClose - startClose) / startClose) * 100 : null
  const color = pct == null ? 'var(--faint)' : pct >= 0 ? 'var(--good)' : 'var(--bad)'
  const points = sparklinePoints(chartBars, 240, 36)

  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--rule)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <span style={{ fontSize: 9.5, color: 'var(--faint)' }}>
          Sejak {since ? since.slice(0, 10) : 'mulai dilacak'} {liveClose != null && <span title="Quote live, bukan snapshot pipeline">· live</span>}
        </span>
        {pct != null && (
          <span style={{ fontSize: 11, fontWeight: 600, color }}>{pct >= 0 ? '+' : ''}{pct.toFixed(1)}%</span>
        )}
      </div>
      {points && (
        <svg viewBox="0 0 240 36" width="100%" height="32" preserveAspectRatio="none">
          <polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      <div style={{ fontSize: 9, color: 'var(--faint)', marginTop: 3 }}>
        Pergerakan harga, bukan validasi tesis — dibaca sendiri, bukan vonis sistem (§12).
      </div>
      {horizon && <HorizonTrack since={since} horizon={horizon} action={action} startPrice={startClose} />}
    </div>
  )
}
