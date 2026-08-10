import { useState } from 'react'
import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import { fmtMoney, fmtPct, nextFilingDeadline, quarterLabel, quarterLabelSet, quarterWindow } from '../format'

// Halaman populasi: ke saham & sektor apa dana institusi bergerak, dibaca
// dari institutional_flow.json (agregasi top_holders Evidence). Tidak ada
// penilaian di sini — institusi menambah posisi BUKAN berarti sahamnya bagus.
// Semua batas sumbernya ada di montrva/layer2/institutional_flow_contracts.py
// dan diringkas di kartu catatan paling bawah halaman ini.

const fmtDate = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso + 'T00:00:00Z')
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
}

const fmtDeadline = (d) =>
  d ? d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '—'

// Kuartal berikutnya = tanggal laporan dominan + 1 kuartal. Dipakai buat
// menjawab "berapa saham yang laporan kuartal barunya sudah mulai terbaca",
// yang selalu jauh lebih kecil dari populasi sampai deadline 13F lewat.
function nextQuarterKey(primary) {
  if (!primary) return null
  const d = new Date(primary + 'T00:00:00Z')
  if (isNaN(d.getTime())) return null
  const end = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 4, 0))
  return end.toISOString().slice(0, 10)
}

const METRIC_SELECT_STYLE = {
  fontSize: 11,
  padding: '4px 8px',
  background: 'var(--panel2)',
  border: '0.5px solid var(--rule)',
  borderRadius: 4,
  color: 'var(--text)',
  cursor: 'pointer',
}

