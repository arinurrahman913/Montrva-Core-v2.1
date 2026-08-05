import Icon from './Icon'

const NAV_GROUPS = [
  { title: 'Market', items: [{ id: 'layer1', label: 'Layer 1 — Context' }] },
  {
    title: 'Fase A — Per Ticker',
    items: [
      { id: 'screening', label: 'Screening' },
      { id: 'evidence', label: 'Evidence' },
      { id: 'knowledge', label: 'Knowledge' },
      { id: 'catalyst', label: 'Catalyst' },
    ],
  },
  {
    title: 'Fase B — Populasi',
    items: [
      // Data publik (13F lewat Yahoo), nol data pribadi — karena itu ia duduk
      // di grup publik ini, bukan di grup "Pribadi" yang hilang total saat
      // personal_enabled false (§9 pemisahan lapisan pribadi).
      { id: 'institutional_flow', label: 'Aliran Dana Institusi' },
      { id: 'peer', label: 'Peer Comparison' },
      { id: 'confidence', label: 'Confidence' },
      { id: 'risk', label: 'Risk / Red Flags' },
      { id: 'reasoning', label: 'Reasoning' },
      { id: 'aggregator', label: 'Aggregator' },
    ],
  },
  { title: 'Tracking', items: [{ id: 'historical', label: 'Historical' }] },
]

// Grup terpisah, dirender cuma kalau personalEnabled (backend /api/
// capabilities) -- rilis publik (folder alphaforge/personal/ dihapus)
// membuat capabilities.personal_enabled otomatis false, grup ini hilang
// total tanpa menyentuh NAV_GROUPS publik di atas sama sekali.
const PERSONAL_NAV_GROUP = {
  title: 'Pribadi',
  items: [
    { id: 'personal_aggregator', label: 'Agregator Pribadi' },
    { id: 'personal_historical', label: 'Riwayat Pribadi' },
    { id: 'personal_calibration', label: 'Rapor Kalibrasi' },
  ],
}

export default function Sidebar({ activeView, onSelect, personalEnabled }) {
  const groups = personalEnabled ? [...NAV_GROUPS, PERSONAL_NAV_GROUP] : NAV_GROUPS

  return (
    <div className="sidebar">
      <div className="brand">
        <div className="brand-name">
          AlphaForge <b>v2</b>
        </div>
        <div className="brand-sub">pipeline dashboard · data live</div>
      </div>

      {groups.map((group) => (
        <div className={`nav-group${group === PERSONAL_NAV_GROUP ? ' nav-group-personal' : ''}`} key={group.title}>
          <div className="nav-group-title">
            {group === PERSONAL_NAV_GROUP && (
              <span className="nav-lock"><Icon name="lock" size={11} /></span>
            )}
            {group.title}
          </div>
          {group.items.map((item) => (
            <div
              key={item.id}
              className={`nav-item${activeView === item.id ? ' active' : ''}`}
              onClick={() => onSelect(item.id)}
            >
              {item.label}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
