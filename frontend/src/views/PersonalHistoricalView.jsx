import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import ThesisProof, { RiskBadge } from '../components/ThesisProof'
import { MODULE_LABELS, outcomeClass, prettyOutcome, prettyAction, BEST_ACTION, rankPersonalPicks } from '../format'

const MODULES = ['multibagger', 'quality_compound', 'speculative']

// Isi expand row: per lens, entry yang SUDAH dievaluasi (outcome != null)
// nampilin badge terbukti/meleset/tidak_berlaku; yang BELUM (masih dalam
// horizon) nampilin ThesisProof yang sama persis dengan kartu top pick di
// Agregator Pribadi -- grafik live + progress bar "hari ke-berapa dari
// horizon" -- supaya tesis yang masih aktif kelihatan "live" di sini juga,
// bukan cuma pas masih jadi top pick baru.
// Per lensa, ambil snapshot TERBARU di mana lensa itu benar-benar jadi top
// pick -- itulah tesis yang sedang dilacak baris ini. Sebelum 2026-08-03
// panel ini membaca snapshot TERAKHIR saja, padahal baris bertahan selamanya
// begitu pernah masuk top-3: 950 dari 2.475 ticker (38%) aksinya sudah
// berubah hari ini (mis. FLYW yang 27-31 Jul top pick Spekulatif, sekarang
// tunggu_katalis), jadi filternya kosong dan panelnya cuma bilang "tidak ada
// data lens" -- persis untuk ticker yang riwayatnya justru paling menarik.
function lensThreads(timeline) {
  const entries = timeline?.entries || []
  const threads = []
  for (const m of MODULES) {
    let chosen = null
    for (const e of entries) {
      const call = (e.personal_call_set || {})[m]
      if (call && call.position_status === 'no_holding' && call.action === BEST_ACTION[m]) chosen = e
    }
    if (chosen) threads.push({ module: m, entry: chosen, call: chosen.personal_call_set[m] })
  }
  return threads
}

// Outcome ditulis backend ke snapshot yang jatuh tempo, yang belum tentu
// snapshot tempat tesis ini pertama muncul -- jadi dicari ke seluruh timeline,
// bukan cuma di entry yang dipilih di atas.
function latestOutcomeFor(timeline, module) {
  let found = null
  for (const e of timeline?.entries || []) {
    if (e.outcome?.[module]) found = e.outcome[module]
  }
  return found
}

