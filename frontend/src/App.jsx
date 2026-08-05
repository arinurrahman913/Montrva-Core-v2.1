import { useState, useEffect } from 'react'
import Sidebar from './components/Sidebar'
import TickerModal from './components/TickerModal'
import GenerateButton from './components/GenerateButton'
import ViewErrorBoundary from './components/ViewErrorBoundary'
import Layer1View from './views/Layer1View'
import ScreeningView from './views/ScreeningView'
import EvidenceView from './views/EvidenceView'
import KnowledgeView from './views/KnowledgeView'
import CatalystView from './views/CatalystView'
import InstitutionalFlowView from './views/InstitutionalFlowView'
import PeerView from './views/PeerView'
import ConfidenceView from './views/ConfidenceView'
import RiskView from './views/RiskView'
import ReasoningView from './views/ReasoningView'
import AggregatorView from './views/AggregatorView'
import HistoricalView from './views/HistoricalView'
import PersonalAggregatorView from './views/PersonalAggregatorView'
import PersonalHistoricalView from './views/PersonalHistoricalView'
import PersonalCalibrationView from './views/PersonalCalibrationView'
import { api } from './api'

const TITLES = {
  layer1: ['Layer 1 — Market Context', '13 komponen makro, membaca data/layer1_context.json'],
  screening: ['Screening', 'Filter kandidat dari universe mentah — data/screening.json'],
  evidence: ['Evidence', 'Fakta terverifikasi per ticker (price, fundamental, ownership, news, SEC filings)'],
  knowledge: ['Knowledge', '7-section profile per ticker, hasil sintesis Evidence'],
  catalyst: ['Catalyst Tracking', 'Peristiwa mendatang per ticker (earnings, dll) — data/catalysts.json'],
  institutional_flow: ['Aliran Dana Institusi', 'Arah kepemilikan ±10 pemegang institusi terbesar per saham — 13F via Yahoo, bukan net flow seluruh institusi'],
  peer: ['Peer Comparison', 'Posisi percentile terhadap peer group'],
  confidence: ['Confidence Report', 'Kekuatan data 0-100 per 7 section Knowledge + penalti peer/context'],
  risk: ['Risk / Red Flags', 'Deteksi anomali governance, financial, momentum, valuation'],
  reasoning: ['Reasoning — 3 Lensa', 'Multibagger, Quality/Compound, Speculative — masing-masing kosakata stance sendiri (D-09)'],
  aggregator: ['Aggregator + Synthesis', '3 lensa berdampingan + peta kesepakatan/perbedaan — tanpa skor tunggal (D-04)'],
  historical: ['Historical Tracking', 'Snapshot analisa utuh per hari (evaluasi outcome menyusul v2.1)'],
  personal_aggregator: ['Agregator Pribadi', 'Action + horizon per lens, dibaca dari stance Reasoning — tidak pernah dipublikasikan'],
  personal_historical: ['Riwayat Pribadi', 'Snapshot rekomendasi pribadi per hari — outcome dievaluasi otomatis begitu call jatuh tempo'],
  personal_calibration: ['Rapor Kalibrasi', 'Hit rate per irisan keputusan, dengan gerbang bukti — melaporkan juga apa yang belum bisa dinilai'],
}

const VIEWS = {
  layer1: Layer1View,
  screening: ScreeningView,
  evidence: EvidenceView,
  knowledge: KnowledgeView,
  catalyst: CatalystView,
  institutional_flow: InstitutionalFlowView,
  peer: PeerView,
  confidence: ConfidenceView,
  risk: RiskView,
  reasoning: ReasoningView,
  aggregator: AggregatorView,
  historical: HistoricalView,
  personal_aggregator: PersonalAggregatorView,
  personal_historical: PersonalHistoricalView,
  personal_calibration: PersonalCalibrationView,
}

export default function App() {
  const [activeView, setActiveView] = useState('layer1')
  const [modalTicker, setModalTicker] = useState(null)
  const [personalEnabled, setPersonalEnabled] = useState(false)
  const [inconsistent, setInconsistent] = useState(false)

  useEffect(() => {
    let cancelled = false
    api.capabilities()
      .then((d) => { if (!cancelled) setPersonalEnabled(!!d.personal_enabled) })
      .catch(() => { if (!cancelled) setPersonalEnabled(false) })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    // Audit item C2/C9: 10 file stage ditulis atomik satu-satu di akhir
    // pipeline, bukan satu transaksi -- kill eksternal di tengah blok itu
    // bisa menyisakan campuran dua run berbeda. /api/consistency membaca
    // marker session_id yang sekarang ditulis seragam di semua file itu;
    // kalau tidak seragam, tampilkan peringatan di sini -- daripada diam-diam
    // menyajikan data campuran tanpa satu pun yang memeriksanya (temuan
    // audit: info-nya sudah ada sejak lama, cuma tidak ada konsumennya).
    let cancelled = false
    api.consistency()
      .then((d) => { if (!cancelled) setInconsistent(!d.consistent) })
      .catch(() => { if (!cancelled) setInconsistent(false) })
    return () => { cancelled = true }
  }, [])

  const ActiveView = VIEWS[activeView]
  const [title, desc] = TITLES[activeView]

  return (
    <div className="app">
      <Sidebar activeView={activeView} onSelect={setActiveView} personalEnabled={personalEnabled} />

      <div className="main">
        <div className="topbar">
          <div>
            <h1>{title}</h1>
            <p>{desc}</p>
          </div>
          <GenerateButton />
        </div>

        {inconsistent && (
          <div
            style={{
              padding: '8px 26px', fontSize: 12.5, fontFamily: 'var(--sans)',
              color: 'var(--bad)', background: 'rgba(251,113,133,.08)',
              borderBottom: '1px solid rgba(251,113,133,.28)',
            }}
            title="File di dashboard/data/ punya session_id yang tidak seragam -- kemungkinan run pipeline terhenti di tengah blok tulis. Lihat /api/consistency untuk detail."
          >
            ⚠ Data tidak konsisten: sebagian file berasal dari run pipeline yang berbeda (kemungkinan run sebelumnya terhenti di tengah jalan).
          </div>
        )}

        <div className="content">
          <ViewErrorBoundary viewKey={activeView}>
            <ActiveView onSelectTicker={setModalTicker} />
          </ViewErrorBoundary>
        </div>
      </div>

      {modalTicker && (
        <TickerModal ticker={modalTicker} context={activeView} onClose={() => setModalTicker(null)} />
      )}
    </div>
  )
}
