import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStageData } from '../useStageData'
import ThesisProof, { RiskBadge } from '../components/ThesisProof'
import {
  prettyAction, horizonLabel, prettyHorizon, BEST_ACTION, rankPersonalPicks, tieAtCutoff,
  factorLabel, splitFactors, isLegacyBreakdown, FACTOR_AXIS,
} from '../format'

const LENSES = ['multibagger', 'quality_compound', 'speculative']

const LENS_TITLES = {
  multibagger: 'Multibagger — Mulai Posisi',
  quality_compound: 'Quality — Akumulasi',
  speculative: 'Speculative — Masuk Spekulatif',
}

// Diranking pakai thesis_score (kekuatan argumen lens ini, 0-100 netral 50),
// BUKAN source_confidence (itu ternyata skor KUALITAS DATA -- turunan
// ConfidenceReport.overall.score dikurangi field yang hilang, bukan
// kekuatan tesis -- lihat reasoning.py:_module_confidence). Audit 2026-07-27
// menemukan 11 ticker seri persis di confidence yang sama padahal skor
// tesisnya beda jauh (0.26-0.76), dan 3 yang tampil sebagai card dipilih
// murni oleh urutan JS sort yang stabil, bukan argumen yang lebih kuat.
//
// Pemecah seri sekarang di comparePersonalPicks (format.js), dipakai bareng
// PersonalHistoricalView -- lihat catatan panjang di sana soal kenapa sort
// "cuma thesis_score" bikin dua halaman ini bisa menyebut top-3 yang beda
// untuk hari yang sama.
function allQualifying(callSets, module) {
  return rankPersonalPicks(
    callSets
      .map((cs) => ({ ticker: cs.ticker, call: cs[module] }))
      .filter(({ call }) => call && call.position_status === 'no_holding' && call.action === BEST_ACTION[module]),
  )
}

function topPicks(callSets, module, n = 3) {
  return allQualifying(callSets, module).slice(0, n)
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
//
// score_breakdown ikut diambil di sini, dari respons api.ticker() yang MEMANG
// SUDAH dipanggil untuk sektor+harga -- bukan request baru, dan bukan lewat
// /api/personal/ticker/. Endpoint pribadi sengaja tetap terpisah (§9 draft
// personal layer); aturan itu melarang data pribadi bocor ke jalur publik,
// bukan sebaliknya, dan kartu ini memang sudah membaca sektor & harga dari
// endpoint publik sejak awal.
function useTickerMeta(ticker, module) {
  const [meta, setMeta] = useState(null)
  useEffect(() => {
    let cancelled = false
    Promise.all([api.ticker(ticker), api.liveQuote(ticker)])
      .then(([t, l]) => {
        if (cancelled) return
        const sector = t?.evidence?.fundamental?.sector ?? null
        const livePrice = l && !l.stale && l.last_price != null ? l.last_price : null
        const fallbackPrice = t?.evidence?.price_market?.last_price ?? null
        const breakdown = t?.reasoning?.[module]?.score_breakdown ?? null
        setMeta({ sector, price: livePrice ?? fallbackPrice, breakdown })
      })
      .catch(() => { if (!cancelled) setMeta({ sector: null, price: null, breakdown: null }) })
    return () => { cancelled = true }
  }, [ticker, module])
  return meta
}

// Kenapa skornya segini -- 3 penggerak terkuat + 1 penahan. Skor tesis sudah
// lama jadi penentu kartu mana yang muncul di halaman ini (lihat
// allQualifying), tapi angkanya selalu tampil tanpa penjelasan apa pun.
//
// Batang bersumbu nol di tengah dengan skala TETAP (FACTOR_AXIS), bukan
// diskalakan ke sumbangan terbesar ticker ini sendiri -- alasannya di komentar
// FACTOR_AXIS (format.js).
function ScoreDrivers({ breakdown, thesisScore }) {
  // Format lama (satu kunci turunan) tidak dirender sama sekali -- lihat
  // isLegacyBreakdown. Blok ini baru muncul setelah pipeline dijalankan ulang.
  if (!breakdown || Object.keys(breakdown).length === 0 || isLegacyBreakdown(breakdown)) return null
  const { shown, total, hidden, hasBlocker } = splitFactors(breakdown)
  // Skor di-clamp 0-100 di reasoning.py, jadi total sumbangan bisa melebihi
  // +50 tanpa terlihat di angka skor. Diungkap, bukan disembunyikan: ticker
  // yang mentok tidak bisa dibedakan dari ticker mentok lainnya.
  const clamped = Math.abs(50 + total - thesisScore) > 0.5
  return (
    <div className="score-drivers">
      <div className="sd-label">Penggerak skor</div>
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
        {!hasBlocker && 'Tidak ada penahan · '}
        {hidden > 0 && `+${hidden} faktor lain · `}
        total {total > 0 ? '+' : ''}{total.toFixed(1)}
        {clamped && `, terklamp di ${thesisScore.toFixed(0)}`}
      </div>
    </div>
  )
}

function PickCard({ ticker, module, call, isNew, onSelectTicker }) {
  const meta = useTickerMeta(ticker, module)
  const thesisScore = call.thesis_score ?? 50
  const scoreColor = thesisScore >= 65 ? 'var(--good)' : thesisScore >= 50 ? 'var(--gold)' : 'var(--faint)'
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
        <span style={{ fontSize: 10.5, fontWeight: 700, color: scoreColor }} title="Skor kekuatan tesis lensa ini (0-100, netral 50) — dasar ranking top pick">
          skor {thesisScore.toFixed(0)}
        </span>
      </div>
      <div style={{ fontSize: 9.5, color: 'var(--faint)', marginBottom: 4 }} title="Kelengkapan & kesegaran data — BUKAN kekuatan tesis">
        data {call.source_confidence?.toFixed(0) ?? '—'}%
      </div>
      {meta?.sector && (
        <div style={{ fontSize: 10, color: 'var(--faint)', marginBottom: 4 }}>{meta.sector}</div>
      )}
      <RiskBadge call={call} />
      <ScoreDrivers breakdown={meta?.breakdown} thesisScore={thesisScore} />
      <div style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 4 }}>
        {horizonLabel(call.action)}: <span style={{ color: 'var(--dim)' }}>{prettyHorizon(call.horizon)}</span>
      </div>
      {call.horizon_basis && (
        <div style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.4 }}>{call.horizon_basis}</div>
      )}
      <ThesisProof ticker={ticker} module={module} action={call.action} horizon={call.horizon} horizonAnchor={call.horizon_anchor} />
    </div>
  )
}

