import { useEffect, useId, useState } from 'react'
import { api } from '../api'
import { horizonProgress, horizonTargetPrice } from '../format'

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

// Badge risiko -- lapisan pribadi sebelumnya tidak pernah membaca Risk sama
// sekali, cuma menitipkan pesan generik "cek Risk Flags sendiri" di teks.
// Ringkasannya (dihitung sekali di personal_reasoning.py, TIDAK menilai
// ulang) tampil langsung di kartu -- merah kalau ada flag high-severity,
// kuning kalau cuma medium. Dipakai bareng di Agregator (PickCard), Riwayat
// (ExpandedTimeline), dan Ticker Modal (PersonalCallCard) -- SATU komponen,
// bukan disalin 3x, supaya kalau ada satu ticker yang bawa flag tinggi DAN
// sedang sekaligus (audit 2026-07-29: 1939 ticker begitu), keduanya tampil,
// bukan cuma yang tinggi (versi lama diam-diam membuang count medium-nya).
export function RiskBadge({ call }) {
  const high = call.risk_flags_high || 0
  const medium = call.risk_flags_medium || 0
  if (high === 0 && medium === 0) return null
  const tone = high > 0 ? 'var(--bad)' : 'var(--warn)'
  const bg = high > 0 ? 'rgba(251,113,133,.12)' : 'rgba(251,191,122,.12)'
  const parts = []
  if (high > 0) parts.push(`${high} tinggi`)
  if (medium > 0) parts.push(`${medium} sedang`)
  const label = `${parts.join(' + ')} risk flag`
  const allTypes = call.risk_flag_types || []
  const shown = allTypes.slice(0, 3).join(', ')
  const extra = allTypes.length > 3 ? ` +${allTypes.length - 3} lagi` : ''
  return (
    <div
      title={allTypes.join(', ')}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 10, fontWeight: 600, color: tone, background: bg, padding: '2px 7px', borderRadius: 5, marginBottom: 6 }}
    >
      ⚠ {label}{shown && <span style={{ fontWeight: 400, opacity: .85 }}> — {shown}{extra}</span>}
    </div>
  )
}

const fmtPrice = (v) => v == null ? '—' : `$${v.toFixed(2)}`

// Sama persis dengan _score_tier di personal_calibration.py DAN gerbang
// _thesis_score_tier di personal_reasoning.py. Kalau salah satu berubah,
// ketiganya harus ikut -- sengaja disebut di sini karena tidak ada yang
// menghubungkan mereka secara struktural.
function scoreTierKey(score) {
  if (score == null) return 'skor tidak tersimpan'
  if (score >= 70) return 'high (skor >=70)'
  if (score >= 50) return 'medium (skor 50-69)'
  return 'low (skor <50)'
}

