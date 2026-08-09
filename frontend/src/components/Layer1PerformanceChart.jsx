// S&P 500 vs Layer Score overlay — validate MONTRVA captures market moves.
// Both normalized 0-100 untuk easy visual comparison.

export default function Layer1PerformanceChart({ spxHistory, layerHistory }) {
  if (!spxHistory || spxHistory.length === 0 || !layerHistory || layerHistory.length === 0) {
    return (
      <div className="chart-card" style={{ padding: '20px' }}>
        <div className="chart-title">S&P 500 vs Layer Score</div>
        <p className="l1-trend-empty">Belum cukup data untuk comparison (butuh ≥2 entry di history)</p>
      </div>
    )
  }

  const W = 640
  const H = 150
  const PAD_Y = 12

  // Align dates: spx banyak data (90 hari), layer sedikit (mungkin cuma 1-10). Ambil overlap.
  //
  // Bar dengan norm_score null DIBUANG lebih dulu. 2026-08-04: run selesai
  // 00:06 sehingga Yahoo mengembalikan bar untuk hari yang belum
  // diperdagangkan -- seluruh nilainya NaN, dan json_safe menulisnya sebagai
  // null. `null.toFixed()` di bawah melempar TypeError, dan karena tidak ada
  // error boundary, SATU angka kosong ini mengosongkan seluruh dashboard
  // (bukan cuma chart ini). Backend juga sudah dibuat tidak menulis bar
  // seperti itu; filter di sini tetap dipertahankan karena data lama sudah
  // terlanjur ada di disk dan komponen tidak boleh percaya begitu saja.
  const layerDates = new Set(layerHistory.map((e) => e.date))
  const spxFiltered = spxHistory.filter(
    (s) => layerDates.has(s.date) && typeof s.norm_score === 'number' && Number.isFinite(s.norm_score),
  )

  if (spxFiltered.length < 2) {
    return (
      <div className="chart-card" style={{ padding: '20px' }}>
        <div className="chart-title">S&P 500 vs Layer Score</div>
        <p className="l1-trend-empty">Overlap tanggal minimal — perlu history lebih banyak untuk validasi</p>
      </div>
    )
  }

  // Normalkan keduanya ke 0-100 dalam window overlap (untuk fair comparison)
  const spxScores = spxFiltered.map((s) => s.norm_score)
  // Sisi Layer dijaga dengan alasan yang sama: satu final_score kosong (mis.
  // entri history dari run yang gagal di tengah) tidak boleh menjatuhkan
  // seluruh halaman.
  const layerScores = layerHistory
    .map((l) => l.final_score)
    .filter((v) => typeof v === 'number' && Number.isFinite(v))

  if (spxScores.length < 2 || layerScores.length === 0) {
    return (
      <div className="chart-card" style={{ padding: '20px' }}>
        <div className="chart-title">S&P 500 vs Layer Score</div>
        <p className="l1-trend-empty">Data harga belum lengkap untuk periode ini</p>
      </div>
    )
  }

  const y = (v) => PAD_Y + (H - 2 * PAD_Y) * (1 - v / 100)
  const n = spxFiltered.length
  const xPx = (i) => (n > 1 ? (i / (n - 1)) * W : 0)

  const spxLine = spxScores.map((v, i) => `${i === 0 ? 'M' : 'L'}${xPx(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const layerLine = layerScores
    .map((v, i) => {
      if (i >= spxScores.length) return null
      return `${i === 0 ? 'M' : 'L'}${xPx(i).toFixed(1)},${y(v).toFixed(1)}`
    })
    .filter(Boolean)
    .join(' ')

  const lastSpxScore = spxScores[spxScores.length - 1]
  const lastLayerScore = layerScores[layerScores.length - 1]
  const delta = lastLayerScore - lastSpxScore

  return (
    <div className="chart-card">
      <div className="chart-title">S&P 500 vs Layer Score</div>
      <div className="perf-subtitle">Validation: apakah MONTRVA memimpin atau mengonfirmasi perubahan market?</div>

      <div style={{ marginTop: 14 }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block' }}>
          <defs>
            <linearGradient id="spx-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#f0776a" stopOpacity="0.15" />
              <stop offset="1" stopColor="#f0776a" stopOpacity="0" />
            </linearGradient>
            <linearGradient id="layer-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#e8b84b" stopOpacity="0.15" />
              <stop offset="1" stopColor="#e8b84b" stopOpacity="0" />
            </linearGradient>
          </defs>

          {/* Background zones */}
          <rect x="0" y={y(65)} width={W} height={y(50) - y(65)} fill="rgba(74,222,128,.08)" />
          <rect x="0" y={y(50)} width={W} height={y(35) - y(50)} fill="rgba(232,184,75,.08)" />
          <rect x="0" y={y(35)} width={W} height={y(0) - y(35)} fill="rgba(240,119,106,.08)" />

          {/* S&P 500 area + line */}
          <path d={`${spxLine} L${xPx(n - 1).toFixed(1)},${H} L${xPx(0).toFixed(1)},${H} Z`} fill="url(#spx-fill)" />
          <path d={spxLine} fill="none" stroke="#f0776a" strokeWidth="2.2" strokeLinecap="round" />

          {/* Layer Score area + line */}
          {layerLine && (
            <>
              <path d={`${layerLine} L${xPx(Math.min(layerScores.length - 1, n - 1)).toFixed(1)},${H} L${xPx(0).toFixed(1)},${H} Z`} fill="url(#layer-fill)" />
              <path d={layerLine} fill="none" stroke="#e8b84b" strokeWidth="2.2" strokeLinecap="round" />
            </>
          )}

          {/* Dots at end */}
          <circle cx={xPx(n - 1)} cy={y(lastSpxScore)} r="3.5" fill="#f0776a" opacity="0.5" />
          <circle cx={xPx(Math.min(layerScores.length - 1, n - 1))} cy={y(lastLayerScore)} r="3.5" fill="#e8b84b" opacity="0.5" />
        </svg>
      </div>

      <div className="perf-stats">
        <div>
          <span className="perf-label">S&P 500</span>
          <span className="perf-val" style={{ color: '#f0776a' }}>{lastSpxScore.toFixed(0)}</span>
        </div>
        <div>
          <span className="perf-label">Layer Score</span>
          <span className="perf-val" style={{ color: '#e8b84b' }}>{lastLayerScore.toFixed(0)}</span>
        </div>
        <div>
          <span className="perf-label">Δ (Layer − S&P)</span>
          <span className="perf-val" style={{ color: delta > 0 ? 'var(--good)' : 'var(--bad)' }}>
            {delta > 0 ? '+' : ''}{delta.toFixed(0)}
          </span>
        </div>
      </div>

      <div className="perf-note">
        Overlap {n} hari. Keduanya normalized 0–100 untuk perbandingan fair. Interpretation: MONTRVA{' '}
        {delta > 10 ? 'memimpin' : delta > -10 ? 'mengonfirmasi' : 'belum selaras dengan'} pergerakan S&P.
      </div>

      <div className="perf-legend">
        <span><i style={{ background: '#f0776a' }} /> S&P 500 (normalized)</span>
        <span><i style={{ background: '#e8b84b' }} /> Layer Score</span>
      </div>
    </div>
  )
}
