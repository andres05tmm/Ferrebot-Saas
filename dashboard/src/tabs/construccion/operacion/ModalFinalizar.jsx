/*
 * ModalFinalizar — REVISIÓN antes de facturar (decisión del dueño): el reloj propone las horas de cada
 * tramo y el supervisor las confirma/ajusta. GET /operacion/{id} trae los tramos con `horas_propuestas`;
 * al confirmar, POST /operacion/{id}/finalizar {ajustes:[{tramo_id,horas}]} materializa el parte del día
 * (mínimo facturable + cartera). Dado el margen 3–4% de PIM, nada se factura sin este paso.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import { useFetch } from '@/components/shared.jsx'
import { Campo, SELECT_CLS, BTN_PRIMARY, BTN_OUTLINE, Esqueleto } from '../comunes.jsx'
import { num } from '@/components/shared.jsx'
import { postOperacion, horaIso } from './net.js'

export default function ModalFinalizar({ sesion, onCerrar, onExito }) {
  const detalleQ = useFetch(sesion ? `/operacion/${sesion.sesion_id}` : null)
  const [horas, setHoras] = useState({})   // tramo_id → string (override)
  const [enviando, setEnviando] = useState(false)

  const tramos = Array.isArray(detalleQ.data?.tramos) ? detalleQ.data.tramos : []
  const valor = (t) => (horas[t.id] !== undefined ? horas[t.id] : String(Number(t.horas_propuestas)))
  const total = tramos.reduce((s, t) => s + (Number(valor(t)) || 0), 0)

  async function finalizar() {
    setEnviando(true)
    const ajustes = tramos.map((t) => ({ tramo_id: t.id, horas: Number(valor(t)) || 0 }))
    const r = await postOperacion(`/operacion/${sesion.sesion_id}/finalizar`, { ajustes })
    setEnviando(false)
    if (!r.ok) { toast.error(r.error); return }
    const facturables = Number(r.data?.horas_facturables ?? total)
    toast.success(`Parte registrado · ${num(facturables)} h facturables`)
    onExito?.()
  }

  return (
    <Dialog open={sesion != null} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="finalizar-desc" className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Finalizar operación — {sesion?.maquina}</DialogTitle>
          <DialogDescription id="finalizar-desc">
            Revisa las horas de cada tramo (las propone el reloj) antes de registrar el parte del día.
          </DialogDescription>
        </DialogHeader>

        {detalleQ.loading ? (
          <Esqueleto filas={2} />
        ) : tramos.length === 0 ? (
          <p className="py-4 text-[13px] text-muted-foreground">Esta sesión no tiene tramos.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {tramos.map((t) => (
              <li key={t.id} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] text-foreground">{t.operador || 'Sin operador'}</div>
                  <div className="text-[11px] tabular-nums text-muted-foreground">
                    {horaIso(t.iniciado_en)}{t.finalizado_en ? `–${horaIso(t.finalizado_en)}` : ' · en curso'}
                  </div>
                </div>
                <Campo label="Horas" className="w-24">
                  <input
                    type="number" inputMode="decimal" step="0.25" min="0"
                    value={valor(t)}
                    onChange={(e) => setHoras((prev) => ({ ...prev, [t.id]: e.target.value }))}
                    className={`${SELECT_CLS} tabular`}
                  />
                </Campo>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-1 flex items-center justify-between border-t border-border-subtle pt-2 text-[13px]">
          <span className="text-muted-foreground">Total del día</span>
          <span className="font-semibold tabular-nums text-foreground">{num(total)} h</span>
        </div>

        <div className="mt-3 flex justify-end gap-2">
          <button type="button" onClick={onCerrar} className={`${BTN_OUTLINE} h-9 cursor-pointer`}>Cancelar</button>
          <button
            type="button" onClick={finalizar} disabled={enviando || detalleQ.loading}
            className={`${BTN_PRIMARY} h-9 cursor-pointer`}
          >
            {enviando ? 'Registrando…' : 'Registrar parte'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