// Base rate tesis sejenis, ditempel di kartu tesis yang MASIH HIDUP.
//
// Ini penanda, BUKAN gerbang: tidak ada action yang berubah karenanya. Alasan
// tegasnya ada di personal_calibration.py — 167 tesis yang lahir cuma di 2
// tanggal masuk tidak boleh menyetel threshold atau gerbang skor. Yang bisa
// dilakukan dengan jujur adalah menaruh angkanya di tempat keputusan dibaca,
// lalu membiarkan orangnya yang menimbang.
//
// Untuk Multibagger & Quality/Compound, isi yang benar justru KEKOSONGANNYA:
// nol tesis pernah jatuh tempo dan yang pertama baru mungkin 2027/2028.
// Tanpa kalimat itu, kartu 5 tahunan terlihat sama meyakinkannya dengan
// kartu spekulatif yang sudah punya 167 vonis.
export function BaseRateNote({ calibration, module, thesisScore }) {
  const base = calibration?.base_rates?.[module]
  if (!base) return null

  const wrap = (children, dim) => (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed var(--rule)', fontSize: 9.5, color: dim ? 'var(--faint)' : 'var(--dim)', lineHeight: 1.55 }}>
      {children}
    </div>
  )

  if (!base.n) {
    return wrap(
      <>
        Belum ada tesis lensa ini yang pernah jatuh tempo
        {base.earliest_possible_verdict && <> — vonis paling cepat <strong>{base.earliest_possible_verdict}</strong></>}.
      </>,
      true,
    )
  }

  // Pakai irisan paling spesifik yang benar-benar punya isi, lalu jatuh ke
  // level modul. Tidak pernah mengarang bucket kosong jadi "0%".
  const tierKey = scoreTierKey(thesisScore)
  const tier = base.by_score_tier?.[tierKey]
  const use = tier && tier.n ? tier : base
  const scoped = use === tier

  return wrap(
    <>
      Tesis sejenis{scoped ? ` (${tierKey})` : ''}:{' '}
      <strong style={{ color: 'var(--good)' }}>{use.terbukti} terbukti</strong> ·{' '}
      <strong style={{ color: 'var(--bad)' }}>{use.meleset} meleset</strong>
      {use.hit_rate_pct != null && (
        <span style={{ opacity: use.evaluable ? 1 : 0.5 }}> — {use.hit_rate_pct.toFixed(0)}%</span>
      )}
      <span style={{ color: 'var(--faint)' }}> dari {use.n} tesis, {use.entry_dates} tanggal masuk</span>
      {!use.evaluable && (
        <span
          style={{ color: 'var(--warn)' }}
          title={`${use.limiters.join(' · ')} — angka ini belum layak dipakai menyetel apa pun, cuma penanda`}
        >
          {' '}· belum cukup bukti
        </span>
      )}
    </>,
    false,
  )
}

// Hari kalender dari tanggal entry -- dipakai jadi koordinat-X chart (satu
// sumbu waktu tunggal dari entry sampai deadline, bukan indeks bar).
function _dayOffset(dateStr, entryMs) {
  return Math.round((new Date(`${dateStr}T00:00:00Z`).getTime() - entryMs) / 86400000)
}

// Geometri + state chart "entry -> deadline" -- dipisah dari komponennya
// biar gampang dibaca: fungsi ini murni hitung angka, komponennya cuma
// nge-render + urus hover.
function _buildChart(bars, liveClose, entryPrice, targetPrice, upperDays) {
  const entryMs = new Date(`${bars[0].date}T00:00:00Z`).getTime()
  const pathPts = bars.map((b) => [_dayOffset(b.date, entryMs), b.close])
  const lastKnownDay = pathPts[pathPts.length - 1][0]
  const todayDay = liveClose != null ? Math.max(lastKnownDay, Math.round((Date.now() - entryMs) / 86400000)) : lastKnownDay
  if (liveClose != null && todayDay !== lastKnownDay) pathPts.push([todayDay, liveClose])

  const currentPrice = liveClose ?? bars[bars.length - 1].close
  const overdue = todayDay > upperDays
  // Domain-X membentang sampai deadline ATAU sampai hari ini kalau sudah
  // lewat deadline (belum sempat dievaluasi run berikutnya) -- supaya bar
  // yang sungguhan ada tidak pernah terpotong di luar viewBox.
  const domainDays = Math.max(upperDays, todayDay, 1)

  const W = 300, H = 68, padT = 6, padB = 4
  const allPrices = [...bars.map((b) => b.close), currentPrice, entryPrice, targetPrice]
  const minP = Math.min(...allPrices) * 0.98
  const maxP = Math.max(...allPrices) * 1.02
  const spanP = maxP - minP || 1
  const xFor = (day) => (day / domainDays) * W
  const yFor = (p) => padT + (1 - (p - minP) / spanP) * (H - padT - padB)

  const state = currentPrice >= targetPrice ? 'hit_target' : currentPrice >= entryPrice ? 'on_track' : 'at_risk'
  const color = state === 'hit_target' ? 'var(--good)' : state === 'on_track' ? 'var(--gold)' : 'var(--bad)'
  const colorHex = state === 'hit_target' ? '#4ADE80' : state === 'on_track' ? '#e8b84b' : '#FB7185'

  const linePoints = pathPts.map(([d, p]) => `${xFor(d).toFixed(1)},${yFor(p).toFixed(1)}`).join(' ')
  const areaPoints = `${xFor(pathPts[0][0]).toFixed(1)},${yFor(minP).toFixed(1)} ${linePoints} ${xFor(todayDay).toFixed(1)},${yFor(minP).toFixed(1)}`

  return {
    W, H, padT, padB, xFor, yFor, entryMs, pathPts, todayDay, domainDays, upperDays,
    overdue, linePoints, areaPoints, color, colorHex, state,
    entryY: yFor(entryPrice), targetY: yFor(targetPrice), todayX: xFor(todayDay),
  }
}