// Batang dua arah dengan sumbu nol di tengah, dua dasar yang bisa dipilih —
// dan keduanya memang perlu ada, karena masing-masing menyembunyikan hal yang
// berbeda:
//
//   arah  = net ÷ total nilai yang berpindah DI SEKTOR ITU SENDIRI. Menjawab
//           "seberapa searah dananya". Technology memindahkan ~$411 miliar ke
//           dua arah (337 saham net nambah, 179 net kurangi) dan netto cuma
//           +$6,3 miliar, jadi batangnya kecil walau dolarnya besar.
//   nilai = net dolar mentah. Menjawab "ke mana dolarnya paling banyak pergi",
//           tapi condong ke sektor besar semata-mata karena ukurannya.
//
// Versi pertama halaman ini cuma punya "arah", dan akibatnya Technology
// tampak nyaris tidak disentuh institusi padahal ia justru tujuan dolar
// terbesar (AVGO +$12,0 mld, PANW +$10,6 mld, AAPL +$5,0 mld) — persis bias
// kebalikan dari yang ingin dihindari.
function SectorFlowChart({ sectors }) {
  const [basis, setBasis] = useState('ratio')
  const valueOf = (s) => (basis === 'ratio' ? s.net_ratio_pct ?? 0 : s.net_value_usd ?? 0)
  const max = Math.max(...sectors.map((s) => Math.abs(valueOf(s))), 1)
  const ordered = [...sectors].sort((a, b) => valueOf(b) - valueOf(a))

  return (
    <div className="chart-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="chart-title" style={{ marginBottom: 0 }}>Arah per Sektor</div>
        <select value={basis} onChange={(e) => setBasis(e.target.value)} style={METRIC_SELECT_STYLE}>
          <option value="ratio">Batang: arah (net ÷ gross)</option>
          <option value="value">Batang: nilai (net $)</option>
        </select>
      </div>
      <div className="contrib-legend" style={{ marginTop: 10 }}>
        <span>
          {basis === 'ratio'
            ? 'Batang = net ÷ total nilai yang berpindah di sektor itu — sektor besar tidak otomatis panjang'
            : 'Batang = net dolar mentah — sektor besar wajar mendominasi'}
        </span>
      </div>
      {ordered.map((s) => {
        const v = valueOf(s)
        const width = (Math.abs(v) / max) * 50
        const positive = v >= 0
        return (
          <div className="contrib-row" key={s.sector}>
            <div className="contrib-label" title={`${s.tickers_net_up} saham net nambah · ${s.tickers_net_down} net kurangi`}>
              {s.sector}
            </div>
            <div className="contrib-track" style={{ position: 'relative' }}>
              <div
                className="contrib-fill"
                style={{
                  position: 'absolute',
                  left: positive ? '50%' : `${50 - width}%`,
                  width: `${width}%`,
                  background: positive ? 'var(--good)' : 'var(--bad)',
                }}
              />
              <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: 1, background: 'var(--rule-strong)' }} />
            </div>
            {/* Dua-duanya selalu ditampilkan, apa pun dasar batangnya — yang
                aktif tebal, yang lain redup. Menyembunyikan salah satunya
                persis yang membuat versi pertama halaman ini menyesatkan. */}
            <div className="contrib-val" style={{ width: 132, color: positive ? 'var(--good)' : 'var(--bad)' }}>
              <span style={{ opacity: basis === 'ratio' ? 1 : 0.5 }}>{fmtPct(s.net_ratio_pct)}</span>
              <span style={{ color: 'var(--faint)', margin: '0 5px' }}>·</span>
              <span style={{ opacity: basis === 'value' ? 1 : 0.5 }}>{fmtMoney(s.net_value_usd)}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Leaderboard({ title, rows, tone, metric, onSelectTicker }) {
  return (
    <div className="chart-card">
      <div className="chart-title" style={{ color: tone }}>{title}</div>
      {rows.length === 0 && <div className="sh-trend-empty">Tidak ada saham yang memenuhi syarat</div>}
      {rows.map((f) => (
        <div
          key={f.ticker}
          onClick={() => onSelectTicker(f.ticker)}
          style={{
            display: 'grid',
            gridTemplateColumns: '58px minmax(0,1fr) 74px',
            gap: 10,
            alignItems: 'baseline',
            padding: '7px 0',
            borderBottom: '1px solid var(--rule)',
            cursor: 'pointer',
          }}
        >
          <span className="ticker">{f.ticker}</span>
          <span style={{ minWidth: 0 }}>
            {/* Metrik yang TIDAK dipakai meranking ikut tampil di baris ini:
                tanpa itu "AAPL +$4,98 mld" terbaca seolah posisinya berubah
                besar, padahal cuma +0,5% dari basis yang memang raksasa. */}
            <span style={{ display: 'block', fontSize: 11.5, color: 'var(--dim)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {metric === 'value' ? fmtPct(f.net_share_change_pct) : fmtMoney(f.net_value_usd)}
              {' · '}{f.increased_count} nambah · {f.reduced_count} kurangi
              {f.new_position_count > 0 && ` · ${f.new_position_count} baru`}
            </span>
            {/* Periode + jendela masuk. Jendela adalah RENTANG satu kuartal,
                bukan tanggal — 13F tidak memuat tanggal transaksi sama sekali. */}
            <span style={{ display: 'block', fontSize: 10.5, color: 'var(--faint)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              <span className="pill neutral" style={{ fontSize: 9.5, padding: '1px 6px', marginRight: 6 }}>
                {quarterLabelSet(f.report_dates)}
              </span>
              {f.new_position_count > 0
                ? `${f.new_position_count} institusi masuk sepanjang ${quarterWindow(f.report_date)}`
                : 'tidak ada pemegang baru di periode ini'}
            </span>
          </span>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 12, fontWeight: 600, textAlign: 'right', color: tone }}>
            {metric === 'value' ? fmtMoney(f.net_value_usd) : fmtPct(f.net_share_change_pct)}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function InstitutionalFlowView({ onSelectTicker }) {
  const { data, error } = useStageData(api.institutionalFlow)
  // Dua metrik menjawab pertanyaan berbeda dan tidak saling menggantikan:
  // "% lembar" menemukan akumulasi yang benar-benar mengubah posisi (basisnya
  // kecil), "nilai" menemukan ke mana dolarnya paling banyak pergi (basisnya
  // raksasa). Meranking cuma dengan salah satunya membuat sisi lain tak
  // terlihat sama sekali.
  const [metric, setMetric] = useState('pct')

  if (error) return <div className="empty">Gagal memuat data/institutional_flow.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>
  if (!data.flows) {
    return (
      <div className="empty">
        institutional_flow.json belum dibuat. Jalankan <code>python scripts/build_institutional_flow.py</code>{' '}
        (memakai evidence.json yang sudah ada, tanpa panggilan jaringan) atau tunggu full pipeline run berikutnya.
      </div>
    )
  }

  const flows = data.flows || []
  const sectors = data.sectors || []
  const eligible = flows.filter((f) => f.eligible_for_ranking)
  const rankValue = (f) => (metric === 'value' ? f.net_value_usd : f.net_share_change_pct)
  const ranked = [...eligible].sort((a, b) => rankValue(b) - rankValue(a))

  const nextQ = nextQuarterKey(data.primary_report_date)
  const nextQCount = nextQ ? (data.report_date_presence_counts || {})[nextQ] || 0 : 0
  const deadline = fmtDeadline(nextFilingDeadline(data.primary_report_date))

  const stats = [
    {
      label: 'Net Ditambah',
      value: fmtMoney(data.net_value_usd),
      tone: data.net_value_usd >= 0 ? 'good' : 'bad',
      icon: 'flow',
      sub: `${fmtPct(data.net_ratio_pct)} dari ${fmtMoney(data.gross_value_usd)} yang berpindah`,
    },
    {
      label: 'Saham Punya Arah',
      value: data.tickers_with_basis,
      icon: 'layers',
      sub: `dari ${data.tickers_with_holders} berpemegang · ${data.tickers_new_positions_only} hanya posisi baru`,
    },
    {
      label: 'Layak Diperingkat',
      value: eligible.length,
      icon: 'clipboard',
      sub: '≥5 pemegang ber-basis & nilai ≥ $500jt',
    },
    {
      label: `Laporan ${quarterLabel(nextQ)}`,
      value: nextQCount,
      icon: 'calendar',
      sub: `sudah mulai terbaca · batas lapor ${deadline}`,
    },
  ]

  const columns = [
    { key: 'ticker', label: 'Ticker', render: (r) => <span className="ticker">{r.ticker}</span> },
    { key: 'sector', label: 'Sektor', render: (r) => r.sector || '—' },
    {
      key: 'net_share_change_pct',
      label: 'Net Lembar',
      render: (r) => (
        <span style={{ color: r.net_share_change_pct == null ? 'var(--faint)' : r.net_share_change_pct >= 0 ? 'var(--good)' : 'var(--bad)' }}>
          {r.net_share_change_pct == null ? '—' : fmtPct(r.net_share_change_pct)}
        </span>
      ),
      sortValue: (r) => r.net_share_change_pct,
    },
    {
      key: 'net_value_usd',
      label: 'Net Nilai',
      render: (r) => (
        <span style={{ color: r.net_value_usd >= 0 ? 'var(--good)' : 'var(--bad)' }}>{fmtMoney(r.net_value_usd)}</span>
      ),
      sortValue: (r) => r.net_value_usd,
    },
    {
      key: 'direction',
      label: 'Nambah / Kurangi / Baru',
      render: (r) => `${r.increased_count} / ${r.reduced_count} / ${r.new_position_count}`,
      sortValue: (r) => r.increased_count - r.reduced_count,
    },
    {
      key: 'holders',
      label: 'Pemegang',
      render: (r) => `${r.holders_with_basis} dari ${r.holders_total}`,
      sortValue: (r) => r.holders_with_basis,
    },
    {
      key: 'report_date',
      label: 'Periode',
      // Saham bercampur dua kuartal ditulis eksplisit ("Q1 + Q2 2026"), bukan
      // disembunyikan di balik penanda "+1" yang harus di-hover dulu — 1.090
      // saham berada dalam kondisi itu, terlalu banyak untuk jadi detail.
      render: (r) => {
        const mixed = (r.report_dates || []).length > 1
        return (
          <span title={(r.report_dates || []).map(fmtDate).join(' · ')} style={mixed ? { color: 'var(--warn)' } : undefined}>
            {quarterLabelSet(r.report_dates && r.report_dates.length ? r.report_dates : [r.report_date])}
          </span>
        )
      },
      sortValue: (r) => r.report_date || '',
    },
    {
      key: 'eligible_for_ranking',
      label: 'Peringkat',
      render: (r) => (
        <span className={`pill ${r.eligible_for_ranking ? 'ok' : 'neutral'}`}>
          {r.eligible_for_ranking ? 'layak' : 'sampel tipis'}
        </span>
      ),
      sortValue: (r) => (r.eligible_for_ranking ? 1 : 0),
    },
  ]

  return (
    <>
      <StatCards stats={stats} />
      <SectorFlowChart sectors={sectors} />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '0 0 10px' }}>
        <div style={{ fontSize: 12, color: 'var(--dim)' }}>
          {metric === 'pct'
            ? 'Diranking menurut % lembar — menemukan akumulasi yang benar-benar mengubah posisi. Mega-cap jarang muncul: menambah $5 mld di AAPL cuma +0,5%.'
            : 'Diranking menurut nilai — menemukan ke mana dolarnya paling banyak pergi. Condong ke saham besar karena basis posisinya memang raksasa.'}
        </div>
        <select value={metric} onChange={(e) => setMetric(e.target.value)} style={METRIC_SELECT_STYLE}>
          <option value="pct">Peringkat: % lembar</option>
          <option value="value">Peringkat: nilai (dolar)</option>
        </select>
      </div>
      <div className="chart-row">
        <Leaderboard
          title="Dikumpulkan"
          rows={ranked.slice(0, 10)}
          tone="var(--good)"
          metric={metric}
          onSelectTicker={onSelectTicker}
        />
        <Leaderboard
          title="Dikurangi"
          rows={[...ranked].reverse().slice(0, 10)}
          tone="var(--bad)"
          metric={metric}
          onSelectTicker={onSelectTicker}
        />
      </div>
      <div className="chart-card" style={{ marginBottom: 22 }}>
        <div className="chart-title">Yang Tidak Bisa Dibaca dari Halaman Ini</div>
        <div className="perf-note" style={{ marginTop: 0, paddingTop: 0, borderTop: 'none' }}>
          Sumbernya laporan 13F yang diagregasi Yahoo, dan cuma <b>±10 pemegang terbesar</b> tiap saham yang
          tersedia — jadi ini arah pemegang terbesar, bukan net flow seluruh institusi. Pemegang berlabel
          posisi baru dihitung sebagai kejadian, tidak pernah masuk persentase: Yahoo memakai +100% sebagai
          penanda "tidak ada basis pembanding", bukan besaran. Nilai dolar diperkirakan dari harga sekarang,
          bukan harga saat transaksi. Laporan 13F terlambat sampai 45 hari, dan populasi bisa memuat dua
          kuartal sekaligus (kolom Laporan menandai saham yang campur). Terakhir: arah kepemilikan bukan
          penilaian — institusi menambah posisi tidak berarti sahamnya layak dibeli.
        </div>
      </div>
      <DataTable columns={columns} rows={flows} onRowClick={(r) => onSelectTicker(r.ticker)} />
    </>
  )
}
