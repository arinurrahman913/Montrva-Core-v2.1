import { Component } from 'react'

// Pagar supaya satu view yang meledak tidak mengosongkan SELURUH dashboard.
//
// 2026-08-04: satu bar spx_history dengan nilai null (Yahoo mengembalikan bar
// untuk hari yang belum diperdagangkan karena run selesai 00:06) membuat
// `null.toFixed()` melempar TypeError di Layer1PerformanceChart. Tanpa
// boundary, React membongkar seluruh pohon -- halaman jadi putih polos tanpa
// pesan apa pun, dan penyebabnya cuma bisa ditemukan dengan menyadap
// console.error secara manual. Angka yang hilang seharusnya merusak satu
// panel, bukan menyembunyikan 13 halaman lain.
//
// Sengaja dipasang per-view (bukan sekali di root) supaya sidebar dan topbar
// tetap hidup: pengguna bisa pindah ke halaman lain tanpa reload.
export default class ViewErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Tetap dicetak: boundary menahan crash-nya, bukan menyembunyikan sebabnya.
    console.error('View crash:', error, info?.componentStack)
  }

  componentDidUpdate(prevProps) {
    // Pindah view = kesempatan baru. Tanpa ini, sekali satu view error,
    // boundary-nya tetap menampilkan pesan error walau view-nya sudah ganti.
    if (prevProps.viewKey !== this.props.viewKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="chart-card" style={{ padding: 20, margin: 20 }}>
        <div className="chart-title" style={{ color: 'var(--bad)' }}>Halaman ini gagal dirender</div>
        <p style={{ fontSize: 12.5, color: 'var(--dim)', lineHeight: 1.7, margin: '8px 0 0' }}>
          Halaman lain tetap bisa dibuka lewat menu di kiri. Pesan aslinya:
        </p>
        <pre style={{
          marginTop: 8, padding: 10, fontSize: 11, fontFamily: 'var(--mono)',
          color: 'var(--dim)', background: 'var(--panel)', borderRadius: 6,
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>
          {String(this.state.error?.message || this.state.error)}
        </pre>
      </div>
    )
  }
}