function ThesisChart({ bars, liveClose, entryPrice, targetPrice, upperDays }) {
  const [hover, setHover] = useState(null)
  const gradId = useId()
  const hatchId = useId()
  if (bars.length < 1) return null
  const c = _buildChart(bars, liveClose, entryPrice, targetPrice, upperDays)

  function onMove(e) {
    const rect = e.currentTarget.getBoundingClientRect()
    const relX = (e.clientX - rect.left) / rect.width
    const day = Math.round(relX * c.domainDays)
    if (day < c.pathPts[0][0] || day > c.todayDay) { setHover(null); return }
    let nearest = c.pathPts[0]
    let bestDiff = Infinity
    for (const pt of c.pathPts) {
      const diff = Math.abs(pt[0] - day)
      if (diff < bestDiff) { bestDiff = diff; nearest = pt }
    }
    setHover({ day: nearest[0], price: nearest[1], xPct: (c.xFor(nearest[0]) / c.W) * 100 })
  }

  return (
    <div style={{ position: 'relative', marginTop: 10 }}>
      <svg
        viewBox={`0 0 ${c.W} ${c.H}`} preserveAspectRatio="none" width="100%" height={c.H}
        style={{ display: 'block', overflow: 'visible', cursor: 'crosshair' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c.colorHex} stopOpacity="0.22" />
            <stop offset="100%" stopColor={c.colorHex} stopOpacity="0" />
          </linearGradient>
          <pattern id={hatchId} width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
            <rect width="6" height="6" fill="var(--panel3)" />
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--rule-strong)" strokeWidth="1.2" />
          </pattern>
        </defs>

        {[0.5].map((f) => (
          <line key={f} x1="0" y1={(c.padT + f * (c.H - c.padT - c.padB)).toFixed(1)} x2={c.W} y2={(c.padT + f * (c.H - c.padT - c.padB)).toFixed(1)} stroke="var(--rule)" strokeWidth="1" />
        ))}

        {!c.overdue ? (
          <>
            <rect x={c.todayX.toFixed(1)} y={c.padT} width={(c.W - c.todayX).toFixed(1)} height={c.H - c.padT - c.padB} fill={`url(#${hatchId})`} opacity="0.55" />
            <line x1={c.W.toFixed(1)} y1={c.padT} x2={c.W.toFixed(1)} y2={c.H - c.padB} stroke="var(--faint)" strokeWidth="1" strokeDasharray="2,3" />
          </>
        ) : (
          <>
            <rect x={c.xFor(c.upperDays).toFixed(1)} y={c.padT} width={(c.todayX - c.xFor(c.upperDays)).toFixed(1)} height={c.H - c.padT - c.padB} fill="var(--bad)" opacity="0.07" />
            <line x1={c.xFor(c.upperDays).toFixed(1)} y1={c.padT} x2={c.xFor(c.upperDays).toFixed(1)} y2={c.H - c.padB} stroke="var(--bad)" strokeWidth="1" strokeDasharray="2,3" opacity="0.6" />
          </>
        )}

        <line x1="0" y1={c.entryY.toFixed(1)} x2={c.W} y2={c.entryY.toFixed(1)} stroke="var(--faint)" strokeWidth="1" strokeDasharray="3,3" />
        <line x1="0" y1={c.targetY.toFixed(1)} x2={c.W} y2={c.targetY.toFixed(1)} stroke="var(--gold-hi)" strokeWidth="1" strokeDasharray="3,3" opacity="0.75" />

        <polygon points={c.areaPoints} fill={`url(#${gradId})`} />
        <polyline points={c.linePoints} fill="none" stroke={c.colorHex} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />

        <circle cx={c.todayX.toFixed(1)} cy={c.yFor(liveClose ?? bars[bars.length - 1].close).toFixed(1)} r="7" fill={c.colorHex} opacity="0.2">
          <animate attributeName="r" values="5;9;5" dur="2.2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.28;0.05;0.28" dur="2.2s" repeatCount="indefinite" />
        </circle>
        <circle cx={c.todayX.toFixed(1)} cy={c.yFor(liveClose ?? bars[bars.length - 1].close).toFixed(1)} r="3" fill={c.colorHex} stroke="var(--panel)" strokeWidth="1.3" />

        {hover && (
          <line x1={c.xFor(hover.day).toFixed(1)} y1={c.padT} x2={c.xFor(hover.day).toFixed(1)} y2={c.H - c.padB} stroke="var(--dim)" strokeWidth="1" opacity="0.5" />
        )}
      </svg>
      {hover && (
        <div style={{
          position: 'absolute', left: `${Math.min(Math.max(hover.xPct, 12), 82)}%`, top: -6,
          transform: 'translateX(-50%)', background: 'var(--ink)', border: '1px solid var(--rule-strong)',
          borderRadius: 6, padding: '4px 7px', fontSize: 9.5, whiteSpace: 'nowrap', pointerEvents: 'none',
          boxShadow: '0 8px 20px -6px rgba(0,0,0,.7)', zIndex: 2,
        }}>
          <div style={{ color: 'var(--faint)', fontSize: 8.5 }}>
            {new Date(c.entryMs + hover.day * 86400000).toISOString().slice(0, 10)}
          </div>
          <div style={{ fontFamily: 'var(--mono)', fontWeight: 700, color: c.color }}>{fmtPrice(hover.price)}</div>
        </div>
      )}
    </div>
  )
}

