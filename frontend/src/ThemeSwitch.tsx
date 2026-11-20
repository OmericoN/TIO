import { useEffect, useState } from 'react'
import { Icon } from './Brand'

let initialTheme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
try {
  const saved = localStorage.getItem('tio-theme')
  if (saved === 'light' || saved === 'dark') initialTheme = saved
} catch { /* Theme selection still works when browser storage is unavailable. */ }
document.documentElement.dataset.theme = initialTheme

export function ThemeSwitch() {
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === 'dark')
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(document.documentElement.dataset.theme === 'dark'))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])
  return <div className="theme-switch" role="group" aria-label="Color theme">
    {(['light', 'dark'] as const).map(theme => <button key={theme} type="button"
      aria-pressed={dark === (theme === 'dark')} onClick={() => {
        document.documentElement.dataset.theme = theme
        setDark(theme === 'dark')
        try { localStorage.setItem('tio-theme', theme) } catch { /* Keep the selection for this visit. */ }
      }}><Icon name={theme === 'light' ? 'sun' : 'moon'} />{theme === 'light' ? 'Light' : 'Dark'}</button>)}
  </div>
}
