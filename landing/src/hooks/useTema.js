import { useEffect, useState } from 'react'
import { temaActual } from '@/lib/tema.js'

/** Tema vivo de la página (claro/oscuro): observa data-tema en <html>. */
export function useTema() {
  const [tema, setTema] = useState(() =>
    typeof document === 'undefined' ? 'claro' : temaActual(),
  )
  useEffect(() => {
    const obs = new MutationObserver(() => setTema(temaActual()))
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-tema'] })
    return () => obs.disconnect()
  }, [])
  return tema
}