// "Bukti" (fitur diminta user): entry/live/target sebagai angka, grafik
// harga entry->deadline (bukan cuma entry->hari-ini) dengan zona setelah
// hari-ini digambar KOSONG/bertekstur -- bukan proyeksi tren -- supaya
// "sisa waktu" kelihatan literal tanpa menyaru sebagai ramalan sistem
// (konsisten dengan disclaimer §12 di bawah). Plus quote live buat titik
// terakhir dan progress bar "hari ke-berapa dari horizon".
export default function ThesisProof({ ticker, module, action, horizon, horizonAnchor, historyEntries: historyEntriesProp, calibration, thesisScore }) {
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
  // `since` adalah timestamp ISO penuh ("2026-07-15T10:30:00"), `b.date`
  // cuma "YYYY-MM-DD". Bandingkan sebagai string apa adanya menjatuhkan bar
  // hari anchor itu sendiri, karena "2026-07-15" < "2026-07-15T10:30:00"
  // secara leksikografis (prefix pendek < string yang lebih panjang).
  const sinceDate = since ? since.slice(0, 10) : null
  let bars = sinceDate ? priceHistory.filter((b) => b.date >= sinceDate) : priceHistory.slice(-5)
  if (bars.length === 0) bars = priceHistory.slice(-2)

  const liveClose = live && !live.stale && live.last_price != null ? live.last_price : null

  if (bars.length + (liveClose != null ? 1 : 0) < 2) {
    return <div style={{ fontSize: 10, color: 'var(--faint)', marginTop: 8 }}>Belum cukup data harga untuk grafik.</div>
  }

  const entryPrice = bars[0].close
  const currentPrice = liveClose ?? bars[bars.length - 1].close
  const pct = entryPrice ? ((currentPrice - entryPrice) / entryPrice) * 100 : null
  const target = horizonTargetPrice(entryPrice, action, horizon)
  const progress = horizon ? horizonProgress(since, horizon, horizonAnchor) : null
  const hitTarget = target != null && currentPrice >= target.targetPrice

  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--rule)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <span style={{ fontSize: 9.5, color: 'var(--faint)' }}>Sejak {sinceDate || 'mulai dilacak'}</span>
        {hitTarget ? (
          <span style={{ fontSize: 9.5, fontWeight: 700, color: 'var(--good)', textTransform: 'uppercase', letterSpacing: '.03em' }}>✓ Sudah capai target</span>
        ) : liveClose != null && (
          <span style={{ fontSize: 9.5, color: 'var(--accent)' }} title="Quote live, bukan snapshot pipeline">● live</span>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: target ? '1fr 1fr 1fr' : '1fr 1fr', gap: 8 }}>
        <div style={{ borderLeft: '2px solid var(--rule)', paddingLeft: 8 }}>
          <div style={{ fontSize: 8.5, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.04em' }}>Entry</div>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 700 }}>{fmtPrice(entryPrice)}</div>
        </div>
        <div style={{ borderLeft: `2px solid ${hitTarget ? 'var(--good)' : 'var(--gold)'}`, paddingLeft: 8 }}>
          <div style={{ fontSize: 8.5, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.04em' }}>{liveClose != null ? 'Sekarang' : 'Terakhir'}</div>
          <div style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 700, color: pct >= 0 ? 'var(--good)' : 'var(--bad)' }}>{fmtPrice(currentPrice)}</div>
          {pct != null && <div style={{ fontFamily: 'var(--mono)', fontSize: 9.5, color: pct >= 0 ? 'var(--good)' : 'var(--bad)' }}>{pct >= 0 ? '+' : ''}{pct.toFixed(1)}%</div>}
        </div>
        {target && (
          <div style={{ borderLeft: '2px solid var(--gold)', paddingLeft: 8 }}>
            <div style={{ fontSize: 8.5, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.04em' }}>Target</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 13, fontWeight: 700, color: 'var(--gold-hi)' }}>{fmtPrice(target.targetPrice)}</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 9.5, color: 'var(--faint)' }}>+{target.thresholdPct.toFixed(0)}%</div>
          </div>
        )}
      </div>

      <ThesisChart
        bars={bars} liveClose={liveClose} entryPrice={entryPrice}
        targetPrice={target ? target.targetPrice : currentPrice}
        upperDays={progress ? progress.upperDays : Math.max((bars.length - 1) * 2, 30)}
      />

      <div style={{ fontSize: 9, color: 'var(--faint)', marginTop: 6 }}>
        Pergerakan harga, bukan validasi tesis — dibaca sendiri, bukan vonis sistem (§12).
      </div>

      {progress && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8, paddingTop: 8, borderTop: '1px dashed var(--rule)' }}>
          <div style={{ flex: 1, height: 5, borderRadius: 3, background: 'var(--panel3)', overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 3, width: `${Math.min(progress.pct, 100)}%`,
              background: progress.status === 'over' ? 'var(--bad)' : progress.status === 'near' ? 'var(--warn)' : 'var(--gold)',
            }} />
          </div>
          <div style={{
            fontSize: 10, fontFamily: 'var(--mono)', fontWeight: 700, whiteSpace: 'nowrap',
            color: progress.status === 'over' ? 'var(--bad)' : progress.status === 'near' ? 'var(--warn)' : 'var(--gold)',
          }}>
            {progress.status === 'over'
              ? `Lewat ${progress.ageDays - progress.upperDays}h dari estimasi`
              : `Hari ${progress.ageDays}/${progress.upperDays} · sisa ${progress.upperDays - progress.ageDays}h`}
          </div>
        </div>
      )}

      <BaseRateNote calibration={calibration} module={module} thesisScore={thesisScore} />
    </div>
  )
}
