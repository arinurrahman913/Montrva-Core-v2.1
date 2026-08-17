import { fmtCompact, fmtMoney, fmtNum, fmtPct } from '../format'

// Grid "DATA PENDUKUNG LENGKAP" — 9 kartu yang memecah satu KnowledgeProfile
// jadi blok yang bisa dibaca sekilas. Dipindah ke berkasnya sendiri (15 Agu
// 2026) begitu halaman Knowledge ikut memakainya: dua pemakai, satu definisi.
//
// `evidence` OPSIONAL — seluruh pembacaannya pakai optional chaining, jadi
// tanpa Evidence kartunya tetap tampil dengan sel "—" alih-alih pecah. Halaman
// Knowledge memanfaatkan itu: knowledge.json sudah ada di memori, Evidence
// (242 MB) ditarik per-ticker saat barisnya dibuka.

function DetailCard({ title, note, noteTone, children }) {
  return (
    <div style={{ background: 'var(--panel2)', borderRadius: 10, padding: '12px 14px', border: '0.5px solid var(--rule)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 10, fontWeight: 600, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.04em' }}>{title}</span>
        {note && (
          <span style={{ fontSize: 9.5, color: noteTone === 'warn' ? 'var(--bad)' : 'var(--faint)', whiteSpace: 'nowrap' }} title={noteTone === 'warn' ? 'Kuartal terakhir yang tersedia di SEC XBRL sudah lewat 9 bulan — angka di kartu ini bukan kondisi terkini' : 'Kuartal fiskal sumber angka di kartu ini'}>
            {noteTone === 'warn' ? '⚠ ' : ''}per {note}
          </span>
        )}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12.5 }}>{children}</div>
    </div>
  )
}

function DetailRow({ label, value, valueColor }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
      <span style={{ color: 'var(--dim)' }}>{label}</span>
      <span style={{ fontWeight: 600, color: valueColor, textAlign: 'right' }}>{value}</span>
    </div>
  )
}

// Baris ini dulu menampilkan `buy_count_30d` tapi dilabeli "Form 4 filing
// (30d)" — dua hal yang berbeda. REPL tampil "0" padahal ada 11 transaksi
// JUAL, neto -175.204 lembar, dan Risk di layar yang sama menyalakan
// `insider_selling_90d`. Arah transaksi nyata sejak sec_form4.py mem-parse
// XML Form 4 (2026-08-15), jadi tidak ada alasan menampilkan satu sisi saja.
function insiderText(ia) {
  const buy = ia.buy_count_30d
  const sell = ia.sell_count_30d
  if (buy == null && sell == null) return '—'
  if (!buy && !sell) return 'nihil'
  return `${buy || 0} beli · ${sell || 0} jual`
}

// Umur kuartal TERAKHIR yang benar-benar ada di SEC XBRL. Growth &
// Profitability seluruhnya turunan dari deret kuartalan itu, dan sampai
// sekarang kartunya tidak menyebut tanggalnya sama sekali — REPL memajang
// capex "kuartal kini" yang sebenarnya dari Q2 2018, dan tidak ada apa pun di
// layar yang bisa membedakannya dari angka bulan lalu. Dihitung di sini (dari
// quarterly_data yang memang sudah dikirim) supaya tidak perlu menunggu run
// penuh berikutnya untuk mulai jujur soal umur data.
const STALE_AFTER_DAYS = 275 // ~3 kuartal: satu kuartal telat itu biasa, tiga tidak

function quarterAge(evidence) {
  const dates = (evidence?.fundamental?.quarterly_data || [])
    .map((q) => q.fiscal_date)
    .filter(Boolean)
  if (!dates.length) return { label: null, tone: null }
  const latest = dates.reduce((a, b) => (a > b ? a : b))
  const days = (Date.now() - Date.parse(`${latest}T00:00:00Z`)) / 86400000
  return { label: latest, tone: days > STALE_AFTER_DAYS ? 'warn' : null }
}

