/*
 * useCronometro — tiempo transcurrido desde un instante ISO, actualizado cada segundo. La ÚNICA pieza
 * net-new de la operación en vivo (el resto reúsa SSE/Semáforo/formularios). Devuelve "H:MM:SS".
 *
 * El reloj de pared corre en el cliente (setInterval 1s); la duración FACTURABLE la calcula el backend al
 * finalizar (desde los timestamps guardados), no este contador — así un tab dormido o un refresco no
 * alteran la facturación. `activo=false` congela el conteo (para tramos ya cerrados).
 */
import { useEffect, useState } from 'react'

export function useCronometro(desdeIso, activo = true) {
  const [ahora, setAhora] = useState(() => Date.now())

  useEffect(() => {
    if (!activo) return undefined
    const id = setInterval(() => setAhora(Date.now()), 1000)
    return () => clearInterval(id)
  }, [activo])

  return formatearElapsed(desdeIso ? ahora - new Date(desdeIso).getTime() : 0)
}

// ms → "H:MM:SS" (nunca negativo). Exportada para testear el formateo sin el intervalo.
export function formatearElapsed(ms) {
  const s = Math.max(0, Math.floor((ms || 0) / 1000))
  const hh = Math.floor(s / 3600)
  const mm = Math.floor((s % 3600) / 60)
  const ss = s % 60
  return `${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}
