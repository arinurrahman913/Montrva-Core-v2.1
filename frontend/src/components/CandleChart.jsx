import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { drawPath, motionOff } from '../anim'

// Grafik candle 1 ticker. SVG tulisan tangan seperti Sparkline/VBarChart yang
// sudah ada -- proyek ini cuma punya react + react-dom, dan satu grafik harga
// bukan alasan menambah pustaka chart 100+ KB.
//
// Datanya dari /api/ticker/<t>/ohlc, yang membaca cache pipeline
// (.cache/price_history) -- NOL panggilan jaringan baru ke Yahoo. Konsekuensinya
// bar terakhir bisa tertinggal dari kutipan live di kepala modal; itu DILABELI
// di kaki grafik, bukan disamarkan.

const RANGES = [30, 90, 180]
const W = 980
const H = 300
const PAD_L = 6
const PAD_R = 54          // ruang label harga + penanda katalis di margin
const VOL_H = 44
const VOL_Y = H - VOL_H
const PRICE_H = VOL_Y - 16

export default function CandleChart({ ticker, catalystDate }) {
  const [days, setDays] = useState(90)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    setData(null)
    setError(null)
    api.tickerOhlc(ticker, days)
      .then((d) => { if (!cancelled) setData(d) })
      .catch((e) => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [ticker, days])

  return (
    <div className="candle-block">
      <div className="candle-head">
        <span className="candle-title">Harga — {days} hari</span>
        <div className="candle-ranges">
          {RANGES.map((r) => (
            <button
              key={r}
              className={`candle-range${r === days ? ' active' : ''}`}
              onClick={() => setDays(r)}
            >
              {r}H
            </button>
          ))}
        </div>
      </div>
      {error && <div className="empty">Gagal memuat bar harga: {error}</div>}
      {!error && !data && <div className="loading">Memuat bar harga…</div>}
      {data && !data.available && (
        <div className="empty">
          Belum ada bar harga ter-cache untuk {ticker}
          {data.reason ? ` — ${data.reason}` : ''}.
        </div>
      )}
      {data?.available && <CandleSvg data={data} catalystDate={catalystDate} />}
    </div>
  )
}

function CandleSvg({ data, catalystDate }) {
  const maPath = useRef(null)
  const svgRef = useRef(null)
  const bars = data.bars

  const geo = useMemo(() => computeGeometry(bars), [bars])

  useEffect(() => {
    drawPath(maPath.current, { duration: 900, delay: 250 })
    if (motionOff() || !svgRef.current) return
    // Tiap bar tumbuh dari titik tengah body-nya sendiri, bukan dari dasar
    // grafik: bar yang "jatuh dari atas" membaca seperti harga bergerak,
    // padahal itu cuma animasi masuk.
    const groups = svgRef.current.querySelectorAll('.cdl')
    groups.forEach((g, i) => {
      g.animate(
        [{ transform: 'scaleY(0.02)', opacity: 0 }, { transform: 'none', opacity: 1 }],
        { duration: 300, delay: i * 6, easing: 'cubic-bezier(.22,.8,.3,1)', fill: 'backwards' },
      )
    })
  }, [geo])

  const { candles, gridLines, maD, hi, lo } = geo
  const last = bars[bars.length - 1]
  const prev = bars.length > 1 ? bars[bars.length - 2] : null
  const chgPct = prev && prev[4] ? ((last[4] - prev[4]) / prev[4]) * 100 : null

  return (
    <>
      <div className="candle-meta">
        <span>
          TUTUP <b>${last[4].toFixed(2)}</b>
        </span>
        {chgPct != null && (
          <span className={chgPct >= 0 ? 'good' : 'bad'}>
            {chgPct >= 0 ? '▲' : '▼'} {Math.abs(chgPct).toFixed(2)}%
          </span>
        )}
        <span>bar {data.last_bar}</span>
        <span className="faint">tinggi {hi.toFixed(2)} · rendah {lo.toFixed(2)}</span>
        <span className="acc">— MA20</span>
        {catalystDate && <span className="warn">┊ katalis {catalystDate} (di luar rentang)</span>}
      </div>

      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} className="candle-svg">
        {gridLines.map((g) => (
          <g key={g.y}>
            <line className="cdl-grid" x1={PAD_L} y1={g.y} x2={W - PAD_R} y2={g.y} />
            <text className="cdl-ax" x={W - PAD_R + 6} y={g.y + 3}>{g.label}</text>
          </g>
        ))}
        <line className="cdl-grid" x1={PAD_L} y1={VOL_Y} x2={W - PAD_R} y2={VOL_Y} />
        <text className="cdl-ax" x={PAD_L} y={H - 2}>VOLUME</text>
        <text className="cdl-ax" x={PAD_L} y={VOL_Y - 5}>{bars[0][0]}</text>
        <text className="cdl-ax" x={W - PAD_R - 60} y={VOL_Y - 5}>{last[0]}</text>

        {/* Katalis hampir selalu di MASA DEPAN, di luar rentang bar. Penandanya
            karena itu duduk di margin kanan -- menggambarnya di bar terakhir
            berarti menaruh peristiwa besok di tanggal kemarin. */}
        {catalystDate && (
          <line className="cdl-event" x1={W - PAD_R + 2} y1={4} x2={W - PAD_R + 2} y2={VOL_Y} />
        )}

        {candles.map((c) => (
          <g className="cdl" key={c.d} style={{ transformOrigin: `0 ${c.mid}px` }}>
            <line className={`cdl-wick ${c.up ? 'up' : 'down'}`} x1={c.x} y1={c.yH} x2={c.x} y2={c.yL} />
            <rect className={`cdl-body ${c.up ? 'up' : 'down'}`} x={c.bx} y={c.by} width={c.bw} height={c.bh} />
            <rect className={`cdl-vol ${c.up ? 'up' : 'down'}`} x={c.bx} y={c.vy} width={c.bw} height={c.vh} />
          </g>
        ))}

        {maD && <path ref={maPath} className="cdl-ma" d={maD} />}
      </svg>

      <div className="candle-foot">
        Bar dari cache pipeline (Screening), umur {data.age_hours} jam — bukan kutipan live.
        Harga sekarang ada di badge kepala modal.
      </div>
    </>
  )
}

