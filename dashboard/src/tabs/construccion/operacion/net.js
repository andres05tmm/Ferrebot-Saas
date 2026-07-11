/*
 * net.js — helpers de red compartidos por los modales de operación (activar/rotar/finalizar). Centraliza
 * el POST con manejo del `detail` del backend (409/404) y el formateo de una hora ISO a hora Colombia.
 */
import { api } from '@/lib/api'

// Lee el `detail` de un error del backend sin romper si el body no es JSON.
async function detalleError(res) {
  try {
    const b = await res.json()
    return typeof b?.detail === 'string' ? b.detail : null
  } catch {
    return null
  }
}

// POST JSON → { ok, data } | { ok:false, error }. Los modales muestran `error` en un toast.
export async function postOperacion(path, body) {
  try {
    const res = await api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    })
    if (!res.ok) return { ok: false, error: (await detalleError(res)) || 'No se pudo completar la acción' }
    return { ok: true, data: await res.json().catch(() => ({})) }
  } catch {
    return { ok: false, error: 'Error de conexión' }
  }
}

// Instante ISO (TIMESTAMPTZ del backend) → "H:MM" en hora Colombia (para las franjas de tramo).
export function horaIso(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString('es-CO', {
    hour: 'numeric', minute: '2-digit', hour12: false, timeZone: 'America/Bogota',
  })
}
