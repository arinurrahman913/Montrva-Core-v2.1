import { useState } from 'react'
import { api } from '../api'
import { useStageData } from '../useStageData'
import StatCards from '../components/StatCards'
import { fmtMoney, fmtIDR, fmtPct, personalActionClass, prettyAction, MODULE_LABELS } from '../format'

// Halaman Portofolio — satu-satunya permukaan TULIS di seluruh dashboard.
//
// Yang dicatat pengguna adalah PERISTIWA (beli/jual); posisi & avg_cost
// dihitung backend dari buku itu (montrva/personal/portfolio.py), tidak
// pernah diketik. holdings.json — yang dibaca pipeline — adalah TURUNANNYA.
//
// Kolom aksi tiga lensa memakai kolom `holding` di ACTION_TABLE, yang sebelum
// halaman ini ada tidak pernah menyala sekali pun: tanpa jalur input, seluruh
// universe selalu `no_holding`. Nilainya datang jadi dari backend (call_set
// dihitung ulang di sana lewat build_personal_call_set yang sama dengan
// pipeline) — TIDAK ada tabel keputusan yang disalin ke JS.

const MODULES = ['multibagger', 'quality_compound', 'speculative']

// Label pendek untuk kepala kartu. Dipotong tangan, bukan `slice(0, 4)` dari
// MODULE_LABELS — pemotongan otomatis menghasilkan "QUAL"/"SPEC" yang terbaca
// seperti singkatan yang gagal, dan akan diam-diam jadi omong kosong begitu
// ada modul bernama lain.
const MODULE_SHORT = {
  multibagger: 'Multibagger',
  quality_compound: 'Quality',
  speculative: 'Spekulatif',
}

const TODAY = new Date().toISOString().slice(0, 10)

const EMPTY_FORM = { ticker: '', side: 'beli', date: TODAY, price: '', amount: '', note: '', sellAll: false }

function num(v) {
  const n = parseFloat(v)
  return Number.isFinite(n) ? n : null
}

// Kembar dengan shares_from_amount() di montrva/personal/portfolio.py, dan
// hanya untuk PRATINJAU — angka yang benar-benar disimpan selalu dihitung
// ulang di Python dari nominal yang dikirim, bukan dari hasil di sini. Kalau
// keduanya suatu saat berbeda, yang salah adalah yang di layar ini, dan
// bedanya akan langsung terlihat begitu transaksinya tersimpan (baris buku
// menampilkan lembar hasil backend).
function sharesFromAmount(amount, price) {
  const qty = amount / price
  const nearest = Math.round(qty)
  if (nearest > 0 && Math.abs(qty - nearest) <= 1e-9 * nearest) return nearest
  return Math.round(qty * 1e8) / 1e8
}

// Lembar ditampilkan tanpa ekor nol: 12 tetap "12", 0,4972 tetap "0,4972".
function fmtShares(q) {
  if (q === null || q === undefined) return '—'
  return Number.isInteger(q) ? String(q) : String(parseFloat(q.toFixed(6)))
}

