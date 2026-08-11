import { useEffect, useState } from 'react'
import { api } from '../api'
import { prettyAction } from '../format'

// Tabel aksi + ambang tier DIAMBIL DARI BACKEND, tidak diketik ulang di sini.
// Alasan lengkapnya ada di docstring /api/personal/action-table
// (backend/personal_routes.py). Ringkasnya: gerbang tier yang sama sudah
// tersalin di tiga tempat tanpa apa pun yang menghubungkannya, dan menyalin
// 72 sel ACTION_TABLE lagi akan membuat UI diam-diam mengajarkan aturan yang
// sudah tidak dipakai kalau tabelnya berubah.
//
// Satu permintaan untuk seluruh sesi: payload-nya kecil dan konstan sepanjang
// run, jadi hasilnya ditahan di modul — bukan di-fetch ulang tiap kartu dibuka
// (TickerModal membuka 3 kartu lensa sekaligus).
let cached = null
let inflight = null

function loadActionTable() {
  if (cached) return Promise.resolve(cached)
  if (!inflight) {
    inflight = api.personalActionTable()
      .then((d) => { cached = d; inflight = null; return d })
      .catch((e) => { inflight = null; throw e })
  }
  return inflight
}

const TIERS = ['high', 'medium', 'low']

// Jarak ke ambang TERDEKAT yang mengubah tier. Ini yang membuat gerbang skor
// berhenti terasa abstrak: call di 70,6 dan call di 88 sama-sama "high", tapi
// yang pertama cuma butuh gerakan 0,6 poin untuk jatuh ke medium dan kehilangan
// action penuhnya.
function tierDistance(score, bounds) {
  if (score == null || !bounds) return null
  const high = bounds.high
  const medium = bounds.medium
  if (score >= high) return { arah: 'turun', poin: score - high, ke: 'medium' }
  if (score >= medium) {
    const naik = high - score
    const turun = score - medium
    return naik <= turun
      ? { arah: 'naik', poin: naik, ke: 'high' }
      : { arah: 'turun', poin: turun, ke: 'low' }
  }
  return { arah: 'naik', poin: medium - score, ke: 'medium' }
}

function Step({ n, label, value, sub, tone }) {
  const warn = tone === 'warn'
  return (
    <div style={{
      background: warn ? 'rgba(251,191,122,.10)' : 'var(--panel)',
      border: `1px solid ${warn ? 'var(--warn)' : 'var(--rule)'}`,
      borderRadius: 6, padding: '7px 9px', minWidth: 0,
    }}>
      <div style={{ fontSize: 9.5, color: 'var(--faint)', marginBottom: 3 }}>{n} · {label}</div>
      <div style={{ fontSize: 11.5, fontWeight: 600, color: warn ? 'var(--warn)' : 'var(--text)', wordBreak: 'break-word' }}>{value}</div>
      {sub && <div style={{ fontSize: 9.5, color: warn ? 'var(--warn)' : 'var(--faint)', marginTop: 2, lineHeight: 1.4 }}>{sub}</div>}
    </div>
  )
}

export default function DecisionPath({ module, call }) {
  const [data, setData] = useState(cached)
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    loadActionTable().then(
      (d) => { if (alive) setData(d) },
      (e) => { if (alive) setError(String(e.message || e)) },
    )
    return () => { alive = false }
  }, [])

  if (error) return <div style={{ fontSize: 10.5, color: 'var(--faint)' }}>Tabel aksi gagal dimuat: {error}</div>
  if (!data) return <div style={{ fontSize: 10.5, color: 'var(--faint)' }}>Memuat tabel aksi…</div>

  const status = call.position_status || 'no_holding'
  const grid = data.action_table?.[status]?.[module]
  const bounds = data.score_tier_bounds
  const score = call.thesis_score
  const stance = call.source_stance
  // Tier dihitung dari ambang milik backend, bukan konstanta lokal.
  const tier = score == null ? null
    : score >= bounds.high ? 'high'
      : score >= bounds.medium ? 'medium' : 'low'
  // Keluaran TABEL (sebelum P4), bukan action final. Kalau P4 tidak menurunkan
  // apa pun, keduanya sama -- dan itu justru yang harus terlihat.
  const fromTable = call.action_downgraded_from || call.action
  const dist = tierDistance(score, bounds)

  if (!grid) {
    return <div style={{ fontSize: 10.5, color: 'var(--faint)' }}>Tabel aksi tidak punya baris untuk {module} / {status}.</div>
  }

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${call.action_downgraded_from ? 4 : 3}, minmax(0,1fr))`, gap: 6, marginBottom: 10 }}>
        <Step n="1" label="stance" value={stance || '—'} sub="dari Reasoning umum" />
        <Step
          n="2" label="skor tesis"
          value={score == null ? '—' : `${score.toFixed(0)} → ${tier}`}
          sub={dist ? `${dist.poin.toFixed(1)} poin ${dist.arah} ke ${dist.ke}` : null}
        />
        <Step n="3" label="tabel aksi" value={prettyAction(fromTable)} sub="sel yang menyala" />
        {call.action_downgraded_from && (
          <Step n="4" label="P4" value={prettyAction(call.action)} sub="diturunkan" tone="warn" />
        )}
      </div>

      {call.action_downgraded_from && (
        <div style={{ background: 'rgba(251,191,122,.10)', borderRadius: 6, padding: '7px 9px', marginBottom: 10, fontSize: 10, color: 'var(--warn)', lineHeight: 1.5 }}>
          Skornya lolos {tier}, tapi confidence lensa ini {call.source_confidence?.toFixed(1) ?? '—'} (band low) — data terlalu tipis untuk eksposur penuh, jadi eksposurnya dicicil.
        </div>
      )}

      <div style={{ fontSize: 9.5, color: 'var(--faint)', marginBottom: 4 }}>
        tabel aksi · {module} · {status === 'holding' ? 'sedang dipegang' : 'belum punya posisi'}
      </div>
      <div style={{ border: '1px solid var(--rule)', borderRadius: 6, overflow: 'hidden' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr 1fr 1fr', fontSize: 9.5, color: 'var(--faint)', padding: '6px 8px', borderBottom: '1px solid var(--rule)' }}>
          <div>stance</div>{TIERS.map((t) => <div key={t}>{t}</div>)}
        </div>
        {Object.entries(grid).map(([rowStance, byTier], i) => {
          const aktif = rowStance === stance
          return (
            <div
              key={rowStance}
              style={{
                display: 'grid', gridTemplateColumns: '1.5fr 1fr 1fr 1fr', fontSize: 10.5,
                padding: '6px 8px', alignItems: 'center',
                borderTop: i === 0 ? 'none' : '1px solid var(--rule)',
                background: aktif ? 'var(--accent-glow)' : 'transparent',
                color: aktif ? 'var(--text)' : 'var(--dim)',
              }}
            >
              <div style={{ fontWeight: aktif ? 600 : 400, wordBreak: 'break-word' }}>{rowStance}</div>
              {TIERS.map((t) => {
                const menyala = aktif && t === tier
                return (
                  <div key={t} style={{ fontWeight: menyala ? 600 : 400, color: menyala ? 'var(--accent)' : 'inherit' }}>
                    {menyala ? '● ' : ''}{prettyAction(byTier[t])}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}
