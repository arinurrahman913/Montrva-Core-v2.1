import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import { personalActionClass, prettyAction, prettyStance, MODULE_LABELS } from '../format'

// Halaman "Aturan Keputusan" — memperlihatkan ACTION_TABLE utuh berdampingan
// dengan berapa kali tiap selnya BENAR-BENAR menyala di run terakhir.
//
// Kenapa dua hal itu harus sebaris: aturan sendirian tidak memberi tahu apa pun
// tentang apakah ia hidup. Dua temuan terbesar di lapisan pribadi keduanya
// berbentuk sama — kolom "high" yang mustahil tercapai saat gerbangnya masih
// confidence.band, dan seluruh blok "holding" yang menganggur sebelum ada
// portofolio. Dua-duanya tidak terlihat dari membaca kode, cuma dari menghitung
// keluaran produksi.
//
// SELURUH isi halaman ini datang dari /api/personal/action-table. Tidak ada satu
// pun aturan yang diketik ulang di berkas ini — termasuk pemetaan skor ke
// tingkat, yang sudah tersalin di tiga tempat dan tidak perlu tempat keempat.

const MODULES = ['multibagger', 'quality_compound', 'speculative']
const TIERS = ['high', 'medium', 'low']
const STATUS_LABEL = { no_holding: 'Belum punya posisi', holding: 'Sedang memegang' }

function Matrix({ module, status, rules, counts }) {
  const stances = Object.keys(rules)
  return (
    <div className="ar-block">
      <div className="ar-block-title">{MODULE_LABELS[module] || module}</div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Stance dari Reasoning</th>
              {TIERS.map((t) => (
                <th key={t}>
                  {t === 'high' ? 'Skor ≥70' : t === 'medium' ? 'Skor 50–69' : 'Skor <50'}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stances.map((st) => (
              <tr key={st} className="expand-row">
                <td className="ar-stance">{prettyStance(st)}</td>
                {TIERS.map((t) => {
                  const action = rules[st][t]
                  const n = counts?.[st]?.[t] || 0
                  return (
                    <td key={t} className={n ? '' : 'ar-dead'}>
                      <span className={`pill ${personalActionClass(action)}`}>{prettyAction(action)}</span>
                      <span className="ar-count">{n ? `${n.toLocaleString('id-ID')} ticker` : 'tidak pernah'}</span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function ActionRulesView() {
  const { data, error } = useStageData(api.personalActionTable)

  if (error) return <div className="empty">Gagal memuat aturan keputusan: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const table = data.action_table || {}
  const counts = data.cell_counts || {}
  const bounds = data.score_tier_bounds || {}

  let total = 0
  let hidup = 0
  for (const status of Object.keys(table)) {
    for (const m of MODULES) {
      for (const st of Object.keys(table[status]?.[m] || {})) {
        for (const t of TIERS) {
          total += 1
          if (counts[status]?.[m]?.[st]?.[t]) hidup += 1
        }
      }
    }
  }

  const stats = [
    { label: 'Sel Aturan', value: total, accent: '#818CF8', sub: '2 status × 3 lensa × stance × 3 tingkat' },
    { label: 'Menyala Run Ini', value: hidup, tone: 'good', accent: '#4ADE80' },
    {
      label: 'Tidak Pernah Dipakai',
      value: total - hidup,
      tone: (total - hidup) > 0 ? 'warn' : undefined,
      accent: '#FBBF7A',
      sub: 'aturannya ada, tapi belum pernah terpenuhi',
    },
    { label: 'Ambang Tingkat', value: `${bounds.high} / ${bounds.medium}`, accent: '#22D3EE', sub: 'high / medium' },
  ]

  return (
    <>
      <StatCards stats={stats} />

      <div className="pf-banner neutral">
        <b>Cara membacanya:</b>
        <span>
          {' '}Sistem tidak pernah menghitung ulang apa pun untuk memilih action. Ia cuma melihat dua hal yang
          sudah dihasilkan Reasoning — <b>stance</b> (baris) dan <b>skor tesis</b> (kolom) — lalu membaca
          jawabannya di tabel ini. Tidak ada mesin skor kedua; kalau kamu tahu stance dan skornya, kamu bisa
          menebak action-nya persis seperti sistem.
        </span>
      </div>

      {Object.keys(table).map((status) => (
        <div key={status} style={{ marginBottom: 26 }}>
          <div className="pf-sec">
            <div className="pf-sec-title">{STATUS_LABEL[status] || status}</div>
            <div className="pf-sec-note">
              {status === 'holding'
                ? 'Dipakai kalau ticker ada di holdings.json — kosakatanya soal menambah/menahan/keluar'
                : 'Dipakai kalau belum punya posisi — kosakatanya soal masuk/memantau/melewati'}
            </div>
          </div>
          {MODULES.map((m) => (
            table[status]?.[m] ? (
              <Matrix key={m} module={m} status={status}
                      rules={table[status][m]} counts={counts[status]?.[m]} />
            ) : null
          ))}
        </div>
      ))}

      <div className="pf-foot">
        Sel bertanda “tidak pernah” bukan berarti salah — ia berarti kombinasi stance & tingkat skor itu belum
        pernah muncul di universe. Yang perlu dicurigai adalah sel yang <b>tidak mungkin</b> menyala:
        begitulah dulu ketahuan bahwa kolom skor tertinggi mustahil tercapai, dan bahwa seluruh blok
        “sedang memegang” menganggur sebelum ada portofolio.
      </div>
    </>
  )
}