// Pratinjau dihitung di sini, bukan diminta ke server: pengguna harus bisa
// melihat akibat sebuah transaksi SEBELUM menyimpannya. Aturannya sengaja
// dijaga sederhana (hanya kasus yang bisa dihitung tanpa memutar ulang seluruh
// buku) — kebenaran finalnya tetap milik backend, yang memvalidasi & menurunkan
// ulang posisi dari buku utuh saat disimpan.
function preview(form, position) {
  const price = num(form.price)
  const amount = num(form.amount)
  if (price === null || amount === null || amount <= 0 || price <= 0) return null
  const held = position?.quantity || 0
  // "Jual semua" TIDAK melewati pembagian sama sekali — lihat catatan di
  // tombolnya. Nominal di layar cuma tampilan; jumlahnya persis milik posisi.
  const qty = form.sellAll && position ? held : sharesFromAmount(amount, price)

  if (qty <= 0) {
    return { tone: 'bad', lines: [`${fmtMoney(amount)} pada harga ${fmtMoney(price)} tidak cukup untuk satu pecahan lembar pun.`] }
  }

  if (form.side === 'beli') {
    const newQty = held + qty
    const newCost = (position?.total_cost || 0) + amount
    return {
      tone: 'ok',
      lines: [
        `${fmtMoney(amount)} ÷ ${fmtMoney(price)} = ${fmtShares(qty)} lembar.`,
        `Lembar ${fmtShares(held)} → ${fmtShares(newQty)}, avg cost jadi ${fmtMoney(newCost / newQty)}${
          position?.avg_cost ? ` (dari ${fmtMoney(position.avg_cost)})` : ''
        }.`,
      ],
    }
  }

  if (!position || held <= 0) {
    return { tone: 'bad', lines: [`Belum ada posisi ${form.ticker.toUpperCase() || 'ini'} untuk dijual.`] }
  }
  if (qty > held) {
    return {
      tone: 'bad',
      lines: [
        `${fmtMoney(amount)} ÷ ${fmtMoney(price)} = ${fmtShares(qty)} lembar, melebihi kepemilikan sekarang (${fmtShares(held)}).`,
        `Nilai posisimu di harga itu ${fmtMoney(held * price)} — pakai "Jual semua" kalau memang mau menutupnya.`,
      ],
    }
  }
  const realized = (price - (position.avg_cost || 0)) * qty
  return {
    tone: realized >= 0 ? 'ok' : 'bad',
    lines: [
      `${fmtMoney(amount)} ÷ ${fmtMoney(price)} = ${fmtShares(qty)} lembar, realized ${fmtMoney(realized)} (harga jual − avg cost ${fmtMoney(position.avg_cost)}).`,
      qty === held
        ? 'Posisi tutup — ticker ini keluar dari holdings.json, bukan disimpan dengan jumlah nol.'
        : `Sisa ${fmtShares(held - qty)} lembar, avg cost tidak berubah.`,
    ],
  }
}

function ActionCell({ call }) {
  if (!call) return <span className="pf-faint">—</span>
  return (
    <span className={`pill ${personalActionClass(call.action)}`} title={call.action_rationale}>
      {prettyAction(call.action)}
    </span>
  )
}

