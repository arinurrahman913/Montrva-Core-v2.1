import { useState } from 'react'
import Sidebar from './components/Sidebar'
import TickerModal from './components/TickerModal'
import GenerateButton from './components/GenerateButton'
import Layer1View from './views/Layer1View'
import ScreeningView from './views/ScreeningView'
import EvidenceView from './views/EvidenceView'
import KnowledgeView from './views/KnowledgeView'
import CatalystView from './views/CatalystView'
import PeerView from './views/PeerView'
import ConfidenceView from './views/ConfidenceView'
import RiskView from './views/RiskView'
import ReasoningView from './views/ReasoningView'
import AggregatorView from './views/AggregatorView'
import HistoricalView from './views/HistoricalView'

const TITLES = {
  layer1: ['Layer 1 — Market Context', '13 komponen makro, membaca data/layer1_context.json'],
  screening: ['Screening', 'Filter kandidat dari universe mentah — data/screening.json'],
  evidence: ['Evidence', 'Fakta terverifikasi per ticker (price, fundamental, ownership, news, SEC filings)'],
  knowledge: ['Knowledge', '7-section profile per ticker, hasil sintesis Evidence'],
  catalyst: ['Catalyst Tracking', 'Peristiwa mendatang per ticker (earnings, dll) — data/catalysts.json'],
  peer: ['Peer Comparison', 'Posisi percentile terhadap peer group'],
  confidence: ['Confidence Report', 'Kekuatan data 0-100 per 7 section Knowledge + penalti peer/context'],
  risk: ['Risk / Red Flags', 'Deteksi anomali governance, financial, momentum, valuation'],
  reasoning: ['Reasoning — 3 Lensa', 'Multibagger, Quality/Compound, Speculative — masing-masing kosakata stance sendiri (D-09)'],
  aggregator: ['Aggregator + Synthesis', '3 lensa berdampingan + peta kesepakatan/perbedaan — tanpa skor tunggal (D-04)'],
  historical: ['Historical Tracking', 'Snapshot analisa utuh per hari (evaluasi outcome menyusul v2.1)'],
}

const VIEWS = {
  layer1: Layer1View,
  screening: ScreeningView,
  evidence: EvidenceView,
  knowledge: KnowledgeView,
  catalyst: CatalystView,
  peer: PeerView,
  confidence: ConfidenceView,
  risk: RiskView,
  reasoning: ReasoningView,
  aggregator: AggregatorView,
  historical: HistoricalView,
}

export default function App() {
  const [activeView, setActiveView] = useState('layer1')
  const [modalTicker, setModalTicker] = useState(null)

  const ActiveView = VIEWS[activeView]
  const [title, desc] = TITLES[activeView]

  return (
    <div className="app">
      <Sidebar activeView={activeView} onSelect={setActiveView} />

      <div className="main">
        <div className="topbar">
          <div>
            <h1>{title}</h1>
            <p>{desc}</p>
          </div>
          <GenerateButton />
        </div>

        <div className="content">
          <ActiveView onSelectTicker={setModalTicker} />
        </div>
      </div>

      {modalTicker && (
        <TickerModal ticker={modalTicker} context={activeView} onClose={() => setModalTicker(null)} />
      )}
    </div>
  )
}
