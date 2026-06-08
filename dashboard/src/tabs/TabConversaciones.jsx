/*
 * TabConversaciones — bandeja de "escalar a humano" (handoff). Capacidad TRANSVERSAL del canal de cara
 * al cliente: se oculta sin la feature 'canal_whatsapp' (NO pack_agenda; el handoff no es de agenda).
 *
 * Lista las conversaciones que un agente pasó a un humano (GET /conversaciones/escaladas): teléfono del
 * cliente, motivo y hace cuánto se escaló (hora Colombia). Acción "Resolver / Devolver al bot"
 * (POST /conversaciones/{id}/resolver) → el bot retoma esa conversación. Tiempo real: las escalaciones
 * nuevas entran en vivo (useRealtimeEvent). El negocio le responde al cliente desde su PROPIO WhatsApp;
 * aquí solo marca cuándo terminó. (Mensajería dentro del dashboard: futuro, fuera de alcance.)
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { Headset, Bot, Phone, Clock, MessageSquareWarning, Inbox } from 'lucide-react'
import { api } from '@/lib/api.js'
import { useFetch, Spinner, ErrorMsg } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'

/** ISO con offset → 'vie 12/06 14:00' legible (hora Colombia, regla #4). */
function fmtFechaCO(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('es-CO', {
    timeZone: 'America/Bogota', weekday: 'short', day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** "hace cuánto" relativo a ahora (la escalada_en viene en ISO con offset). */
export function haceCuanto(iso, ahora = Date.now()) {
  if (!iso) return '—'
  const min = Math.floor((ahora - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'hace un momento'
  if (min < 60) return `hace ${min} min`
  const h = Math.floor(min / 60)
  if (h < 24) return `hace ${h} h`
  const d = Math.floor(h / 24)
  return `hace ${d} ${d === 1 ? 'día' : 'días'}`
}

export default function TabConversaciones() {
  const escaladasQ = useFetch('/conversaciones/escaladas')
  useRealtimeEvent(
    ['conversacion_escalada', 'conversacion_resuelta', 'reconnected'],
    escaladasQ.refetch,
  )

  const escaladas = Array.isArray(escaladasQ.data) ? escaladasQ.data : []

  return (
    <div className="space-y-3">
      <Encabezado total={escaladas.length} />
      <Nota />

      {escaladasQ.loading && escaladasQ.data === null ? (
        <Spinner />
      ) : escaladasQ.error ? (
        <ErrorMsg msg="No se pudieron cargar las conversaciones." />
      ) : escaladas.length === 0 ? (
        <EstadoVacio />
      ) : (
        <ul className="space-y-2">
          {escaladas.map(c => (
            <ConversacionItem key={c.id} conv={c} onResuelta={escaladasQ.refetch} />
          ))}
        </ul>
      )}
    </div>
  )
}

function Encabezado({ total }) {
  return (
    <div className="flex items-center gap-2">
      <h2 className="text-sm font-semibold inline-flex items-center gap-1.5">
        <Headset className="size-4 text-primary" /> Conversaciones en espera
      </h2>
      {total > 0 && (
        <Badge variant="primary" aria-label={`${total} en espera`}>
          {total} {total === 1 ? 'cliente' : 'clientes'}
        </Badge>
      )}
    </div>
  )
}

function Nota() {
  return (
    <p className="text-xs text-muted-foreground">
      Atiende a cada cliente desde tu propio WhatsApp. Cuando termines, pulsa{' '}
      <span className="font-medium text-foreground">Resolver</span> para que el bot vuelva a responderle.
    </p>
  )
}

function EstadoVacio() {
  return (
    <Card className="py-12 grid place-items-center text-center">
      <Inbox className="size-7 text-muted-foreground mb-2" aria-hidden="true" />
      <p className="text-sm font-medium">Sin conversaciones en espera</p>
      <p className="text-xs text-muted-foreground mt-1">
        Cuando un cliente pida un asesor, su conversación aparecerá aquí.
      </p>
    </Card>
  )
}

function ConversacionItem({ conv, onResuelta }) {
  const [resolviendo, setResolviendo] = useState(false)

  async function resolver() {
    setResolviendo(true)
    try {
      const res = await api(`/conversaciones/${conv.id}/resolver`, { method: 'POST' })
      if (res.ok) {
        toast.success('Conversación devuelta al bot')
        onResuelta()
      } else {
        toast.error('No se pudo resolver la conversación')
      }
    } catch {
      toast.error('Error de conexión')
    } finally {
      setResolviendo(false)
    }
  }

  return (
    <li>
      <Card className="p-3.5 flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 font-medium text-sm">
            <Phone className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
            <span className="truncate tabular-nums">{conv.cliente_telefono}</span>
          </div>
          {conv.motivo && (
            <div className="mt-1 flex items-start gap-1.5 text-[13px] text-muted-foreground">
              <MessageSquareWarning className="size-3.5 shrink-0 mt-0.5 text-warning" aria-hidden="true" />
              <span className="truncate">{conv.motivo}</span>
            </div>
          )}
          <div className="mt-1 flex items-center gap-1 text-[11px] text-muted-foreground" title={fmtFechaCO(conv.escalada_en)}>
            <Clock className="size-3" aria-hidden="true" /> {haceCuanto(conv.escalada_en)}
          </div>
        </div>

        <Button
          size="sm" variant="outline" onClick={resolver} disabled={resolviendo}
          className="shrink-0 self-start sm:self-auto"
          aria-label={`Resolver conversación ${conv.cliente_telefono}`}
        >
          <Bot className="size-3.5" /> {resolviendo ? 'Resolviendo…' : 'Resolver / Devolver al bot'}
        </Button>
      </Card>
    </li>
  )
}
