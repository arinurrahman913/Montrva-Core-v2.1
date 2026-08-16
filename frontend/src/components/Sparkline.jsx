// Dekoratif sparkline (area chart bergradien) untuk Layer 1 cards.
// Bentuk garis ditentukan `trend` (up/down/flat) + wiggle deterministik dari `seed`,
// jadi tiap komponen punya siluet yang khas tapi stabil antar-render.

function hashStr(s) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function mulberry32(a) {
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export default function Sparkline({ trend = 'flat', color = '#e8b84b', seed = 'x', height = 52, points = 9 }) {
  const rnd = mulberry32(hashStr(seed))
  const W = 320
  const pad = 6
  const n = points
  const vals = []
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1)
    let base = trend === 'up' ? 0.18 + 0.64 * t : trend === 'down' ? 0.82 - 0.64 * t : 0.5
    base += (rnd() - 0.5) * 0.2
    vals.push(Math.max(0.06, Math.min(0.94, base)))
  }
  const x = (i) => pad + (W - 2 * pad) * (i / (n - 1))
  const y = (v) => pad + (height - 2 * pad) * (1 - v)
  const line = vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(n - 1).toFixed(1)},${height} L${x(0).toFixed(1)},${height} Z`
  const gid = `sg-${seed}`

  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} preserveAspectRatio="none" style={{ display: 'block' }}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.34" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={x(n - 1)} cy={y(vals[n - 1])} r="3" fill={color} />
    </svg>
  )
}

// Sparkline DATA NYATA -- sengaja komponen terpisah dari `Sparkline` di atas,
// yang bentuknya dikarang dari seed. Keduanya terlihat sama di layar, dan itu
// persis bahayanya: satu grafik karangan yang nyelip di tabel angka akan
// dibaca sebagai riwayat sungguhan. Yang ini menerima deret apa adanya dan
// tidak menggambar apa pun kalau titiknya kurang dari dua.
export function DataSpark({ values, width = 88, height = 26, pathRef = null }) {
  const pts = (values || []).filter((v) => v != null && Number.isFinite(v))
  if (pts.length < 2) {
    return <span style={{ fontSize: 10, color: 'var(--faint)' }}>—</span>
  }
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const span = max - min || 1
  const x = (i) => 1 + (i / (pts.length - 1)) * (width - 2)
  const y = (v) => height - 2 - ((v - min) / span) * (height - 4)
  const d = pts.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  const rising = pts[pts.length - 1] >= pts[pts.length - 2]
  const color = rising ? 'var(--good)' : 'var(--bad)'

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} style={{ display: 'block' }}>
      <path ref={pathRef} d={d} fill="none" stroke={color} strokeWidth="1.3" opacity=".9" />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1])} r="1.9" fill={color} />
    </svg>
  )
}

// Bar "N/total input" untuk komponen degraded (mis. market_sentiment).
export function InputBar({ used = 1, total = 4, color = '#4fd1e0', height = 34 }) {
  const W = 320
  const fill = (W * used) / total
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width="100%" height={height} style={{ display: 'block' }}>
      <rect x="0" y="13" width={W} height="8" rx="4" fill="rgba(255,255,255,.06)" />
      <rect x="0" y="13" width={fill} height="8" rx="4" fill={color} />
      <text x={Math.min(fill + 8, 210)} y="21" fontSize="10" fill="#7c8595">
        {used}/{total} input tersedia
      </text>
    </svg>
  )
}
