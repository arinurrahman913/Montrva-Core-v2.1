import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import ThesisProof from '../components/ThesisProof'
import { MODULE_LABELS, outcomeClass, prettyOutcome, prettyAction } from '../format'

const MODULES = ['multibagger', 'quality_compound', 'speculative']

// Isi expand row: per lens, entry yang SUDAH dievaluasi (outcome != null)
// nampilin badge terbukti/meleset/tidak_berlaku; yang BELUM (masih dalam
// horizon) nampilin ThesisProof yang sama persis dengan kartu top pick di
// Agregator Pribadi -- grafik live + progress bar "hari ke-berapa dari
// horizon" -- supaya tesis yang masih aktif kelihatan "live" di sini juga,
// bukan cuma pas masih jadi top pick baru.
function ExpandedTimeline({ ticker, entry }) {
  const callSet = entry?.personal_call_set || {}
  const outcome = entry?.outcome || null
  const lenses = MODULES.map((m) => ({ module: m, call: callSet[m] })).filter((l) => l.call)

  if (lenses.length === 0) {
    return <div style={{ padding: '12px 14px 14px 30px', fontSize: 11, color: 'var(--faint)' }}>Tidak ada data lens untuk snapshot ini.</div>
  }

  return (
    <div style={{ padding: '12px 14px 14px 30px', background: 'var(--panel2)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
        {lenses.map(({ module, call }) => {
          const moduleOutcome = outcome?.[module]
          return (
            <div key={module}>
              <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--faint)', marginBottom: 6 }}>
                {MODULE_LABELS[module]} — {prettyAction(call.action)}
              </div>
              {moduleOutcome ? (
                <div>
                  <span className={`pill ${outcomeClass(moduleOutcome.classification)}`}>{prettyOutcome(moduleOutcome.classification)}</span>
                  {moduleOutcome.return_pct != null && (
                    <span style={{ marginLeft: 8, fontSize: 11, fontFamily: 'var(--mono)', color: 'var(--dim)' }}>
                      {moduleOutcome.return_pct >= 0 ? '+' : ''}{moduleOutcome.return_pct}%
                    </span>
                  )}
                </div>
              ) : (
                <ThesisProof ticker={ticker} module={module} action={call.action} horizon={call.horizon} />
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

export default function PersonalHistoricalView({ onSelectTicker }) {
  const { data, error } = useStageData(api.personalHistory)
  const { data: dueData } = useStageData(api.personalDueForReview)

  if (error) return <div className="empty">Gagal memuat data/personal/personal_history.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const timelines = Object.values(data)
  const totalEntries = timelines.reduce((s, t) => s + (t.total_entries || 0), 0)
  const dueSet = new Set(dueData?.due_for_review || [])

  const lastEntry = (t) => (t.entries && t.entries.length ? t.entries[t.entries.length - 1] : null)

  // Skor track-record keseluruhan -- dihitung dari SEMUA entry yang sudah
  // dievaluasi (bukan cuma entry terakhir), supaya "berapa persen tesisku
  // beneran kejadian" kelihatan lintas waktu, bukan cuma snapshot sekarang.
  let terbukti = 0, meleset = 0, ambigu = 0
  for (const t of timelines) {
    for (const e of t.entries || []) {
      if (!e.outcome) continue
      for (const m of MODULES) {
        const c = e.outcome[m]?.classification
        if (c === 'terbukti') terbukti++
        else if (c === 'meleset') meleset++
        else if (c === 'ambigu') ambigu++
      }
    }
  }
  const evaluatedTotal = terbukti + meleset + ambigu
  const accuracyPct = evaluatedTotal > 0 ? (terbukti / evaluatedTotal) * 100 : null

  const stats = [
    { label: 'Tickers Tracked', value: timelines.length },
    { label: 'Total Snapshots', value: totalEntries },
    { label: 'Layak Ditinjau Ulang', value: dueSet.size, tone: dueSet.size ? 'warn' : undefined },
    {
      label: 'Track Record',
      value: accuracyPct != null ? `${accuracyPct.toFixed(0)}%` : '—',
      tone: accuracyPct == null ? undefined : accuracyPct >= 50 ? 'good' : 'bad',
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
      label: 'Outcome (snapshot terakhir)',
      render: (r) => {
        const e = lastEntry(r)
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
        Outcome dievaluasi otomatis begitu call jatuh tempo (umur snapshot &gt; batas atas horizon-nya), dibandingkan
        pergerakan harga sejak action itu pertama muncul terhadap threshold per horizon. Cuma action berklaim arah
        (masuk/keluar) yang dievaluasi — <code>pantau</code>/<code>tahan</code>/<code>tunggu_katalis</code> selalu
        "tidak berlaku". Klik baris untuk lihat rincian live snapshot terakhir, klik nama ticker untuk buka detail lengkap.
      </p>
      <DataTable
        columns={columns}
        rows={timelines}
        renderExpanded={(r) => <ExpandedTimeline ticker={r.ticker} entry={lastEntry(r)} />}
      />
    </>
  )
}
