// Icon set inline (stroke SVG, mewarisi currentColor). Tanpa dependency eksternal.

const ICONS = {
  // stat cards
  gauge: '<path d="M4 15a8 8 0 1 1 16 0"/><path d="M12 15l3.5-4"/><circle cx="12" cy="15" r="1.4"/>',
  layers: '<path d="M12 3 3 8l9 5 9-5-9-5z"/><path d="m3 12 9 5 9-5"/><path d="m3 16 9 5 9-5"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  alert: '<path d="M12 4 2.5 20h19L12 4z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
  shield: '<path d="M12 3 5 6v5c0 4 3 7 7 8 4-1 7-4 7-8V6l-7-3z"/><path d="m9 12 2 2 4-4"/>',
  // components
  cycle: '<path d="M20 11a8 8 0 1 0-.6 3"/><path d="M20 4v7h-7"/>',
  rotate: '<path d="M4 12a8 8 0 1 1 2.5 5.8"/><path d="M4 20v-5h5"/>',
  flow: '<path d="m3 17 5-5 4 4 9-9"/><path d="M17 7h4v4"/>',
  droplet: '<path d="M12 3s6 5.5 6 10a6 6 0 1 1-12 0c0-4.5 6-10 6-10z"/>',
  chartline: '<path d="M4 5v14h16"/><path d="m7 14 4-4 3 3 5-6"/>',
  activity: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
  calendar: '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M4 10h16"/><path d="M8 3v4"/><path d="M16 3v4"/>',
  dollar: '<path d="M12 3v18"/><path d="M16 7.5A3 3 0 0 0 13 6h-2.2a2.3 2.3 0 0 0 0 4.6h2.4a2.3 2.3 0 0 1 0 4.6H11a3 3 0 0 1-3-1.6"/>',
  flame: '<path d="M12 3c.5 3 3.5 4 3.5 7.5A3.5 3.5 0 1 1 8.5 11c0-1.3.6-2.2 1.4-3 .1 1.3 1 1.9 1.6 2 .3-2-.5-4-.5-7z"/>',
  wave: '<path d="M3 12h3.5l2-5 4 10 2-5H20"/>',
  bars: '<path d="M4 20h16"/><rect x="5" y="11" width="3" height="7" rx="1"/><rect x="10.5" y="6" width="3" height="12" rx="1"/><rect x="16" y="9" width="3" height="9" rx="1"/>',
  mood: '<circle cx="12" cy="12" r="9"/><path d="M9 10h.01"/><path d="M15 10h.01"/><path d="M9 14.5h6"/>',
  bond: '<path d="M4 7h11"/><path d="M4 12h7"/><path d="M4 17h14"/><path d="M17 9.5v5"/><circle cx="17" cy="7" r="1.6"/><circle cx="20" cy="17" r="1.6"/>',
  clipboard: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1"/><path d="M8 11h8"/><path d="M8 15h5"/>',
  newspaper: '<path d="M4 6h13a2 2 0 0 1 2 2v11a1.5 1.5 0 0 1-1.5 1.5H6a2 2 0 0 1-2-2z"/><path d="M19 9v9a1.5 1.5 0 0 0 1.5 1.5"/><path d="M7 9h7"/><path d="M7 12.5h7"/><path d="M7 16h4"/>',
  filedoc: '<path d="M7 2.5h7L18.5 7v14.5H7z"/><path d="M14 2.5V7h4.5"/><path d="M9.5 12.5h5"/><path d="M9.5 15.5h5"/><path d="M9.5 18.5h3"/>',
  dot: '<circle cx="12" cy="12" r="4"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
}

export default function Icon({ name, size = 18 }) {
  const inner = ICONS[name] || ICONS.dot
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      dangerouslySetInnerHTML={{ __html: inner }}
    />
  )
}
