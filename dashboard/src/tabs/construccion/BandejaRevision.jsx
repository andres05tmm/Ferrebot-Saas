/*
 * BandejaRevision — cola de recibos que el bot importó con baja confianza (F5, vertical construcción).
 *
 * Los trabajadores de campo mandan foto de recibo por Telegram; si la extracción queda con confianza
 * < 0.7 el gasto se guarda con `requiere_revision=true` y NO cuenta como definitivo hasta que un admin
 * lo apruebe. Esta sección vive ARRIBA de la pestaña Gastos y le da al dueño una cola accionable:
 * ver el recibo, la plata y la imputación, y aprobar de un toque.
 *
 *   GET  /gastos/revision      → lista de GastoLeer con requiere_revision=true (dinero como STRING).
 *   POST /gastos/{id}/aprobar  → baja el flag (idempotente); la fila sale de la cola.
 *
 * Solo admin (cifras financieras) y SILENCIOSA por diseño (patrón ResumenPortafolio): si el fetch falla,
 * la cola está vacía o el usuario no es admin, devuelve null y no estorba al resto de Gastos.
 * Presentación tokenizada (design system del repo, comunes.jsx): ámbar solo para la acción, el riesgo
 * (si aplica) solo por Semáforo.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { ReceiptText, ExternalLink, Check, HardHat, Truck, Bot } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Semaforo, BTN_PRIMARY } from './comunes.jsx'

const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }

// Fecha corta en hora Colombia (regla #4): dd MMM, sin ambigüedad de zona.
function fechaCorta(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('es-CO', {
      timeZone: 'America/Bogota', day: '2-digit', month: 'short',
    })
  } catch { return '' }
}

export default function BandejaRevision({ refreshKey }) {
  const { isAdmin } = useAuth()
  const admin = isAdmin()

  // Sin admin no se pide nada (path falsy → useFetch queda en reposo, sin 403).
  const revisionQ = useFetch(admin ? '/gastos/revision' : null, [refreshKey])
  useRealtimeEvent(['gasto_registrado', 'reconnected'], revisionQ.refetch)

  // Aprobados de forma optimista: se ocultan al instante; si el POST falla, vuelven.
  const [aprobados, setAprobados] = useState(() => new Set())
  const [enviando, setEnviando] = useState(() => new Set())

  const lista = (Array.isArray(revisionQ.data) ? revisionQ.data : []).filter((g) => !aprobados.has(g.id))

  // Silencioso: mientras carga, si falla, si no hay admin o si la cola quedó vacía.
  if (!admin || revisionQ.loading || revisionQ.error || lista.length === 0) return null

  async function aprobar(id) {
    setEnviando((s) => new Set(s).add(id))
    setAprobados((s) => new Set(s).add(id))   // optimista: fuera de la lista ya
    try {
      const res = await api(`/gastos/${id}/aprobar`, { method: 'POST' })
      if (res.ok) {
        toast.success('Recibo aprobado')
      } else {
        // Revertir: la fila vuelve a la cola.
        setAprobados((s) => { const c = new Set(s); c.delete(id); return c })
        toast.error('No se pudo aprobar el recibo')
      }
    } catch {
      setAprobados((s) => { const c = new Set(s); c.delete(id); return c })
      toast.error('Error de conexión')
    } finally {
      setEnviando((s) => { const c = new Set(s); c.delete(id); return c })
    }
  }

  return (
    <Card className="p-3.5 border-l-[3px] border-l-warning" aria-label="Recibos del bot por revisar">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <ReceiptText className="size-4 text-muted-foreground" aria-hidden="true" />
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Por revisar · {lista.length} {lista.length === 1 ? 'recibo del bot' : 'recibos del bot'}
        </h2>
      </div>

      <ul className="divide-y divide-border-subtle">
        {lista.map((g) => {
          // Códigos del vertical (REPUESTOS, MANTENIMIENTO_MAQUINA…) → legibles; sin código → fallback.
          const cat = g.categoria_gasto ? g.categoria_gasto.toLowerCase().replace(/_/g, ' ') : 'Sin clasificar'
          const esBot = g.origen_registro === 'TELEGRAM_BOT'
          return (
            <li key={g.id} className="flex items-center gap-3 py-2.5">
              <Recibo url={g.comprobante_url} />

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <span className="tabular text-[15px] font-semibold text-foreground">{cop(n(g.monto))}</span>
                  <span className="text-[11px] text-muted-foreground">{fechaCorta(g.creado_en)}</span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  <span className="text-[12px] capitalize text-secondary-foreground">{cat}</span>
                  {g.obra_id != null && (
                    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <HardHat className="size-3" aria-hidden="true" /> Obra #{g.obra_id}
                    </span>
                  )}
                  {g.maquina_id != null && (
                    <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                      <Truck className="size-3" aria-hidden="true" /> Máquina #{g.maquina_id}
                    </span>
                  )}
                  {esBot && (
                    <Semaforo tono="azul" className="gap-1">
                      <Bot className="size-3" aria-hidden="true" /> Bot
                    </Semaforo>
                  )}
                </div>
              </div>

              <button
                type="button"
                onClick={() => aprobar(g.id)}
                disabled={enviando.has(g.id)}
                className={`${BTN_PRIMARY} h-9 shrink-0`}
              >
                <Check className="size-4" aria-hidden="true" />
                {enviando.has(g.id) ? 'Aprobando…' : 'Aprobar'}
              </button>
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

/** Miniatura del recibo → abre el comprobante en pestaña nueva. Sin URL: placeholder con icono. */
function Recibo({ url }) {
  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="group relative size-11 shrink-0 overflow-hidden rounded-md border border-border bg-surface-2"
        aria-label="Ver recibo"
      >
        <img src={url} alt="Recibo" className="size-full object-cover" />
        <span className="absolute inset-0 grid place-items-center bg-black/0 text-transparent transition-colors group-hover:bg-black/45 group-hover:text-white">
          <ExternalLink className="size-4" aria-hidden="true" />
        </span>
      </a>
    )
  }
  return (
    <span className="grid size-11 shrink-0 place-items-center rounded-md border border-border bg-surface-2 text-muted-foreground" aria-hidden="true">
      <ReceiptText className="size-4" />
    </span>
  )
}