function KnowledgeDetailCards({ knowledge, evidence }) {
  const fh = knowledge.financial_health || {}
  const rt = fh.revenue_trend || {}
  const bs = fh.balance_sheet || {}
  const ht = knowledge.historical_trend || {}
  const own = knowledge.ownership || {}
  const val = knowledge.valuation || {}
  const pt = val.price_target || {}
  const gov = knowledge.governance || {}
  const cs = knowledge.competitive_structure || {}

  const fundamental = evidence?.fundamental || {}
  const io = evidence?.institutional_ownership || {}
  const topHolder = (io.top_holders || [])[0]
  const ia = evidence?.institutional_activity || {}
  // KUNCI `items`, bukan `news`/`filings` — EvidencePackage.to_dict() di
  // contracts.py menamai ulang kedua list ini saat serialisasi (nama field
  // dataclass cuma hidup di sisi Python). Salah kunci di sini bikin kartu
  // "Berita & Filing Terbaru" menampilkan "Tidak ada data terbaru" untuk
  // SEMUA ticker: 757 dari 800 sampel sebenarnya punya berita, REPL saja
  // punya 36 berita + 57 filing. Bandingkan SecFilingsBlock di
  // TickerModal.jsx yang sejak awal sudah memakai `items`.
  const news = (evidence?.news?.items || []).slice(0, 3)
  const filings = (evidence?.sec_filings?.items || []).slice(0, 3)
  const revEst = (evidence?.analyst_estimates?.revenue_estimates || []).find((r) => r.period === '+1q')
  const qAge = quarterAge(evidence)

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 10 }}>
      <DetailCard title="Business Profile">
        <DetailRow label="Sektor" value={knowledge.sector || '—'} />
        <DetailRow label="Business model" value={cs.business_model || '—'} />
        <DetailRow label="Karyawan" value={cs.employees_count != null ? fmtCompact(cs.employees_count) : '—'} />
        {/* "—" di sel lain berarti "ticker ini tidak punya angkanya". TAM
            berbeda: TIDAK ADA sumber data untuk itu di seluruh pipeline
            (Yahoo/Finnhub/SEC tak satu pun menerbitkan TAM), jadi 0 dari
            4.273 ticker akan pernah terisi. Dibedakan supaya tidak dibaca
            sebagai data yang kebetulan hilang. */}
        <DetailRow
          label="TAM estimate"
          value={cs.tam_estimate != null ? fmtMoney(cs.tam_estimate) : <span style={{ color: 'var(--faint)', fontWeight: 400, fontSize: 11 }} title="Tidak ada penyedia data di pipeline ini yang menerbitkan TAM — bukan data yang hilang untuk ticker ini saja">tanpa sumber</span>}
        />
      </DetailCard>

      <DetailCard title="Growth" note={qAge.label} noteTone={qAge.tone}>
        <DetailRow label="Revenue YoY (kini)" value={fmtPct(rt.yoy_q4)} />
        <DetailRow label="CAGR 3Y" value={fmtPct(rt.cagr_3y)} />
        <DetailRow label="CAGR 5Y" value={fmtPct(rt.cagr_5y)} />
      </DetailCard>

      <DetailCard title="Profitability" note={qAge.label} noteTone={qAge.tone}>
        {/* Bank tidak melaporkan "revenue"; penyebut margin di sini adalah
            pendapatan bunga bersih + non-bunga. Angkanya sah, tapi TIDAK
            sebanding dengan margin perusahaan non-keuangan — dikatakan
            eksplisit karena tidak ada apa pun di angkanya yang menunjukkan
            itu. */}
        {String(evidence?.fundamental?.revenue_basis || '').startsWith('financial') && (
          <div style={{ fontSize: 10, color: 'var(--faint)', marginBottom: 2 }} title="Penyebut margin = pendapatan bunga bersih + pendapatan non-bunga, basis pelaporan lembaga keuangan">
            basis pendapatan bank — tidak sebanding lintas sektor
          </div>
        )}
        <DetailRow label="Net margin (kini)" value={fmtPct(fh.net_margin_trend?.q4)} />
        <DetailRow label="Gross margin (kini)" value={fmtPct(fh.gross_margin_trend?.q4)} />
        <DetailRow label="ROE · ROA" value={`${fmtPct(fh.roe != null ? fh.roe * 100 : null)} · ${fmtPct(fh.roa != null ? fh.roa * 100 : null)}`} />
      </DetailCard>

      <DetailCard title="Balance Sheet & Liquidity">
        <DetailRow label="Debt/Equity" value={bs.debt_to_equity != null ? `${fmtNum(bs.debt_to_equity, 2)}x` : '—'} />
        <DetailRow label="Current · Quick ratio" value={`${fmtNum(bs.current_ratio)} · ${fmtNum(bs.quick_ratio)}`} />
        <DetailRow label="Cash & equiv." value={bs.cash_and_equivalents != null ? fmtMoney(bs.cash_and_equivalents) : '—'} />
      </DetailCard>

      <DetailCard title="Valuation">
        <DetailRow label="P/E · P/S · P/B" value={`${fmtNum(val.pe_ratio_trailing)}x · ${fmtNum(val.ps_ratio)}x · ${fmtNum(val.pb_ratio)}x`} />
        <DetailRow
          label={`Target (${pt.num_analysts ?? '—'} analis)`}
          value={pt.target_mean ? `${fmtMoney(pt.target_mean)} (${fmtPct(pt.upside_pct)})` : '—'}
          valueColor="var(--accent)"
        />
        {revEst && <DetailRow label="Revenue est. Q depan" value={fmtPct(revEst.growth)} />}
      </DetailCard>

      <DetailCard title="Performa Historis">
        <DetailRow label="Return 1Y" value={fmtPct(ht.return_1y)} valueColor={ht.return_1y >= 0 ? 'var(--good)' : 'var(--bad)'} />
        <DetailRow label="Volatilitas harian" value={fmtPct(ht.volatility_daily)} />
        <DetailRow label="Beta" value={fmtNum(ht.beta)} />
      </DetailCard>

      <DetailCard title="Kepemilikan">
        <DetailRow label="Institutional own." value={fmtPct(own.institutional_pct != null ? own.institutional_pct * 100 : null)} />
        {topHolder && <DetailRow label="Top holder" value={`${topHolder.holder} ${fmtPct(topHolder.pct_held)}`} />}
        <DetailRow
          label="Insider Form 4 (30h)"
          value={insiderText(ia)}
          valueColor={ia.net_shares_30d < 0 ? 'var(--bad)' : ia.net_shares_30d > 0 ? 'var(--good)' : undefined}
        />
      </DetailCard>

      <DetailCard title="Governance">
        <DetailRow label="Saham beredar (12bln)" value={fmtPct(gov.shares_outstanding_change_12m)} />
        <DetailRow label="Auditor · Restatement" value={`${(gov.auditor_changes || []).length} · ${(gov.restatements || []).length}`} />
      </DetailCard>

      <DetailCard title="Berita & Filing Terbaru">
        {news.length === 0 && filings.length === 0 && <span style={{ color: 'var(--faint)' }}>Tidak ada data terbaru.</span>}
        {news.map((n, i) => (
          <div key={`n${i}`} style={{ fontSize: 11.5 }}>
            {n.headline} <span style={{ color: 'var(--faint)' }}>· {n.published_at?.slice(0, 10)}</span>
          </div>
        ))}
        {filings.map((f, i) => (
          <div key={`f${i}`} style={{ fontSize: 11.5 }}>
            {f.form_type} filed <span style={{ color: 'var(--faint)' }}>· {f.filing_date}</span>
          </div>
        ))}
      </DetailCard>
    </div>
  )
}

export default KnowledgeDetailCards
