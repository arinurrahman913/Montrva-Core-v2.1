import { useEffect, useRef, useState } from 'react'

// Satu tempat untuk memutuskan "animasi boleh jalan atau tidak".
//
// Dua syaratnya beda sifat dan dua-duanya nyata:
//   - prefers-reduced-motion: permintaan eksplisit user, wajib dihormati.
//   - document.hidden: tab latar membekukan requestAnimationFrame. Kalau
//     animasi jadi SYARAT angkanya muncul, modal yang dibuka di tab latar
//     menampilkan "0,00" abadi sampai tab-nya dilihat. Ketahuan saat menguji
//     mockup penyajian angka (docs/MOCKUP_PENYAJIAN_ANGKA.html), bukan
//     sesudah rilis.
//
// Aturannya karena itu: nilai akhir SELALU jadi keadaan dasar; animasi cuma
// pengaya di atasnya.
export function motionOff() {
  if (typeof document === 'undefined') return true
  if (document.hidden) return true
  return typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * Angka yang naik dari 0 ke `value` SEKALI, saat komponennya pertama muncul.
 *
 * Sengaja TIDAK menganimasikan perubahan nilai berikutnya: kalau skor berubah
 * karena run pipeline baru, ia berganti diam-diam. Gerakan di situ terbaca
 * sebagai "sesuatu terjadi sekarang", padahal datanya dari run tadi malam.
 */
export function useCountUp(value, decimals = 2, delayMs = 0) {
  const [shown, setShown] = useState(value)
  const done = useRef(false)

  useEffect(() => {
    if (done.current || value == null || !Number.isFinite(value)) {
      setShown(value)
      return
    }
    done.current = true
    if (motionOff()) { setShown(value); return }

    let raf = 0
    let start = 0
    const DUR = 420
    const tick = (now) => {
      if (!start) start = now
      const p = Math.min(1, (now - start) / DUR)
      const eased = 1 - Math.pow(1 - p, 3)
      setShown(value * eased)
      if (p < 1) raf = requestAnimationFrame(tick)
    }
    const timer = setTimeout(() => { setShown(0); raf = requestAnimationFrame(tick) }, delayMs)
    return () => { clearTimeout(timer); cancelAnimationFrame(raf) }
  }, [value, decimals, delayMs])

  return shown == null || !Number.isFinite(shown) ? '—' : shown.toFixed(decimals)
}

/**
 * Gambar ulang sebuah <path> SVG kiri->kanan. Dipakai garis MA dan sparkline.
 * No-op (langsung tampil penuh) kalau animasi sedang dimatikan.
 */
export function drawPath(path, { duration = 500, delay = 0 } = {}) {
  if (!path || motionOff() || typeof path.getTotalLength !== 'function') return
  const len = path.getTotalLength()
  if (!len) return
  path.style.strokeDasharray = String(len)
  path.animate(
    [{ strokeDashoffset: len }, { strokeDashoffset: 0 }],
    { duration, delay, easing: 'ease-out', fill: 'backwards' },
  )
}