// Riwayat satu posisi. Kolom Sisa / Avg Cost / Efek yang membuatnya penjelasan
// dan bukan sekadar daftar transaksi: avg cost adalah satu-satunya angka di
// halaman ini yang tidak bisa diperiksa pengguna dari ingatannya sendiri.
//
// Semua nilai di sini datang JADI dari backend (replay() di portfolio.py) —
// tidak ada avg cost berjalan yang dihitung ulang di JS.
function PositionHistory({ rows, onDelete, busy }) {
  if (!rows?.length) return <div className="pf-hist-empty">Belum ada transaksi tercatat.</div>
  return (
    <div className="pf-hist">
      <table>
        <thead>
          <tr>
            <th>Tanggal</th><th>Aksi</th>
            <th style={{ textAlign: 'right' }}>Nominal</th>
            <th style={{ textAlign: 'right' }}>Harga</th>
            <th style={{ textAlign: 'right' }}>Lembar</th>
            <th style={{ textAlign: 'right' }}>Sisa</th>
            <th style={{ textAlign: 'right' }}>Avg Cost</th>
            <th>Efek</th><th />
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => (
            <tr key={h.id}>
              <td className="pf-dim">{h.date}</td>
              <td><span className={`pill ${h.side === 'beli' ? 'ok' : 'bad'}`}>{h.side}</span></td>
              <td className="pf-num" title={h.amount_derived ? 'Transaksi lama — nominal dihitung dari harga × lembar' : 'Nominal yang diketik'}>
                {fmtMoney(h.amount)}{h.amount_derived && <span className="pf-src">≈</span>}
              </td>
              <td className="pf-num">{fmtMoney(h.price)}</td>
              <td className="pf-num pf-dim">{fmtShares(h.quantity)}</td>
              <td className="pf-num">{fmtShares(h.quantity_after)}</td>
              <td className="pf-num">{h.avg_cost_after === null ? '—' : fmtMoney(h.avg_cost_after)}</td>
              <td className={h.realized === null || h.realized === undefined ? 'pf-dim' : h.realized >= 0 ? 'pf-pos' : 'pf-neg'}>
                {h.effect}
                {h.note && <span className="pf-hist-note"> · {h.note}</span>}
              </td>
              <td><button className="pf-del" onClick={() => onDelete(h.id)} disabled={busy}>hapus</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function PositionCard({ p, open, onToggle, onSelectTicker, onDelete, busy, rate }) {
  const cs = p.call_set
  const mb = cs?.multibagger
  return (
    <div className={`pf-card${p.is_open ? '' : ' closed'}`}>
      <div className="pf-card-head" onClick={onToggle} role="button" tabIndex={0}>
        <span className={`pf-caret${open ? ' open' : ''}`}>›</span>
        <span className="pf-card-ticker">
          {p.pending_next_run && <span className="pf-dot" title="Dicatat sesudah run pipeline terakhir" />}
          <span
            className="ticker"
            onClick={(e) => { e.stopPropagation(); onSelectTicker?.(p.ticker) }}
            title="Buka detail ticker"
          >{p.ticker}</span>
        </span>

        {p.is_open ? (
          <>
            <span className="pf-kv"><i>Lembar</i><b>{fmtShares(p.quantity)}</b></span>
            <span className="pf-kv"><i>Avg cost</i><b>{fmtMoney(p.avg_cost)}</b></span>
            <span className="pf-kv">
              <i>Harga</i>
              <b>{fmtMoney(p.price)}{p.price_source === 'snapshot' && <span className="pf-src" title="Kutipan live gagal — harga dari run pipeline terakhir">snapshot</span>}</b>
            </span>
            <span className="pf-kv">
              <i>Nilai</i>
              <b>{fmtMoney(p.market_value)}</b>
              {fmtIDR(p.market_value, rate) && <u>{fmtIDR(p.market_value, rate)}</u>}
            </span>
            <span className="pf-kv">
              <i>P/L</i>
              <b className={(p.unrealized_pct || 0) >= 0 ? 'pf-pos' : 'pf-neg'}>{fmtPct(p.unrealized_pct)}</b>
            </span>
            <span className="pf-kv"><i>Bobot</i><b>{p.weight_pct === null ? '—' : `${p.weight_pct.toFixed(1)}%`}</b></span>
          </>
        ) : (
          <>
            <span className="pf-kv">
              <i>Realized</i>
              <b className={p.realized_pnl >= 0 ? 'pf-pos' : 'pf-neg'}>{fmtMoney(p.realized_pnl)}</b>
              {fmtIDR(p.realized_pnl, rate) && <u>{fmtIDR(p.realized_pnl, rate)}</u>}
            </span>
            <span className="pf-kv"><i>Fee total</i><b>{fmtMoney(p.total_fee)}</b></span>
            <span className="pf-kv"><i>Ditutup</i><b>{p.last_transaction_date}</b></span>
            <span className="pill neutral">posisi tutup — tidak di holdings.json</span>
          </>
        )}

        <span className="pf-card-actions">
          {p.is_open && (!p.in_universe ? (
            <span className="pill neutral">di luar universe — tidak ada analisis</span>
          ) : (
            <>
              {MODULES.map((m) => (
                <span key={m} className="pf-lens" title={MODULE_LABELS[m] || m}>
                  <i>{MODULE_SHORT[m] || m}</i><ActionCell call={cs?.[m]} />
                </span>
              ))}
              {mb?.horizon_status && mb.horizon_status !== 'tidak_berlaku' && (
                <span className={`pill ${mb.horizon_status === 'horizon_terlewati' ? 'warn' : 'neutral'}`}>
                  {mb.horizon_status === 'horizon_terlewati' ? 'horizon terlewati' : 'dalam horizon'}
                </span>
              )}
            </>
          ))}
        </span>
      </div>
      {open && <PositionHistory rows={p.history} onDelete={onDelete} busy={busy} />}
    </div>
  )
}

export default function PortfolioView({ onSelectTicker }) {
  const [reloadKey, setReloadKey] = useState(0)
  const [form, setForm] = useState(EMPTY_FORM)
  const [errors, setErrors] = useState([])
  const [busy, setBusy] = useState(false)
  const [showNote, setShowNote] = useState(false)
  // Posisi terbuka terbentang default (riwayatnya justru yang dicari saat
  // membuka halaman); posisi tertutup terlipat — arsip, bukan yang dikerjakan.
  const [collapsed, setCollapsed] = useState({})
  const { data, error } = useStageData(api.personalPortfolio, [reloadKey])

  const reload = () => setReloadKey((k) => k + 1)
  // Menyunting field mana pun membatalkan status "jual semua": begitu angkanya
  // diubah tangan, transaksinya bukan lagi penutupan posisi yang eksak, dan
  // mengirim jumlah lembar lama akan menyimpan sesuatu yang berbeda dari yang
  // terbaca di layar.
  const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value, sellAll: false }))

  const submit = async () => {
    setBusy(true)
    setErrors([])
    try {
      await api.personalAddTransaction({
        ticker: form.ticker,
        side: form.side,
        date: form.date,
        price: num(form.price),
        // Nominal yang dikirim, bukan lembar: penurunannya milik Python,
        // supaya angka yang tersimpan cuma punya satu penulis.
        amount: num(form.amount),
        // KECUALI penutupan posisi. Nominal harus dibulatkan ke sen sebelum
        // sampai ke sini, dan pembagian baliknya tidak selalu mengembalikan
        // jumlah yang sama persis — terukur: 12 lembar @ 305,260009765625
        // memberi nominal 3663,12 yang dibagi balik jadi 11,99999996 dan
        // menyisakan sepersepuluh juta lembar menggantung selamanya. Satu-
        // satunya operasi yang WAJIB eksak karena itu tidak lewat pembagian
        // sama sekali: jumlahnya dikirim apa adanya dari posisi.
        quantity: form.sellAll && formPosition ? formPosition.quantity : undefined,
        note: form.note,
      })
      setForm({ ...EMPTY_FORM, date: form.date })
      reload()
    } catch (e) {
      setErrors(e.errors || [String(e)])
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id) => {
    setBusy(true)
    setErrors([])
    try {
      await api.personalDeleteTransaction(id)
      reload()
    } catch (e) {
      setErrors(e.errors || [String(e)])
    } finally {
      setBusy(false)
    }
  }

  if (error) return <div className="empty">Gagal memuat portofolio: {error}</div>
  if (!data) return <div className="loading">Memuat…</div>

  const positions = data.positions || []
  const open = positions.filter((p) => p.is_open)
  const closed = positions.filter((p) => !p.is_open)
  const s = data.summary || {}
  const heaviest = open.reduce((a, b) => ((b.weight_pct || 0) > (a?.weight_pct || 0) ? b : a), null)
  const formPosition = open.find((p) => p.ticker === form.ticker.trim().toUpperCase())
  const pv = preview(form, formPosition)

  // Rupiah CUMA tampilan — kursnya datang dari backend, angka yang tersimpan
  // & dihitung tetap dolar. Kalau kursnya gagal diambil (rate null), baris
  // rupiahnya hilang seluruhnya, bukan diisi kurs tebakan.
  const rate = data.fx?.rate || null
  const withIdr = (usd, extra) => [fmtIDR(usd, rate), extra].filter(Boolean).join(' · ') || null

  const stats = [
    {
      label: 'Modal Ditanam',
      value: fmtMoney(s.total_cost),
      accent: '#818CF8',
      sub: withIdr(s.total_cost, `${s.open_positions || 0} posisi terbuka`),
    },
    { label: 'Nilai Sekarang', value: fmtMoney(s.market_value), accent: '#22D3EE', sub: withIdr(s.market_value) },
    {
      label: 'Belum Direalisasi',
      value: fmtMoney(s.unrealized_pnl),
      tone: (s.unrealized_pnl || 0) >= 0 ? 'good' : 'bad',
      accent: (s.unrealized_pnl || 0) >= 0 ? '#4ADE80' : '#FB7185',
      sub: withIdr(s.unrealized_pnl, fmtPct(s.unrealized_pct)),
    },
    {
      label: 'Sudah Direalisasi',
      value: fmtMoney(s.realized_pnl),
      tone: (s.realized_pnl || 0) >= 0 ? 'good' : 'bad',
      accent: '#e8b84b',
      sub: withIdr(s.realized_pnl, closed.length ? `${closed.length} posisi tertutup` : null),
    },
    {
      label: 'Bobot Terbesar',
      value: heaviest ? fmtPct(heaviest.weight_pct, 1).replace('+', '') : '—',
      tone: (heaviest?.weight_pct || 0) > 25 ? 'warn' : undefined,
      accent: '#FBBF7A',
      sub: heaviest ? heaviest.ticker : null,
    },
  ]

  return (
    <>
      <StatCards stats={stats} />

      {(data.pending_next_run || []).length > 0 && (
        <div className="pf-banner">
          <b>{data.pending_next_run.length} posisi belum masuk run pipeline terakhir</b> ({data.pending_next_run.join(', ')}).
          <span>
            {' '}Kolom aksi di bawah dihitung ulang sekarang juga dari hasil reasoning run terakhir lewat fungsi pipeline yang sama,
            jadi isinya benar — tapi halaman Sintesis masih menampilkan versi <code>no_holding</code> sampai run berikutnya.
          </span>
        </div>
      )}

      {(s.positions_without_price || []).length > 0 && (
        <div className="pf-banner neutral">
          <b>Tanpa harga: {s.positions_without_price.join(', ')}</b>
          <span> — dikeluarkan dari nilai pasar & bobot, bukan dihitung nol. Modalnya tetap masuk Modal Ditanam.</span>
        </div>
      )}

      <div className="pf-sec">
        <div className="pf-sec-title">Posisi Terbuka</div>
        <div className="pf-sec-note">Tiap posisi membawa riwayatnya sendiri — dari mana avg cost-nya berasal</div>
      </div>
      {open.length === 0 ? (
        <div className="table-wrap"><div className="empty">Belum ada posisi. Catat transaksi pertama di bawah.</div></div>
      ) : (
        open.map((p) => (
          <PositionCard
            key={p.ticker}
            p={p}
            open={collapsed[p.ticker] !== true}
            onToggle={() => setCollapsed((c) => ({ ...c, [p.ticker]: c[p.ticker] !== true }))}
            onSelectTicker={onSelectTicker}
            onDelete={remove}
            busy={busy}
            rate={rate}
          />
        ))
      )}

      {closed.length > 0 && (
        <>
          <div className="pf-sec" style={{ marginTop: 22 }}>
            <div className="pf-sec-title">Posisi Tertutup</div>
            <div className="pf-sec-note">Tidak ditulis ke holdings.json — realized-nya tetap dihitung di ringkasan</div>
          </div>
          {closed.map((p) => (
            <PositionCard
              key={p.ticker}
              p={p}
              open={collapsed[p.ticker] === false}
              onToggle={() => setCollapsed((c) => ({ ...c, [p.ticker]: c[p.ticker] !== false ? false : true }))}
              onSelectTicker={onSelectTicker}
              onDelete={remove}
              busy={busy}
            />
          ))}
        </>
      )}

      <div className="pf-sec" style={{ marginTop: 26 }}>
        <div className="pf-sec-title">Catat Transaksi</div>
        <div className="pf-sec-note">Yang disimpan peristiwa — avg cost dihitung, tidak diketik</div>
      </div>
      <div className="pf-form">
        <div className="pf-grid">
          <label className="pf-fld">
            <span>Ticker</span>
            <input value={form.ticker} onChange={set('ticker')} placeholder="AAPL" />
          </label>
          <label className="pf-fld">
            <span>Jenis</span>
            <div className="pf-seg">
              <button
                type="button"
                className={form.side === 'beli' ? 'on-buy' : ''}
                onClick={() => setForm((f) => ({ ...f, side: 'beli', sellAll: false }))}
              >Beli</button>
              <button
                type="button"
                className={form.side === 'jual' ? 'on-sell' : ''}
                onClick={() => setForm((f) => ({ ...f, side: 'jual', sellAll: false }))}
              >Jual</button>
            </div>
          </label>
          <label className="pf-fld">
            <span>Tanggal</span>
            <input type="date" value={form.date} max={TODAY} onChange={set('date')} />
          </label>
          <label className="pf-fld">
            <span>Harga / lembar</span>
            <input value={form.price} onChange={set('price')} inputMode="decimal" placeholder="0.00" />
          </label>
          <label className="pf-fld">
            <span>Nominal ($)</span>
            <input value={form.amount} onChange={set('amount')} inputMode="decimal" placeholder="0.00" />
            {form.side === 'jual' && formPosition && (
              // Menjual pakai nominal tidak bisa menutup posisi dengan tepat:
              // ketik 2.400 padahal posisinya bernilai 2.413 dan sisa 0,06
              // lembar menggantung. Tombol ini menghitung nominalnya dari
              // posisi × harga — dan ikut MENULIS harga yang dipakainya ke
              // field harga, supaya angkanya terlihat, bukan ditebak.
              <button
                type="button"
                className="pf-all"
                onClick={() => {
                  // Harga dibulatkan ke sen dulu: kutipan live datang sebagai
                  // float mentah (305.260009765625), dan menaruhnya apa adanya
                  // di field yang dibaca manusia bikin form terlihat rusak.
                  const raw = num(form.price) ?? formPosition.price
                  if (!raw) return
                  const price = Math.round(raw * 100) / 100
                  setForm((f) => ({
                    ...f,
                    price: String(price),
                    amount: String(parseFloat((formPosition.quantity * price).toFixed(2))),
                    sellAll: true,
                  }))
                }}
              >
                Jual semua ({fmtShares(formPosition.quantity)} lembar)
              </button>
            )}
          </label>
        </div>
        <div className="pf-row2">
          {showNote ? (
            <label className="pf-fld">
              <span>Catatan (opsional)</span>
              <input value={form.note} onChange={set('note')} placeholder="mis. masuk sesudah koreksi pasca-earnings" autoFocus />
            </label>
          ) : (
            <button type="button" className="pf-note-toggle" onClick={() => setShowNote(true)}>+ catatan</button>
          )}
          <button className="pf-submit" onClick={submit} disabled={busy}>
            {busy ? 'Menyimpan…' : 'Catat Transaksi'}
          </button>
        </div>

        {pv && (
          <div className={`pf-preview ${pv.tone}`}>
            <span className="pf-lbl">Pratinjau — dihitung sebelum disimpan</span>
            {pv.lines.map((l) => <div key={l}>{l}</div>)}
          </div>
        )}

        {errors.length > 0 && (
          <div className="pf-preview bad">
            <span className="pf-lbl">Ditolak — tidak ada yang disimpan</span>
            <ul>{errors.map((e) => <li key={e}>{e}</li>)}</ul>
          </div>
        )}
      </div>

      {/* Tabel "Buku Transaksi" global DIHAPUS (15 Agu 2026): isinya sekarang
          hidup di dalam kartu posisi masing-masing, lengkap dengan sisa lembar
          & avg cost berjalan yang tidak pernah bisa ditampilkan tabel campuran
          semua ticker. Tidak ada yang hilang — tiap transaksi milik satu
          ticker, dan tiap ticker muncul sebagai posisi terbuka atau tertutup.
          Tombol hapus ikut pindah ke baris riwayat tempat pengguna melihatnya. */}
      <div className="pf-foot">
        Metode basis biaya {data.cost_basis_method} · buku transaksi adalah sumber kebenaran,
        holdings.json diturunkan darinya setiap kali berubah.
        {/* Kursnya ditulis lengkap dengan jamnya supaya tiap angka rupiah di
            atas bisa diperiksa, bukan diterima begitu saja. */}
        {rate ? (
          <> · Rupiah dihitung dari kurs <b>Rp {Math.round(rate).toLocaleString('id-ID')}</b>/USD
          {data.fx?.fetched_at && ` (${data.fx.fetched_at.slice(11, 16)} UTC)`} — tampilan saja, pencatatan tetap dolar.</>
        ) : (
          <> · Kurs USD/IDR gagal diambil, jadi rupiah tidak ditampilkan sama sekali daripada memakai kurs tebakan.</>
        )}
      </div>
    </>
  )
}
