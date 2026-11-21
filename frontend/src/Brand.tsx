const icons = {
  stop: 'M6 6h12v12H6z',
  play: 'm8 5 11 7-11 7Z',
  pause: 'M6 5h4v14H6zM14 5h4v14h-4z',
  profile: 'M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM4 21v-2a8 8 0 0 1 16 0v2',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5',
  moon: 'M20.5 13.2A8.5 8.5 0 0 1 10.8 3.5a8.5 8.5 0 1 0 9.7 9.7Z',
  feed: 'M3 5h18v14H3z M7 9h4v6H7z M14 9h3m-3 3h3m-3 3h3',
  discussion: 'M3 11a9 9 0 0 1 18 0v5a2 2 0 0 1-2 2h-2v-7h4 M3 11h4v7H5a2 2 0 0 1-2-2z',
  voice: 'M9 6a3 3 0 1 1 6 0v5a3 3 0 1 1-6 0z M5 10v1a7 7 0 0 0 14 0v-1m-7 8v3m-3 0h6',
  arrow: 'M4 12h15m-6-6 6 6-6 6',
  list: 'M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01',
  search: 'M16.5 16.5 21 21 M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z',
}

export function Icon({ name }: { name: keyof typeof icons }) {
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={icons[name]} /></svg>
}

export function Brand() {
  return <span className="brand">TIO</span>
}

// Each line interpolates between two curves to give the audio ribbon its twist.
export function AudioRibbon() {
  return <svg className="audio-ribbon" viewBox="0 0 700 300" preserveAspectRatio="none" fill="none" aria-hidden="true">
    {Array.from({ length: 72 }, (_, index) => {
      const t = index / 71
      const arch = Math.sin(t * Math.PI)
      const path = `M-22 ${175 + 15*t} C85 ${175 + 42*t}, 115 ${25 - 46*arch}, 225 ${78 + 110*t} S370 ${275 + 20*arch - 140*t}, 450 ${204 - 109*t} S588 ${25 + 28*t - 40*arch}, 725 ${125 + 40*t}`
      return <path key={index} d={path} stroke="currentColor" strokeWidth={0.85} opacity={0.42 + 0.54 * arch} />
    })}
  </svg>
}