// Audit 2026-07-27: 8 dari 11 kandidat teratas hari itu ternyata Technology
// (hari lain semuanya Utilities) -- ranking yang mengikuti kelengkapan/skor
// data cenderung mengelompok ke sektor yang pelaporannya seragam, dan
// sebelumnya tidak ada peringatan sama sekali. Fetch TERPISAH dari PickCard
// (yang fokus ke harga+sektor SATU ticker) -- ini menghitung sektor across
// SEMUA ticker yang tampil sebagai top pick hari ini, sekali per render.
function useSectorConcentration(tickers) {
  const [bySector, setBySector] = useState(null)
  const key = tickers.join(',')
  useEffect(() => {
    let cancelled = false
    if (tickers.length === 0) { setBySector({}); return }
    Promise.all(tickers.map((t) => api.ticker(t).catch(() => null)))
      .then((results) => {
        if (cancelled) return
        const counts = {}
        results.forEach((r, i) => {
          const sector = r?.evidence?.fundamental?.sector
          if (!sector) return
          if (!counts[sector]) counts[sector] = []
          counts[sector].push(tickers[i])
        })
        setBySector(counts)
      })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return bySector
}

function SectorConcentrationNote({ tickers }) {
  const bySector = useSectorConcentration(tickers)
  if (!bySector) return null
  const concentrated = Object.entries(bySector).filter(([, ts]) => ts.length >= 2)
  if (concentrated.length === 0) return null
  return (
    <div style={{ fontSize: 11, color: 'var(--warn)', background: 'rgba(251,191,122,.1)', padding: '7px 10px', borderRadius: 7, marginBottom: 8 }}>
      ⚠ Konsentrasi sektor: {concentrated.map(([sector, ts]) => `${sector} (${ts.join(', ')})`).join(' · ')} — top pick hari ini
      tidak terdiversifikasi, semua taruhannya bergerak bareng kalau sektor itu goyang.
    </div>
  )
}

// Audit 2026-07-29 poin #9: satu ticker yang jadi top pick di 2+ lensa
// SEKALIGUS itu konsentrasi yang lebih besar lagi daripada sekadar sektor
// sama -- bukan cuma "2 saham beda di sektor sama", tapi "1 saham yang sama
// dapat 2 rekomendasi masuk terpisah". Murni sinkron dari picksByLens yang
// sudah ada (bukan fetch baru) -- gak seperti sektor yang butuh Evidence.
function crossLensConcentration(picksByLens) {
  const lensesByTicker = {}
  for (const { module, picks } of picksByLens) {
    for (const { ticker } of picks) {
      if (!lensesByTicker[ticker]) lensesByTicker[ticker] = []
      lensesByTicker[ticker].push(module)
    }
  }
  return Object.entries(lensesByTicker).filter(([, modules]) => modules.length >= 2)
}

function CrossLensConcentrationNote({ picksByLens }) {
  const overlap = crossLensConcentration(picksByLens)
  if (overlap.length === 0) return null
  return (
    <div style={{ fontSize: 11, color: 'var(--warn)', background: 'rgba(251,191,122,.1)', padding: '7px 10px', borderRadius: 7, marginBottom: 12 }}>
      ⚠ Ticker rangkap lensa: {overlap.map(([t, modules]) => `${t} (${modules.map((m) => LENS_TITLES[m].split(' —')[0]).join(' + ')})`).join(' · ')} —
      taruhan yang sama muncul lewat 2 lensa berbeda sekaligus, bukan 2 ide terpisah.
    </div>
  )
}

// Seri di batas potong diungkap, bukan disembunyikan: judul "Action Terkuat
// per Lensa" bikin pembaca wajar mengira 3 kartu ini pemenang mutlak, padahal
// live 2026-07-31 Quality punya 60 ticker yang skor tesisnya SAMA PERSIS
// (100.0) -- yang tampil cuma 3 di antaranya, dipilih pemecah seri (risiko
// paling sedikit). Cuma dirender kalau serinya memang ada, biar tidak bising.
function TieNote({ tie }) {
  if (!tie) return null
  return (
    <div style={{ fontSize: 10, color: 'var(--faint)', marginBottom: 8, lineHeight: 1.45 }}>
      {tie.count} ticker seri di skor {tie.score.toFixed(0)} — 3 ini menang karena risikonya paling sedikit.
    </div>
  )
}

function TopPicksSection({ callSets, historyData, onSelectTicker }) {
  const picksByLens = LENSES.map((m) => {
    const all = allQualifying(callSets, m)
    return { module: m, picks: all.slice(0, 3), tie: tieAtCutoff(all, 3) }
  })
  if (picksByLens.every((p) => p.picks.length === 0)) return null
  const allTickers = [...new Set(picksByLens.flatMap((p) => p.picks.map((x) => x.ticker)))]
  // computeTopPickDiff sebelumnya DIHITUNG tapi tidak pernah dipanggil dari
  // sini -- badge "BARU" di bawah tidak pernah menyala di semua sesi
  // sebelumnya walau logikanya sudah benar (audit 2026-07-29). added/removed
  // itu terhadap SEMUA kandidat yang qualify (n=99), bukan cuma top-3 yang
  // tampil -- makanya di-Set-kan per lens supaya lookup "apa ticker top-3 ini
  // baru dibanding kemarin" gampang & O(1) saat render tiap card.
  const newTickersByLens = historyData
    ? Object.fromEntries(
        Object.entries(computeTopPickDiff(callSets, historyData)).map(([m, d]) => [m, new Set(d.added.map((x) => x.ticker))]),
      )
    : {}

  return (
    <div style={{ marginBottom: 22 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)', marginBottom: 4 }}>
        Top Pick Pribadi — Action Terkuat per Lensa
      </div>
      <p style={{ fontSize: 11, color: 'var(--faint)', margin: '2px 0 14px', lineHeight: 1.5 }}>
        Filter dari <code>action</code> yang sudah dihitung tiap lensa (cuma yang belum dipegang), diranking pakai{' '}
        <code>skor</code> (kekuatan tesis lensa ini) — bukan ranking gabungan lintas lensa (D-04 tetap berlaku).{' '}
        <code>data%</code> di kartu itu kelengkapan data, bukan kekuatan tesis — dua ukuran yang beda. Risk flag
        (kalau ada) sudah tampil di kartu, tapi tetap baca <code>horizon_basis</code> sebelum bertindak.
      </p>
      <SectorConcentrationNote tickers={allTickers} />
      <CrossLensConcentrationNote picksByLens={picksByLens} />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
        {picksByLens.map(({ module, picks, tie }) => (
          <div key={module}>
            <div style={{ fontSize: 10.5, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.03em', marginBottom: 8 }}>
              {LENS_TITLES[module]}
            </div>
            <TieNote tie={tie} />
            {picks.length === 0 ? (
              <div style={{ fontSize: 11, color: 'var(--faint)' }}>Tidak ada ticker dengan action ini saat ini.</div>
            ) : (
              picks.map(({ ticker, call }) => (
                <PickCard
                  key={ticker} ticker={ticker} module={module} call={call} onSelectTicker={onSelectTicker}
                  isNew={newTickersByLens[module]?.has(ticker) ?? false}
                />
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
  // historyData opsional (badge "BARU" cuma bonus) -- gagal/lum termuat TIDAK
  // boleh memblokir render Top Pick, jadi tidak dicek error-nya di sini.
  const { data: historyData } = useStageData(api.personalHistory)

  if (error) return <div className="empty">Gagal memuat data/personal/personal_calls.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const callSets = data.call_sets || []

  return <TopPicksSection callSets={callSets} historyData={historyData} onSelectTicker={onSelectTicker} />
}
