import { useMemo } from 'react'
import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import DataTable from '../components/DataTable'
import ThesisProof, { RiskBadge } from '../components/ThesisProof'
import { MODULE_LABELS, outcomeClass, prettyOutcome, prettyAction, isBestAction, rankPersonalPicks } from '../format'

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
      if (call && call.position_status === 'no_holding' && isBestAction(m, call.action)) chosen = e
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

const fmtPrice = (v) => (v == null ? '—' : `$${Number(v).toFixed(2)}`)
const fmtDay = (iso) => {
  if (!iso) return null
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00Z`)
  return Number.isNaN(d.getTime())
    ? String(iso).slice(0, 10)
    : d.toLocaleDateString('id-ID', { day: 'numeric', month: 'short', timeZone: 'UTC' })
}

// Kartu tesis yang SUDAH divonis. Anatominya sengaja sama dengan ThesisProof
// (kartu tesis yang masih hidup): tiga kolom entry/harga sekarang/target,
// supaya dua keadaan itu dibaca dengan cara yang sama. Sebelum 2026-08-03
// bagian ini cuma badge + persen -- angka 9,37% tanpa harga entry, target,
// harga penutup yang dipakai, atau tanggal, jadi tidak bisa diperiksa sendiri.
//
// Semua field harga bisa null untuk outcome yang dievaluasi SEBELUM
// personal_evaluation.py mulai menyimpannya (749 record); kartu tetap tampil,
// blok harganya diganti satu baris keterangan. Sengaja tidak direkonstruksi:
// harga "saat evaluasi" waktu itu tidak sama dengan harga sekarang, dan
// menebaknya berarti mengarang bukti.
// Sebab meleset (backend: personal_evaluation.diagnose_miss). Kosakata
// tertutup, jadi dipetakan eksplisit — bukan mem-format ulang string mentah,
// supaya nilai tak dikenal ketahuan alih-alih tampil sebagai snake_case.
//
// `hint` bukan hiasan: tiap sebab menuntut perbaikan yang BERBEDA, dan itu
// seluruh alasan field ini ada. Target ketinggian menuntut peninjauan
// threshold; arah salah menuntut peninjauan seleksi.
const MISS_REASON = {
  target_ketinggian: {
    label: 'target ketinggian',
    tone: 'warn',
    hint: 'Arah tesisnya benar — harga naik, cuma tidak sampai threshold horizon ini. Yang layak ditinjau: targetnya, bukan pemilihan sahamnya.',
  },
  turun_bareng_pasar: {
    label: 'turun bareng pasar',
    tone: 'warn',
    hint: 'Harga turun, tapi turunnya lebih sedikit dari indeks pada jendela yang sama. Yang layak ditinjau: keputusan masuk pasar saat itu, bukan pilihan sahamnya.',
  },
  arah_salah: {
    label: 'arah salah',
    tone: 'bad',
    hint: 'Harga turun DAN tertinggal dari indeks. Ini kegagalan seleksi — satu-satunya kategori yang benar-benar menuduh tesisnya.',
  },
  harga_justru_naik: {
    label: 'harga justru naik',
    tone: 'bad',
    hint: 'Action keluar mengklaim harga tidak akan naik; harga naik melewati threshold.',
  },
}

// Rekap sebab lintas SEMUA tesis terevaluasi — inilah yang membuat riwayat
// bisa dipakai belajar, bukan cuma dibaca satu-satu. "Hit 29,9%" tidak
// memberi tahu apa yang harus diperbaiki; "89 arah salah vs 28 target
// ketinggian" memberi tahu.
//
// Baris tanggal masuk di bawahnya adalah pagar kejujurannya, dan sengaja
// tidak bisa dilewatkan: selama tanggal masuknya cuma segelintir, angka-angka
// di atas mengukur beberapa hari pasar, bukan mutu metodenya.
function MissBreakdown({ tally, terbukti, entryDates }) {
  const order = ['target_ketinggian', 'turun_bareng_pasar', 'arah_salah', 'harga_justru_naik']
  const rows = order.filter((k) => tally[k]).map((k) => ({ k, n: tally[k], ...MISS_REASON[k] }))
  const totalMiss = rows.reduce((s, r) => s + r.n, 0)
  if (!totalMiss) return null
  const total = totalMiss + terbukti
  const nDates = entryDates.size

  return (
    <div style={{ padding: '12px 14px', marginBottom: 12, background: 'var(--panel)', border: '1px solid var(--rule)', borderRadius: 10 }}>
      <div style={{ fontSize: 11, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--faint)', marginBottom: 8 }}>
        Sebab {totalMiss} tesis meleset
      </div>
      <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 8 }}>
        <div style={{ width: `${(terbukti / total) * 100}%`, background: 'var(--good)' }} title={`${terbukti} terbukti`} />
        {rows.map((r) => (
          <div
            key={r.k}
            style={{ width: `${(r.n / total) * 100}%`, background: r.tone === 'bad' ? 'var(--bad)' : 'var(--warn)', opacity: r.tone === 'bad' ? 1 : 0.55 }}
            title={`${r.n} ${r.label}`}
          />
        ))}
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {rows.map((r) => (
          <div key={r.k} style={{ minWidth: 150 }} title={r.hint}>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 16, color: r.tone === 'bad' ? 'var(--bad)' : 'var(--warn)' }}>
              {r.n}
              <span style={{ fontSize: 10, color: 'var(--faint)', marginLeft: 5 }}>
                {((r.n / totalMiss) * 100).toFixed(0)}% dari yang meleset
              </span>
            </div>
            <div style={{ fontSize: 11 }}>{r.label}</div>
            <div style={{ fontSize: 9.5, color: 'var(--faint)', lineHeight: 1.45, marginTop: 2 }}>{r.hint}</div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid var(--rule)', fontSize: 10, color: 'var(--faint)', lineHeight: 1.55 }}>
        {total} tesis ini lahir di <strong style={{ color: nDates < 5 ? 'var(--warn)' : 'inherit' }}>{nDates} tanggal masuk</strong> yang berbeda
        {nDates < 5 && (
          <> — jadi ini bukan {total} taruhan independen, melainkan {nDates} taruhan yang disebar ke banyak ticker.
          Satu jendela pasar yang buruk membuat semuanya meleset bersamaan. Persentase di atas belum layak dipakai
          menyetel threshold atau gerbang skor; yang menambah bukti di sini bukan menunggu, tapi menyebar tanggal masuk.</>
        )}
      </div>
    </div>
  )
}

function MissReason({ reason }) {
  if (!reason) return null
  const r = MISS_REASON[reason]
  if (!r) return <span className="pill" style={{ fontSize: 10 }}>{reason}</span>
  return (
    <span className={`pill ${r.tone}`} style={{ fontSize: 10 }} title={r.hint}>
      {r.label}
    </span>
  )
}

// Pembanding indeks. Sebelum 2026-08-04 blok ini cuma satu baris kecil
// "+x% vs S&P 500" yang praktis TIDAK PERNAH tampil: excess_return_pct null
// di 167 dari 167 tesis berarah, karena benchmark_at_call tidak pernah terisi
// dan evaluate_due_entries() dipanggil tanpa harga indeks sama sekali.
// Akibatnya vonis "MELESET -8,54%" berdiri telanjang — tidak ada cara
// membedakan tesis yang salah dari pasar yang turun, padahal itu satu-satunya
// pertanyaan yang bikin riwayat ini berguna sebagai pelajaran.
//
// Sekarang: kalimatnya menyebut arah (unggul/tertinggal) lebih dulu, angka
// mentah indeksnya ikut ditampilkan supaya bisa diperiksa sendiri (anatomi
// yang sama dengan entry/tutup/target di atas), dan sumber pembandingnya
// diungkap kalau bukan dari harga yang tercatat saat call dibuat.
function BenchmarkRow({ outcome }) {
  const ex = outcome.excess_return_pct
  const br = outcome.benchmark_return_pct
  if (ex == null && br == null) return null

  const ahead = ex != null && ex >= 0
  const levels = []
  if (outcome.benchmark_at_entry != null) levels.push(Number(outcome.benchmark_at_entry).toFixed(0))
  if (outcome.benchmark_at_exit != null) levels.push(Number(outcome.benchmark_at_exit).toFixed(0))

  return (
    <div style={{ marginTop: 8, padding: '7px 10px', background: 'var(--panel)', borderRadius: 6 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 9, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--faint)' }}>
          S&amp;P 500 jendela sama
        </span>
        {br != null && (
          <span style={{ fontFamily: 'var(--mono)', fontSize: 12 }}>{br >= 0 ? '+' : ''}{br}%</span>
        )}
        {levels.length === 2 && (
          <span style={{ fontSize: 9.5, color: 'var(--faint)', fontFamily: 'var(--mono)' }}>
            {levels[0]} → {levels[1]}
          </span>
        )}
      </div>
      {ex != null && (
        <div
          style={{ marginTop: 2, fontSize: 11.5, color: ahead ? 'var(--good)' : 'var(--bad)' }}
          title="Return tesis dikurangi return S&P 500 pada jendela waktu yang sama — naik 3% saat indeks naik 8% itu tertinggal, bukan berhasil"
        >
          {ahead ? 'unggul' : 'tertinggal'} {Math.abs(ex)}% dari indeks
        </div>
      )}
      {/* Cadangan terakhir di resolve_benchmark_pair: harga indeks HARI INI,
          bukan pada exit_date. Dipakai daripada tidak ada pembanding sama
          sekali, tapi tidak boleh terlihat setara dengan yang tanggalnya cocok. */}
      {String(outcome.benchmark_source || '').includes('harga_hari_ini') && (
        <div style={{ marginTop: 3, fontSize: 9, color: 'var(--faint)' }} title="Penutupan indeks pada exit_date tidak ada di deret, jadi dipakai harga indeks saat evaluasi berjalan — jendelanya sedikit lebih panjang dari jendela tesis">
          ⓘ indeks sisi keluar dibaca saat evaluasi, bukan pada tanggal tutup
        </div>
      )}
      {outcome.benchmark_backfilled && (
        <div style={{ marginTop: 3, fontSize: 9, color: 'var(--faint)' }} title="Evaluasi ini berjalan tanpa pembanding indeks; angkanya dipulihkan dari penutupan ^GSPC pada entry_date & exit_date yang tersimpan — fakta publik yang tetap, bukan rekonstruksi dari keadaan sekarang">
          ⓘ pembanding indeks dipulihkan setelah evaluasi
        </div>
      )}
    </div>
  )
}

function OutcomeCard({ outcome }) {
  const hasPrices = outcome.entry_price != null || outcome.exit_price != null
  const ret = outcome.return_pct
  const tone = outcome.classification === 'terbukti' ? 'var(--good)' : outcome.classification === 'meleset' ? 'var(--bad)' : 'var(--dim)'
  const meta = []
  if (outcome.due_at) meta.push(`jatuh tempo ${fmtDay(outcome.due_at)}`)
  if (outcome.horizon_days != null) meta.push(`horizon ${outcome.horizon_days} hari`)
  if (outcome.evaluated_at) meta.push(`dievaluasi ${fmtDay(outcome.evaluated_at)}`)

  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span className={`pill ${outcomeClass(outcome.classification)}`}>{prettyOutcome(outcome.classification)}</span>
        {ret != null && (
          <span style={{ fontSize: 12, fontFamily: 'var(--mono)', color: tone }}>{ret >= 0 ? '+' : ''}{ret}%</span>
        )}
        {outcome.threshold_pct != null && (
          <span style={{ fontSize: 10, color: 'var(--faint)' }}>target +{outcome.threshold_pct}%</span>
        )}
        <MissReason reason={outcome.miss_reason} />
      </div>

      {hasPrices ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 8, marginTop: 8, padding: 10, background: 'var(--panel)', borderRadius: 6 }}>
          <div>
            <div style={{ fontSize: 9, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--faint)' }}>entry</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 13 }}>{fmtPrice(outcome.entry_price)}</div>
            {outcome.entry_date && <div style={{ fontSize: 9.5, color: 'var(--faint)' }}>{fmtDay(outcome.entry_date)}</div>}
          </div>
          <div>
            <div style={{ fontSize: 9, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--faint)' }}>tutup dipakai</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 13, color: tone }}>{fmtPrice(outcome.exit_price)}</div>
            {/* Tanggalnya sering BUKAN due_at (mis. jatuh tempo Minggu -> yang
                dipakai tutupan Jumat) -- ditulis apa adanya, tidak disamakan. */}
            {outcome.exit_date && <div style={{ fontSize: 9.5, color: 'var(--faint)' }}>{fmtDay(outcome.exit_date)}</div>}
          </div>
          <div>
            <div style={{ fontSize: 9, letterSpacing: '.04em', textTransform: 'uppercase', color: 'var(--faint)' }}>target</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 13 }}>{fmtPrice(outcome.target_price)}</div>
            {outcome.threshold_pct != null && outcome.target_price != null && (
              <div style={{ fontSize: 9.5, color: 'var(--faint)' }}>+{outcome.threshold_pct}%</div>
            )}
          </div>
        </div>
      ) : (
        <div style={{ marginTop: 6, fontSize: 10, color: 'var(--faint)' }}>
          Rincian harga tidak tersimpan untuk evaluasi ini (dijalankan sebelum field harga ada).
        </div>
      )}

      <BenchmarkRow outcome={outcome} />

      {meta.length > 0 && (
        <div style={{ marginTop: 6, fontSize: 9.5, color: 'var(--faint)', lineHeight: 1.6 }}>{meta.join(' · ')}</div>
      )}
      {outcome.evaluation_lag_days > 0 && (
        <div style={{ fontSize: 9.5, color: 'var(--faint)' }}>
          harga diambil {outcome.evaluation_lag_days} hari setelah jatuh tempo (run berikutnya)
        </div>
      )}
      {outcome.baseline === 'price_history' && (
        <div style={{ marginTop: 3, fontSize: 9, color: 'var(--faint)' }} title="price_at_call tidak tersimpan untuk entry ini (dibuat sebelum field ini ada) -- direkonstruksi dari price_history">
          ⓘ harga entry direkonstruksi (bukan harga asli saat call)
        </div>
      )}
      {/* Angka hasil scripts/backfill_outcome_prices.py: turunan eksak dari
          return_pct yang sudah tersimpan, tapi dipulihkan SETELAH evaluasi --
          bukan dicatat saat vonis dibuat. Bedanya kecil tapi nyata (exit_date
          ditemukan dengan mencocokkan harga, bisa kosong), jadi disebutkan. */}
      {outcome.prices_backfilled && (
        <div style={{ marginTop: 3, fontSize: 9, color: 'var(--faint)' }} title="Evaluasi ini berjalan sebelum harga ikut disimpan; angkanya dipulihkan dari return_pct + harga entry, bukan dicatat saat evaluasi">
          ⓘ rincian harga dipulihkan setelah evaluasi
        </div>
      )}
      {/* Pemulihan pertama menaruh tanggal keluar yang mustahil di 450 outcome
          (pencocokan harga tanpa batas bawah menunjuk hari berbulan-bulan
          sebelum tesisnya mulai). scripts/repair_exit_dates.py menghitungnya
          ulang dengan aturan evaluator sendiri. Disebut terpisah dari catatan
          harga di atas karena yang dipulihkan di sini TANGGAL -- dan tanggal
          itulah yang menentukan hari pembacaan indeks & panjang jendela
          vonis-z, bukan cuma keterangan di kartu. */}
      {outcome.exit_date_repaired && (
        <div style={{ marginTop: 3, fontSize: 9, color: 'var(--faint)' }} title="Tanggal keluar yang tersimpan sebelumnya mendahului tanggal masuk — dihitung ulang sebagai bar terakhir yang sudah tutup saat evaluasi berjalan, aturan yang sama dengan yang dipakai evaluator">
          ⓘ tanggal keluar diperbaiki (sebelumnya mendahului tanggal masuk)
        </div>
      )}
    </div>
  )
}

function ExpandedTimeline({ ticker, timeline, calibration }) {
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
                <OutcomeCard outcome={moduleOutcome} />
              ) : (
                <ThesisProof ticker={ticker} module={module} action={call.action} horizon={call.horizon} horizonAnchor={call.horizon_anchor} calibration={calibration} thesisScore={call.thesis_score} />
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
//
// MASUKANNYA sekarang proyeksi tipis dari /api/personal/history/candidates
// (~2,4 MB: ticker, tanggal, lensa, + field ranking), bukan seluruh
// personal_history.json (160 MB). Yang dipindah ke backend cuma penyaringan
// "call ini berhak ikut perebutan" -- PERANKINGANNYA tetap di sini, memakai
// rankPersonalPicks yang sama dengan Agregator, karena aturan pemecah seri
// itulah yang dulu bikin dua halaman menyebut top-3 berbeda untuk hari yang
// sama. Menyalinnya ke Python akan menghidupkan lagi risiko itu.
const TOP_N = 3

function buildTopThreeIndex(candidates) {
  const byDayModule = new Map() // "YYYY-MM-DD|module" -> [{ticker, call}]
  for (const c of candidates || []) {
    if (!c.day || !c.module) continue
    const key = `${c.day}|${c.module}`
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
    // `c` sendiri sudah membawa field ranking di tingkat atas, dan
    // comparePersonalPicks membaca `x.call || x` -- jadi dioper apa adanya.
    byDayModule.get(key).push(c)
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
//
// Dulu ini memeriksa satu timeline lawan indeks; sekarang mengembalikan
// himpunannya langsung, karena himpunan inilah yang menentukan timeline mana
// yang perlu diambil dari server sama sekali.
function everTopThreeTickers(topSetByDayModule) {
  const out = new Set()
  for (const set of topSetByDayModule.values()) for (const t of set) out.add(t)
  return [...out].sort()
}

export default function PersonalHistoricalView({ onSelectTicker }) {
  // Dua langkah, sengaja: siapa yang pernah top-3 itu pertanyaan lintas SELURUH
  // populasi (4.186 ticker), tapi yang ditampilkan halaman ini cuma ~35 timeline.
  // Dulu keduanya dijawab dengan satu unduhan 160 MB dan halaman butuh ~1 menit
  // sebelum menampilkan apa pun. Sekarang: proyeksi kandidat ~2,4 MB untuk
  // menjawab pertanyaan pertama, lalu ~0,9 MB timeline untuk yang benar-benar
  // dipakai.
  const { data: candData, error } = useStageData(api.personalHistoryCandidates)
  const trackedList = useMemo(
    () => (candData ? everTopThreeTickers(buildTopThreeIndex(candData.candidates)) : []),
    [candData],
  )
  // Kunci string, bukan array: array baru tiap render akan membuat useStageData
  // memuat ulang tanpa henti.
  const trackedKey = trackedList.join(',')
  const { data: tlData } = useStageData(
    () => (trackedKey ? api.personalHistoryTickers(trackedList) : Promise.resolve({ timelines: {} })),
    [trackedKey],
  )
  const { data: dueData } = useStageData(api.personalDueForReview)
  // ~10 KB — dipakai BaseRateNote di tiap kartu tesis hidup. Diambil sekali di
  // sini lalu dioper turun; satu halaman bisa merender puluhan kartu, jadi
  // fetch di dalam kartu akan jadi puluhan request untuk data yang sama.
  const { data: calibration } = useStageData(api.personalCalibration)

  if (error) return <div className="empty">Gagal memuat data/personal/personal_history.json: {error}</div>
  if (!candData || !tlData) return <div className="loading">Memuat…</div>

  const timelines = trackedList.map((t) => tlData.timelines?.[t]).filter(Boolean)
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
  const missTally = {}
  // Tanggal masuk yang BERBEDA di antara tesis terevaluasi. Angka ini jauh
  // lebih penting daripada kelihatannya: 167 tesis berarah di riwayat live
  // ternyata cuma lahir di 2 tanggal (27 & 29 Jul), jadi itu bukan 167
  // taruhan independen melainkan 2 taruhan yang disebar ke ~85 ticker. Tanpa
  // menampilkannya, "hit 29,9% dari 167 tesis" terbaca jauh lebih meyakinkan
  // daripada bukti yang sebenarnya ada.
  const entryDates = new Set()
  for (const t of timelines) {
    for (const e of t.entries || []) {
      if (!e.outcome) continue
      for (const m of MODULES) {
        const o = e.outcome[m]
        if (!o) continue
        // HARUS diawali ticker. `thesis_key` yang ditulis backend berbentuk
        // "{module}:{tanggal mulai streak}" (personal_evaluation.py) — unik di
        // dalam SATU timeline ticker, tapi TIDAK unik lintas ticker. Sebelum
        // 2026-08-04 kunci di sini memakai thesis_key mentah, jadi seluruh
        // tesis Spekulatif yang mulai 27 Jul di 4.166 ticker runtuh jadi SATU
        // kunci: dedupe menyisakan 2 tesis, dan kartu "Track Record" + "vs
        // S&P 500" di atas dihitung dari 2 sampel, bukan 167. Kebetulan
        // ketahuan karena rekap sebab meleset di bawah selalu kosong.
        const key = `${t.ticker}:${o.thesis_key || `${m}:${e.analyzed_at}`}`
        if (seenThesisKeys.has(key)) continue
        seenThesisKeys.add(key)
        if (o.classification === 'terbukti') terbukti++
        else if (o.classification === 'meleset') meleset++
        else if (o.classification === 'ambigu') ambigu++
        if (o.excess_return_pct != null) { excessSum += o.excess_return_pct; excessCount += 1 }
        if (o.miss_reason) missTally[o.miss_reason] = (missTally[o.miss_reason] || 0) + 1
        if (o.classification && o.classification !== 'tidak_berlaku' && o.entry_date) entryDates.add(o.entry_date)
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
      <MissBreakdown tally={missTally} terbukti={terbukti} entryDates={entryDates} />
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
        renderExpanded={(r) => <ExpandedTimeline ticker={r.ticker} timeline={r} calibration={calibration} />}
      />
    </>
  )
}