function ExpandedTimeline({ ticker, timeline }) {
  const entries = timeline?.entries || []
  const lastCallSet = entries.length ? (entries[entries.length - 1].personal_call_set || {}) : {}
  const lastDate = entries.length ? (entries[entries.length - 1].analyzed_at || '').slice(0, 10) : null
  const lenses = lensThreads(timeline)

  if (lenses.length === 0) {
    return <div style={{ padding: '12px 14px 14px 30px', fontSize: 11, color: 'var(--faint)' }}>Tidak ada data lens untuk snapshot ini.</div>
  }

  return (
    <div style={{ padding: '12px 14px 14px 30px', background: 'var(--panel2)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
        {lenses.map(({ module, call, entry }) => {
          const moduleOutcome = latestOutcomeFor(timeline, module)
          const threadDate = (entry.analyzed_at || '').slice(0, 10)
          const nowAction = (lastCallSet[module] || {}).action
          const stillTopPick = nowAction === call.action
          return (
            <div key={module}>
              <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--faint)', marginBottom: 6 }}>
                {MODULE_LABELS[module]} — {prettyAction(call.action)}
              </div>
              <div style={{ fontSize: 10, color: 'var(--faint)', marginBottom: 6 }}>
                {stillTopPick ? (
                  <>masih berlaku · snapshot {threadDate}</>
                ) : (
                  <>
                    dari snapshot {threadDate} · sekarang{' '}
                    <span style={{ color: 'var(--dim)' }}>{nowAction ? prettyAction(nowAction) : '—'}</span>
                    {lastDate ? ` (${lastDate})` : ''}
                  </>
                )}
              </div>
              <RiskBadge call={call} />
              {moduleOutcome ? (
                <div>
                  <span className={`pill ${outcomeClass(moduleOutcome.classification)}`}>{prettyOutcome(moduleOutcome.classification)}</span>
                  {moduleOutcome.return_pct != null && (
                    <span style={{ marginLeft: 8, fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--dim)' }}>
                      {moduleOutcome.return_pct >= 0 ? '+' : ''}{moduleOutcome.return_pct}%
                    </span>
                  )}
                  {moduleOutcome.excess_return_pct != null && (
                    <div
                      style={{ marginTop: 4, fontSize: 10, color: moduleOutcome.excess_return_pct >= 0 ? 'var(--good)' : 'var(--bad)' }}
                      title="Return dikurangi return S&P 500 pada jendela waktu yang sama -- naik 3% saat indeks naik 8% itu tertinggal, bukan berhasil"
                    >
                      {moduleOutcome.excess_return_pct >= 0 ? '+' : ''}{moduleOutcome.excess_return_pct}% vs S&amp;P 500
                    </div>
                  )}
                  {moduleOutcome.baseline === 'price_history' && (
                    <div style={{ marginTop: 3, fontSize: 9, color: 'var(--faint)' }} title="price_at_call tidak tersimpan untuk entry ini (dibuat sebelum field ini ada) -- direkonstruksi dari price_history, yang cuma menyimpan ~1 tahun">
                      ⓘ harga entry direkonstruksi (bukan harga asli saat call)
                    </div>
                  )}
                </div>
              ) : (
                <ThesisProof ticker={ticker} module={module} action={call.action} horizon={call.horizon} horizonAnchor={call.horizon_anchor} />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// Pola dasarnya SAMA dengan HistoricalView.jsx (publik): snapshot utuh per
// hari. BEDA dari publik: outcome DI SINI dievaluasi mekanis begitu call
// jatuh tempo (personal_evaluation.py, disetujui pengguna 2026-07-27) --
// bukan ditunda selamanya seperti Historical publik, karena lapisan ini
// memang dirancang buat satu orang yang menerima risiko menilai sendiri.
function outcomeSummary(entry) {
  if (!entry?.outcome) return null
  const counts = { terbukti: 0, meleset: 0, ambigu: 0 }
  for (const m of MODULES) {
    const c = entry.outcome[m]?.classification
    if (c in counts) counts[c] += 1
  }
  return counts
}

// Top pick "beneran" = TEPAT sama dengan yang dihitung PersonalAggregatorView
// (topPicks(): urutan thesis_score, top 3 per lensa) -- BUKAN sekadar
// "action-nya cocok kategori" (itu terlalu longgar, bisa puluhan ticker per
// lensa lolos kategori tapi cuma 3 yang beneran tampil sebagai card). HARUS
// pakai metrik ranking yang SAMA PERSIS dengan Aggregator (thesis_score, bukan
// source_confidence -- lihat catatan panjang di topPicks() PersonalAggregator-
// View.jsx) atau dua halaman ini akan disagree lagi seperti sebelum audit
// 2026-07-27. Karena personal_history.json disimpan PER TICKER (bukan per
// hari lintas semua ticker), top-3 per hari harus dihitung ULANG di sini
// dengan menggabungkan SEMUA timeline dulu, dikelompokkan per (tanggal,
// lensa) -- baru bisa tahu siapa saja yang benar-benar masuk 3 besar hari itu.
const TOP_N = 3

function buildTopThreeIndex(allTimelines) {
  const byDayModule = new Map() // "YYYY-MM-DD|module" -> [{ticker, score}]
  for (const timeline of allTimelines) {
    for (const entry of timeline.entries || []) {
      const day = (entry.analyzed_at || '').slice(0, 10)
      if (!day) continue
      const callSet = entry.personal_call_set || {}
      for (const m of MODULES) {
        const call = callSet[m]
        if (call && call.position_status === 'no_holding' && call.action === BEST_ACTION[m]) {
          const key = `${day}|${m}`
          if (!byDayModule.has(key)) byDayModule.set(key, [])
          // Fallback thesis_score -> source_confidence (data lama, dari sebelum
          // audit 2026-07-27/28 yang menambahkan field itu) ditangani di dalam
          // comparePersonalPicks, bukan di sini -- supaya persis sama dengan
          // yang dipakai Agregator. Tanpa fallback itu semua entry lama ikut
          // default ke 50 dan seri, jadi ticker yang DULU benar-benar tampil
          // sebagai top-pick card bisa kalah undian ulang sekarang cuma karena
          // datanya lebih tua dari field ini (bug nyata, ditemukan live: AMD
          // hilang dari Riwayat padahal terverifikasi pernah tampil sebagai
          // card di Agregator pada 2026-07-27).
          byDayModule.get(key).push({ ticker: timeline.ticker, call })
        }
      }
    }
  }
  const topSetByDayModule = new Map()
  for (const [key, list] of byDayModule) {
    // Peringkat yang SAMA PERSIS dengan Agregator (comparePersonalPicks) --
    // dulu di sini cuma `b.score - a.score`, dan karena Array.sort stabil,
    // pemenang seri ditentukan urutan array: halaman ini iterasi
    // personal_history.json (urutan ticker pertama kali tercatat) sedangkan
    // Agregator iterasi personal_calls.json (NASDAQ dulu baru NYSE), jadi
    // dua halaman bisa menyebut top-3 yang beda untuk hari yang sama.
    topSetByDayModule.set(key, new Set(rankPersonalPicks(list).slice(0, TOP_N).map((x) => x.ticker)))
  }
  return topSetByDayModule
}

// Begitu satu ticker PERNAH beneran masuk 3 besar (hari mana pun, lensa mana
// pun), dia tetap kesimpen di sini SELAMANYA walau sekarang sudah keluar dari
// daftar top pick Agregator (holding, atau kesalip confidence ticker lain) --
// itu justru intinya: record utuh sampai jatuh tempo, apa pun hasilnya nanti.
function wasEverTopThree(timeline, topSetByDayModule) {
  return (timeline.entries || []).some((entry) => {
    const day = (entry.analyzed_at || '').slice(0, 10)
    return MODULES.some((m) => topSetByDayModule.get(`${day}|${m}`)?.has(timeline.ticker))
  })
}

export default function PersonalHistoricalView({ onSelectTicker }) {
  const { data, error } = useStageData(api.personalHistory)
  const { data: dueData } = useStageData(api.personalDueForReview)

  if (error) return <div className="empty">Gagal memuat data/personal/personal_history.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const allTimelines = Object.values(data)
  const topThreeIndex = buildTopThreeIndex(allTimelines)
  const timelines = allTimelines.filter((t) => wasEverTopThree(t, topThreeIndex))
  const totalEntries = timelines.reduce((s, t) => s + (t.total_entries || 0), 0)
  // /api/personal/due-for-review menghitung lintas SEMUA ticker (bukan cuma
  // yang pernah top pick) -- irisan ke timelines yang sudah difilter di atas
  // supaya angka "Layak Ditinjau Ulang" konsisten dengan baris yang tampil.
  const trackedTickers = new Set(timelines.map((t) => t.ticker))
  const dueSet = new Set((dueData?.due_for_review || []).filter((t) => trackedTickers.has(t)))

  const lastEntry = (t) => (t.entries && t.entries.length ? t.entries[t.entries.length - 1] : null)
  const lastEvaluatedEntry = (t) => {
    const es = t.entries || []
    for (let i = es.length - 1; i >= 0; i--) if (es[i].outcome) return es[i]
    return null
  }

  // Skor track-record -- dihitung per TESIS (thesis_key), bukan per entry
  // harian. Backend (personal_evaluation.py) menulis outcome yang SAMA ke
  // semua snapshot harian dalam satu streak, jadi tanpa dedupe di sini satu
  // tesis yang bertahan 200 hari akan menghitung 200 kali dalam statistik --
  // mendominasi track record cuma karena snapshot-nya lebih banyak, bukan
  // karena lebih sering benar. Set `seenThesisKeys` memastikan tiap thesis_key
  // unik cuma dihitung sekali walau muncul di puluhan entry.
  const seenThesisKeys = new Set()
  let terbukti = 0, meleset = 0, ambigu = 0
  let excessSum = 0, excessCount = 0
  for (const t of timelines) {
    for (const e of t.entries || []) {
      if (!e.outcome) continue
      for (const m of MODULES) {
        const o = e.outcome[m]
        if (!o) continue
        const key = o.thesis_key || `${t.ticker}:${m}:${e.analyzed_at}`
        if (seenThesisKeys.has(key)) continue
        seenThesisKeys.add(key)
        if (o.classification === 'terbukti') terbukti++
        else if (o.classification === 'meleset') meleset++
        else if (o.classification === 'ambigu') ambigu++
        if (o.excess_return_pct != null) { excessSum += o.excess_return_pct; excessCount += 1 }
      }
    }
  }
  const evaluatedTotal = terbukti + meleset + ambigu
  const accuracyPct = evaluatedTotal > 0 ? (terbukti / evaluatedTotal) * 100 : null
  const avgExcessPct = excessCount > 0 ? excessSum / excessCount : null

  const stats = [
    { label: 'Pernah Jadi Top Pick', value: timelines.length },
    { label: 'Total Snapshots', value: totalEntries },
    { label: 'Layak Ditinjau Ulang', value: dueSet.size, tone: dueSet.size ? 'warn' : undefined },
    {
      label: 'Track Record',
      value: accuracyPct != null ? `${accuracyPct.toFixed(0)}%` : '—',
      tone: accuracyPct == null ? undefined : accuracyPct >= 50 ? 'good' : 'bad',
    },
    {
      // Dihitung per TESIS (thesis_key), sama dengan Track Record di atas.
      // Ini pembanding yang selama ini gak ada -- "Track Record 40%" tanpa
      // konteks kelihatan buruk, padahal peluang naik >=3% dalam 28 hari
      // TANPA skill apa pun sudah sekitar 38-40% (volatilitas tahunan saham
      // biasa ~30%). Excess return (vs S&P 500 pada jendela yang sama) jujur
      // menjawab "lebih baik dari sekadar nyimpen di indeks, atau enggak".
      label: 'vs S&P 500 (rata-rata)',
      value: avgExcessPct != null ? `${avgExcessPct >= 0 ? '+' : ''}${avgExcessPct.toFixed(1)}%` : '—',
      tone: avgExcessPct == null ? undefined : avgExcessPct >= 0 ? 'good' : 'bad',
    },
  ]

  const columns = [
    {
      key: 'ticker',
      label: 'Ticker',
      render: (r) => (
        <span
          className="ticker"
          onClick={(e) => { e.stopPropagation(); onSelectTicker(r.ticker) }}
          title="Buka detail lengkap ticker"
        >
          {r.ticker}
        </span>
      ),
    },
    { key: 'total', label: 'Snapshots', render: (r) => r.total_entries || 0, sortValue: (r) => r.total_entries || 0 },
    {
      key: 'last_date',
      label: 'Snapshot Terakhir',
      render: (r) => (r.last_entry_date ? r.last_entry_date.slice(0, 10) : '—'),
      sortValue: (r) => r.last_entry_date,
    },
    {
      key: 'due',
      label: 'Status',
      render: (r) =>
        dueSet.has(r.ticker) ? (
          <span className="pill warn">layak ditinjau ulang</span>
        ) : (
          <span style={{ color: 'var(--faint)' }}>—</span>
        ),
      sortValue: (r) => (dueSet.has(r.ticker) ? 1 : 0),
    },
    {
      key: 'outcome',
      label: 'Outcome (evaluasi terakhir)',
      render: (r) => {
        // Snapshot TERAKHIR selalu snapshot run hari ini, yang menurut
        // definisinya belum jatuh tempo -- membacanya membuat kolom ini
        // hampir selalu "menunggu evaluasi" walau outcome-nya ada di
        // snapshot lebih lama (live 2026-08-03: cuma 67 dari 243 ticker
        // ber-outcome yang outcome-nya kebetulan ada di entry terakhir).
        const e = lastEvaluatedEntry(r) || lastEntry(r)
        const summary = outcomeSummary(e)
        if (!summary) return <span style={{ color: 'var(--faint)' }}>menunggu evaluasi</span>
        const parts = []
        if (summary.terbukti) parts.push(<span key="t" className="pill ok" style={{ fontSize: 10 }}>{summary.terbukti} terbukti</span>)
        if (summary.meleset) parts.push(<span key="m" className="pill bad" style={{ fontSize: 10 }}>{summary.meleset} meleset</span>)
        if (summary.ambigu) parts.push(<span key="a" className="pill warn" style={{ fontSize: 10 }}>{summary.ambigu} ambigu</span>)
        return parts.length > 0 ? <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>{parts}</div> : <span style={{ color: 'var(--faint)' }}>tidak berlaku</span>
      },
    },
  ]

  return (
    <>
      <StatCards stats={stats} />
      <p className="narrative" style={{ margin: '0 0 12px', color: 'var(--dim)', fontSize: 13 }}>
        Cuma ticker yang PERNAH jadi Top Pick (lihat Agregator Pribadi) yang tercatat di sini — begitu lolos jadi top
        pick sekali, dia tetap kesimpen walau kemudian keluar dari daftar top pick (holding, atau action-nya melemah).
        Outcome dievaluasi otomatis begitu call jatuh tempo (umur snapshot &gt; batas atas horizon-nya), dibandingkan
        pergerakan harga sejak action itu pertama muncul terhadap threshold per horizon — hasilnya (terbukti/meleset)
        jadi referensi track-record ke depan. Klik baris untuk lihat rincian tiap lensa pada snapshot terakhir dia
        benar-benar jadi top pick (kalau action-nya sudah berubah, action sekarang ikut ditulis), klik nama ticker
        untuk buka detail lengkap.
      </p>
      <DataTable
        columns={columns}
        rows={timelines}
        renderExpanded={(r) => <ExpandedTimeline ticker={r.ticker} timeline={r} />}
      />
    </>
  )
}