function computeGeometry(bars) {
  const highs = bars.map((b) => b[2] ?? b[4])
  const lows = bars.map((b) => b[3] ?? b[4])
  const hi = Math.max(...highs)
  const lo = Math.min(...lows)
  const pad = (hi - lo) * 0.06 || 1
  const top = hi + pad
  const bot = lo - pad
  const y = (p) => ((top - p) / (top - bot)) * PRICE_H + 8

  const cw = (W - PAD_L - PAD_R) / bars.length
  const bw = Math.max(1.5, cw * 0.62)
  const maxVol = Math.max(...bars.map((b) => b[5] || 0)) || 1

  const candles = bars.map((b, i) => {
    const [d, o, h, l, c, v] = b
    const open = o ?? c
    const x = PAD_L + i * cw + cw / 2
    const yO = y(open)
    const yC = y(c)
    const vh = ((v || 0) / maxVol) * (VOL_H - 6)
    return {
      d, x, up: c >= open,
      yH: y(h ?? Math.max(open, c)), yL: y(l ?? Math.min(open, c)),
      bx: x - bw / 2, bw,
      by: Math.min(yO, yC), bh: Math.max(1.2, Math.abs(yC - yO)),
      mid: (yO + yC) / 2,
      vy: H - vh, vh,
    }
  })

  const gridLines = []
  for (let i = 0; i <= 4; i++) {
    const p = top - (top - bot) * (i / 4)
    gridLines.push({ y: +y(p).toFixed(1), label: p.toFixed(p >= 100 ? 0 : 2) })
  }

  // MA20 baru mulai di bar ke-20 -- bar sebelumnya TIDAK dipaksa punya nilai
  // (rata-rata dari 3 bar bukan MA20, cuma terlihat seperti itu).
  const closes = bars.map((b) => b[4])
  const maPts = []
  for (let i = 19; i < closes.length; i++) {
    const avg = closes.slice(i - 19, i + 1).reduce((a, b) => a + b, 0) / 20
    maPts.push([PAD_L + i * cw + cw / 2, y(avg)])
  }
  const maD = maPts.length
    ? maPts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')
    : null

  return { candles, gridLines, maD, hi, lo }
}
