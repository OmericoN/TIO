import { createRoot } from 'react-dom/client'
import { LiveHostPreview } from './LiveHostPreview'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <main className="mx-auto max-w-3xl px-6 py-12">
    <h1 className="text-3xl font-semibold">TIO</h1>
    <LiveHostPreview />
  </main>,
)
