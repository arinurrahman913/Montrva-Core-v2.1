import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import { MODULE_LABELS, prettyStance } from '../format'

// Rapor kalibrasi. Bacanya dari /api/personal/calibration (~10 KB) dan BUKAN
// dari /api/personal/history (127 MB) — halaman ini harus bisa dibuka dalam
// sekejap, karena gunanya justru dilihat berulang.
//
// Aturan tampilan yang menyetir seluruh berkas ini: irisan yang belum lolos
// gerbang bukti tetap ditampilkan LENGKAP dengan angkanya, tapi persennya
// diredupkan dan diberi alasan. Menyembunyikannya bikin orang mengira belum
// ada apa-apa; menampilkannya seperti irisan yang sudah kuat bikin orang
// menyetel threshold dari dua hari pasar.

const DIM_HELP = {
  modul: 'Lensa mana yang sudah bisa dinilai sama sekali.',
  horizon: 'Threshold per horizon (mingguan 3%, bulanan 5%, dst) — kalau satu horizon selalu meleset tipis, yang salah bisa jadi thresholdnya.',
  action: 'Action mana yang benar-benar menghasilkan, bukan cuma sering muncul.',
  stance: 'Stance dari Reasoning Umum — menghubungkan hasil balik ke sumber keputusannya.',
  'tingkat skor tesis': 'Gerbang ACTION_TABLE memakai skor >=70 sebagai "high". Irisan ini menjawab apakah gerbang itu memisahkan sesuatu: kalau hit rate high tidak lebih baik dari medium, gerbangnya tidak bekerja.',
  'tanggal masuk': 'Sebaran tanggal masuk. Selama daftar ini pendek, semua angka di halaman ini mengukur beberapa hari pasar.',
  'tipe risk flag': 'Apakah flag risiko benar-benar meramal kegagalan. Satu tesis bisa punya beberapa flag, jadi baris di sini tidak saling lepas dan jumlahnya boleh melebihi total tesis — "(tanpa flag)" ada sebagai pembanding.',
}

