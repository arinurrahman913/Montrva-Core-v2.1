import Icon from './Icon'

// Halaman yang tahapnya TETAP DIHITUNG pipeline tapi nav-nya disembunyikan
// (15 Agu 2026, atas permintaan pengguna). Menghapus itemnya dari daftar di
// bawah akan menghilangkan alasannya juga; disimpan sebagai set terpisah supaya
// mengembalikannya cukup mengosongkan set ini.
//
// KENAPA disembunyikan, bukan tahapnya dibuang: ongkos MENGHITUNG keduanya cuma
// 14 detik dari 17.026 (0,08% satu run penuh). Yang berat justru penyajiannya —
// /api/aggregator mengirim 43 MB ke browser tiap halaman itu dibuka. Nav yang
// disembunyikan menghapus 43 MB itu tanpa membuang datanya.
//
// `reasoning` SENGAJA TIDAK di sini walau juga jarang dibuka: seluruh lapisan
// pribadi berdiri di atasnya (action pribadi = tabel lookup dari stance yang
// dihasilkan reasoning.py, "tidak ada mesin skor kedua"). Aggregator &
// Historical umum diperiksa dan TIDAK disentuh montrva/personal/ sama sekali.
const HIDDEN_NAV_ITEMS = new Set(['aggregator', 'historical'])

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
// capabilities) -- rilis publik (folder montrva/personal/ dihapus)
// membuat capabilities.personal_enabled otomatis false, grup ini hilang
// total tanpa menyentuh NAV_GROUPS publik di atas sama sekali.
const PERSONAL_NAV_GROUP = {
  title: 'Pribadi',
  items: [
    // Paling atas: satu-satunya halaman tempat pengguna MENULIS, dan yang
    // memberi makan seluruh sisa grup ini (holdings.json -> position_status
    // -> kolom `holding` di ACTION_TABLE).
    { id: 'portfolio', label: 'Portofolio' },
    { id: 'personal_aggregator', label: 'Agregator Pribadi' },
    { id: 'personal_historical', label: 'Riwayat Pribadi' },
    { id: 'personal_calibration', label: 'Rapor Kalibrasi' },
  ],
}

export default function Sidebar({ activeView, onSelect, personalEnabled }) {
  const all = personalEnabled ? [...NAV_GROUPS, PERSONAL_NAV_GROUP] : NAV_GROUPS
  // Grup yang seluruh itemnya tersembunyi ikut hilang — kalau tidak, "Tracking"
  // tersisa sebagai judul tanpa isi (Historical satu-satunya penghuninya).
  const groups = all
    // `personal` ditandai eksplisit, BUKAN dibandingkan lewat identitas objek:
    // .map() di bawah membuat objek baru, jadi `group === PERSONAL_NAV_GROUP`
    // akan diam-diam jadi false dan grup Pribadi kehilangan gembok + gayanya.
    .map((g) => ({ ...g, personal: g === PERSONAL_NAV_GROUP,
                   items: g.items.filter((i) => !HIDDEN_NAV_ITEMS.has(i.id)) }))
    .filter((g) => g.items.length > 0)

  return (
    <div className="sidebar">
      <div className="brand">
        {/* Monogram digambar inline sebagai SVG, bukan berkas gambar: ukurannya
            ~600 byte, ikut warna tema lewat currentColor-nya sendiri, dan tetap
            tajam di layar berapa pun DPR tanpa perlu varian @2x. Bentuk M/V di
            sini PENDEKATAN dari logo — begitu ada berkas SVG aslinya, ganti isi
            <svg> ini saja, sisa markup tidak perlu berubah. */}
        <svg className="brand-mark" viewBox="0 0 60 60" role="img" aria-label="Monogram MONTRVA">
          <circle cx="30" cy="30" r="26" fill="none" stroke="var(--gold-dim)" strokeWidth="1.4" />
          <text x="29" y="41" textAnchor="middle" fontFamily="Georgia, serif" fontSize="31" fill="var(--gold)">M</text>
          <text x="37" y="43" textAnchor="middle" fontFamily="Georgia, serif" fontSize="25" fill="var(--gold-dim)">V</text>
        </svg>
        <div className="brand-name">MONTRVA</div>
        <div className="brand-rule" />
        <div className="brand-sub">Market Intelligence</div>
        <div className="brand-sub">&amp; Research</div>
      </div>

      {groups.map((group) => (
        <div className={`nav-group${group.personal ? ' nav-group-personal' : ''}`} key={group.title}>
          <div className="nav-group-title">
            {group.personal && (
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