const pct = (v) => (v == null ? '—' : `${v.toFixed(1)}%`)
const signed = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`)

function GateBadge({ row }) {
  if (row.evaluable) return <span className="pill ok" style={{ fontSize: 9.5 }}>bukti cukup</span>
  return (
    <span className="pill" style={{ fontSize: 9.5, color: 'var(--faint)' }} title={row.limiters.join(' · ')}>
      belum cukup bukti
    </span>
  )
}

function SliceTable({ dimension, rows }) {
  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{ fontSize: 12, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--dim)', marginBottom: 2 }}>
        {dimension}
      </div>
      <div style={{ fontSize: 10.5, color: 'var(--faint)', marginBottom: 8, lineHeight: 1.5, maxWidth: 780 }}>
        {DIM_HELP[dimension]}
      </div>
      <div className="table-wrap" style={{ overflowX: 'auto' }}>
        <table style={{ minWidth: 720 }}>
          <thead>
            <tr>
              <th>Nilai</th>
              <th style={{ textAlign: 'right' }}>Tesis</th>
              <th style={{ textAlign: 'right' }}>Terbukti</th>
              <th style={{ textAlign: 'right' }}>Meleset</th>
              <th style={{ textAlign: 'right' }}>Hit</th>
              <th style={{ textAlign: 'right' }}>Median excess</th>
              <th style={{ textAlign: 'right' }}>Tgl masuk</th>
              <th>Bukti</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td style={{ fontFamily: 'var(--mono)', fontSize: 11.5 }}>
                  {MODULE_LABELS[r.key] || prettyStance(r.key)}
                </td>
                <td style={{ textAlign: 'right', fontFamily: 'var(--mono)' }}>{r.n}</td>
                <td style={{ textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--good)' }}>{r.terbukti}</td>
                <td style={{ textAlign: 'right', fontFamily: 'var(--mono)', color: 'var(--bad)' }}>{r.meleset}</td>
                {/* Persen diredupkan kalau gerbangnya belum lolos — angkanya
                    tetap terbaca, tapi tidak boleh terlihat berwibawa. */}
                <td style={{ textAlign: 'right', fontFamily: 'var(--mono)', opacity: r.evaluable ? 1 : 0.45 }}>
                  {pct(r.hit_rate_pct)}
                </td>
                <td style={{ textAlign: 'right', fontFamily: 'var(--mono)', opacity: r.evaluable ? 1 : 0.45, color: r.median_excess_pct == null ? 'inherit' : r.median_excess_pct >= 0 ? 'var(--good)' : 'var(--bad)' }}>
                  {signed(r.median_excess_pct)}
                </td>
                <td style={{ textAlign: 'right', fontFamily: 'var(--mono)' }} title={r.entry_date_list.join(', ')}>
                  {r.entry_dates}
                </td>
                <td><GateBadge row={r} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// Blok yang paling gampang dilupakan dan paling penting ada: dua dari tiga
// lensa belum punya SATU pun vonis, dan bukan karena belum sempat —
// horizonnya 730 & 1825 hari. Rapor tanpa blok ini terbaca seolah sistem
// sudah terkalibrasi, padahal yang terukur baru sepertiganya.
function NotYetEvaluable({ items }) {
  return (
    <div style={{ padding: '12px 14px', marginBottom: 18, background: 'var(--panel)', border: '1px solid var(--rule)', borderRadius: 10 }}>
      <div style={{ fontSize: 12, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--dim)', marginBottom: 8 }}>
        Yang belum bisa dinilai
      </div>
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        {items.map((n) => (
          <div key={n.module} style={{ minWidth: 210 }}>
            <div style={{ fontSize: 11.5, marginBottom: 3 }}>{MODULE_LABELS[n.module] || n.module}</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 18, color: n.evaluated ? 'var(--text)' : 'var(--faint)' }}>
              {n.evaluated}
              <span style={{ fontSize: 10, color: 'var(--faint)', marginLeft: 6 }}>tesis divonis</span>
            </div>
            <div style={{ fontSize: 10, color: 'var(--faint)', marginTop: 3, lineHeight: 1.5 }}>
              {n.active_calls.toLocaleString('id-ID')} call berjalan
              {n.earliest_possible_verdict && (
                <>
                  <br />vonis paling cepat <strong>{n.earliest_possible_verdict}</strong>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// Batas antara "menandai" dan "mengubah", plus daftar yang sengaja TIDAK
// dikerjakan. Ditaruh di halaman ini, bukan di dokumen terpisah, karena di
// sinilah godaannya muncul: begitu satu irisan kelihatan meyakinkan, dorongan
// untuk langsung menyetel threshold datang di layar yang sama.
//
// Syaratnya dihitung backend (personal_calibration._mechanical_tuning), jadi
// yang tampil di sini status yang benar-benar diperiksa, bukan salinan janji.
const DELIBERATELY_NOT_DONE = [
  ['Tidak ada mesin skor kedua', 'action tetap murni dari ACTION_TABLE (stance + tingkat skor tesis). Halaman ini tidak pernah jadi input ke sana.'],
  ['Tidak ada auto-tuning threshold', 'HORIZON_OUTCOME_THRESHOLD_PCT dan gerbang _thesis_score_tier tidak disetel dari hasil, sampai tiga syarat di atas terpenuhi.'],
  ['Tidak ada pembobotan ulang risk flag', 'high_debt terlihat 16% vs 32% tanpa flag, tapi dari 1 tanggal masuk — itu belum bukti, baru petunjuk.'],
  ['Tidak ada angka yang tidak bisa ditelusuri', 'setiap persen di halaman ini bisa dilacak balik ke outcome tersimpan; tidak ada model yang "belajar" di luar itu.'],
]

function MechanicalTuning({ mt }) {
  return (
    <div style={{ padding: '12px 14px', margin: '18px 0 12px', background: 'var(--panel)', border: '1px solid var(--rule)', borderRadius: 10 }}>
      <div style={{ fontSize: 12, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--dim)', marginBottom: 8 }}>
        Kapan angka ini boleh mengubah sistem
      </div>
      <div style={{ fontSize: 12, marginBottom: 10 }}>
        Status sekarang:{' '}
        <strong style={{ color: mt.allowed ? 'var(--good)' : 'var(--warn)' }}>
          {mt.allowed ? 'ketiga syarat terpenuhi' : 'belum boleh — masih menandai saja'}
        </strong>
      </div>
      <div style={{ display: 'grid', gap: 6, marginBottom: 12 }}>
        {mt.conditions.map((c) => (
          <div key={c.label} style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 11 }}>
            <span style={{ color: c.met ? 'var(--good)' : 'var(--faint)', fontFamily: 'var(--mono)' }}>
              {c.met ? '✓' : '○'}
            </span>
            <span style={{ color: c.met ? 'var(--text)' : 'var(--dim)' }}>
              {c.label}
              <span style={{ color: 'var(--faint)' }}> — {c.detail}</span>
            </span>
          </div>
        ))}
      </div>
      <div style={{ paddingTop: 10, borderTop: '1px solid var(--rule)' }}>
        <div style={{ fontSize: 10.5, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--faint)', marginBottom: 6 }}>
          Yang sengaja tidak dikerjakan
        </div>
        <div style={{ display: 'grid', gap: 5 }}>
          {DELIBERATELY_NOT_DONE.map(([title, why]) => (
            <div key={title} style={{ fontSize: 10.5, lineHeight: 1.55 }}>
              <strong style={{ color: 'var(--dim)' }}>{title}</strong>
              <span style={{ color: 'var(--faint)' }}> — {why}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function PersonalCalibrationView() {
  const { data, error } = useStageData(api.personalCalibration)

  if (error) return <div className="empty">Gagal memuat data/personal/calibration.json: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>
  if (!data.overall) {
    return (
      <div className="empty">
        calibration.json belum dibangun. Jalankan <code>python scripts/build_calibration.py</code> atau tunggu run pipeline berikutnya.
      </div>
    )
  }

  const o = data.overall
  const gate = data.evidence_gate
  const stats = [
    { label: 'Tesis Terevaluasi', value: data.theses_total },
    { label: 'Punya Klaim Arah', value: data.theses_directional },
    {
      label: 'Hit Rate',
      value: pct(o.hit_rate_pct),
      tone: o.hit_rate_pct == null ? undefined : o.hit_rate_pct >= 50 ? 'good' : 'bad',
    },
    {
      label: 'Median vs S&P 500',
      value: signed(o.median_excess_pct),
      tone: o.median_excess_pct == null ? undefined : o.median_excess_pct >= 0 ? 'good' : 'bad',
    },
    {
      label: 'Tanggal Masuk Berbeda',
      value: o.entry_dates,
      tone: o.entry_dates < gate.min_entry_dates ? 'warn' : 'good',
    },
  ]

  const dims = []
  for (const s of data.slices) {
    const found = dims.find((d) => d.dimension === s.dimension)
    if (found) found.rows.push(s)
    else dims.push({ dimension: s.dimension, rows: [s] })
  }
  const nEvaluable = data.slices.filter((s) => s.evaluable).length

  return (
    <>
      <StatCards stats={stats} />

      <div
        style={{
          padding: '12px 14px', marginBottom: 18, borderRadius: 10,
          background: 'var(--panel)',
          border: `1px solid ${nEvaluable ? 'var(--rule)' : 'rgba(251,191,122,.35)'}`,
        }}
      >
        <div style={{ fontSize: 13, marginBottom: 6 }}>
          {nEvaluable === 0 ? (
            <><strong style={{ color: 'var(--warn)' }}>Belum ada satu pun irisan yang layak dipakai menyetel sistem.</strong> Angka di
            bawah tetap ditampilkan apa adanya — bukan disembunyikan — tapi jangan dipakai mengubah threshold atau gerbang skor.</>
          ) : (
            <><strong>{nEvaluable} dari {data.slices.length} irisan</strong> sudah lolos gerbang bukti.</>
          )}
        </div>
        <div style={{ fontSize: 11, color: 'var(--dim)', lineHeight: 1.65 }}>
          Gerbangnya dua: minimal <strong>{gate.min_theses} tesis</strong> DAN minimal{' '}
          <strong>{gate.min_entry_dates} tanggal masuk</strong> yang berbeda. Syarat kedua yang biasanya mengikat —{' '}
          {data.theses_directional} tesis di sini lahir cuma di <strong>{o.entry_dates} tanggal</strong>{' '}
          ({o.entry_date_list.join(', ')}), jadi itu {o.entry_dates} taruhan yang disebar ke banyak ticker, bukan{' '}
          {data.theses_directional} sampel independen. Satu jendela pasar yang buruk membuat semuanya meleset bersamaan.
          Yang menambah bukti di sini bukan menunggu, tapi <strong>menyebar tanggal masuk</strong>.
        </div>
        {o.with_excess > 0 && (
          <div style={{ fontSize: 11, color: 'var(--dim)', marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--rule)', lineHeight: 1.65 }}>
            Pembanding indeks: <strong>{o.beat_index} dari {o.with_excess}</strong> tesis unggul dari S&amp;P 500 pada
            jendela masing-masing, median excess <strong>{signed(o.median_excess_pct)}</strong>. Ini yang menjawab "salah
            pilih saham, atau salah ada di pasar itu" — hit rate rendah saat indeks naik berarti yang pertama.
          </div>
        )}
      </div>

      {data.not_yet_evaluable?.length > 0 && <NotYetEvaluable items={data.not_yet_evaluable} />}

      {Object.keys(data.miss_reasons || {}).length > 0 && (
        <div style={{ padding: '12px 14px', marginBottom: 18, background: 'var(--panel)', border: '1px solid var(--rule)', borderRadius: 10 }}>
          <div style={{ fontSize: 12, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--dim)', marginBottom: 8 }}>
            Sebab meleset
          </div>
          <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
            {Object.entries(data.miss_reasons).map(([k, v]) => (
              <div key={k}>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 18 }}>{v}</div>
                <div style={{ fontSize: 11, color: 'var(--dim)' }}>{k.replace(/_/g, ' ')}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {dims.map((d) => (
        <SliceTable key={d.dimension} dimension={d.dimension} rows={d.rows} />
      ))}

      {data.mechanical_tuning && <MechanicalTuning mt={data.mechanical_tuning} />}

      <p className="narrative" style={{ margin: '4px 0 0', color: 'var(--faint)', fontSize: 11, lineHeight: 1.7 }}>
        Halaman ini tidak mengubah keputusan apa pun — tidak ada threshold, gerbang skor, atau bobot yang disetel dari
        angka di sini. Itu keputusan sadar: menyetel sistem dari {data.theses_directional} tesis yang lahir di{' '}
        {o.entry_dates} tanggal berarti menjahitnya ke satu jendela pasar. Dibangun ulang tiap run pipeline
        (personal_calibration.py) atau manual lewat <code>python scripts/build_calibration.py</code>.
        {data.generated_at && <> Terakhir dibangun {data.generated_at.slice(0, 16).replace('T', ' ')} UTC.</>}
      </p>
    </>
  )
}
