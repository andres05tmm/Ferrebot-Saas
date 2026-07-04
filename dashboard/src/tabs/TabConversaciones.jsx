/*
 * TabConversaciones — INBOX con hand-off bidireccional (Fase 2; modelo Chatwoot sobre el estado
 * bot/humano del backend). Capacidad TRANSVERSAL del canal: se oculta sin la feature 'canal_whatsapp'.
 *
 * Dos columnas: la lista de conversaciones (GET /conversaciones: teléfono, último mensaje, estado,
 * "hace cuánto") con búsqueda y filtro; y el hilo del cliente seleccionado (GET
 * /conversaciones/{id}/mensajes) con burbujas diferenciadas por autor (cliente/bot/asesor) y un
 * composer para responder COMO HUMANO. El composer solo está activo si la conversación está en
 * `humano` (botón "Tomar" si está con el bot) y dentro de la ventana de 24h de WhatsApp (texto libre);
 * fuera de la ventana se deshabilita y se avisa. Tiempo real por SSE (conversacion_mensaje/escalada/
 * resuelta) refresca lista e hilo. Todas las horas en zona Colombia (regla #4).
 */
import { useEffect, useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Bot, Headset, Phone, Search, Send, User, UserCheck, Inbox } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, Spinner, ErrorMsg } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import { cn } from '@/lib/utils'

const VENTANA_24H_MS = 24 * 60 * 60 * 1000

/** ISO con offset → 'vie 12/06 14:00' legible (hora Colombia, regla #4). */
export function fmtFechaCO(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('es-CO', {
    timeZone: 'America/Bogota', weekday: 'short', day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** 'HH:MM' en hora Colombia (para la hora de cada burbuja). */
export function fmtHora(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString('es-CO', {
    timeZone: 'America/Bogota', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

/** "hace cuánto" relativo a ahora (instante en ISO con offset). */
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

/** ¿Sigue abierta la ventana de 24h? (último mensaje ENTRANTE del cliente hace < 24h). */
export function ventanaAbierta(mensajes, ahora = Date.now()) {
  const entrantes = (mensajes || []).filter(m => m.direccion === 'entrante')
  if (entrantes.length === 0) return false
  const ultimo = entrantes[entrantes.length - 1]
  return ahora - new Date(ultimo.creada_en).getTime() < VENTANA_24H_MS
}

const FILTROS = [
  { id: 'todas', label: 'Todas' },
  { id: 'humano', label: 'En humano' },
  { id: 'bot', label: 'Con bot' },
]

export default function TabConversaciones() {
  const { refreshKey } = useOutletContext() ?? {}
  const [seleccionId, setSeleccionId] = useState(null)
  const [filtro, setFiltro] = useState('todas')
  const [busqueda, setBusqueda] = useState('')

  const inboxQ = useFetch('/conversaciones', [refreshKey])
  const conversaciones = useMemo(() => (Array.isArray(inboxQ.data) ? inboxQ.data : []), [inboxQ.data])

  // Hilo de la conversación seleccionada (sin selección → useFetch en reposo).
  const mensajesQ = useFetch(seleccionId ? `/conversaciones/${seleccionId}/mensajes` : null, [refreshKey])

  const seleccion = conversaciones.find(c => c.id === seleccionId) || null

  // Realtime: refresca la lista siempre; el hilo solo si el evento es del cliente abierto.
  useRealtimeEvent(
    ['conversacion_mensaje', 'conversacion_escalada', 'conversacion_resuelta', 'reconnected'],
    (_tipo, data) => {
      inboxQ.refetch()
      if (seleccion && (!data?.cliente_telefono || data.cliente_telefono === seleccion.cliente_telefono)) {
        mensajesQ.refetch()
      }
    },
  )

  const visibles = useMemo(() => {
    const q = busqueda.trim().toLowerCase()
    return conversaciones.filter(c => {
      if (filtro !== 'todas' && c.estado !== filtro) return false
      if (q && !c.cliente_telefono.toLowerCase().includes(q)) return false
      return true
    })
  }, [conversaciones, filtro, busqueda])

  return (
    <div className="space-y-3">
      <Encabezado total={conversaciones.filter(c => c.estado === 'humano').length} />

      {inboxQ.loading && inboxQ.data === null ? (
        <Spinner />
      ) : inboxQ.error ? (
        <ErrorMsg msg="No se pudieron cargar las conversaciones." />
      ) : (
        <Card className="grid grid-cols-1 md:grid-cols-[320px_1fr] overflow-hidden p-0 h-[600px] shadow-sm">
          <ListaConversaciones
            conversaciones={visibles}
            seleccionId={seleccionId}
            onSeleccionar={setSeleccionId}
            filtro={filtro} onFiltro={setFiltro}
            busqueda={busqueda} onBusqueda={setBusqueda}
          />
          <Hilo
            conversacion={seleccion}
            mensajes={Array.isArray(mensajesQ.data) ? mensajesQ.data : []}
            loading={mensajesQ.loading}
            onCambio={() => { inboxQ.refetch(); mensajesQ.refetch() }}
          />
        </Card>
      )}
    </div>
  )
}

function Encabezado({ total }) {
  return (
    <div className="flex items-center gap-2">
      <h2 className="text-sm font-semibold inline-flex items-center gap-1.5">
        <Headset className="size-4 text-primary" /> Inbox de conversaciones
      </h2>
      {total > 0 && (
        <Badge variant="primary" aria-label={`${total} esperando humano`}>
          {total} esperando humano
        </Badge>
      )}
    </div>
  )
}

// ── Columna izquierda: lista + búsqueda + filtro ─────────────────────────────
function ListaConversaciones({ conversaciones, seleccionId, onSeleccionar, filtro, onFiltro, busqueda, onBusqueda }) {
  return (
    <div className="border-b md:border-b-0 md:border-r border-border flex flex-col min-h-0">
      <div className="p-2.5 border-b border-border space-y-2">
        <div className="relative">
          <Search className="size-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input
            value={busqueda} onChange={e => onBusqueda(e.target.value)}
            placeholder="Buscar teléfono…" aria-label="Buscar conversación" className="h-9 pl-8 text-sm"
          />
        </div>
        <div className="inline-flex items-center gap-1 rounded-md bg-surface-2 p-0.5">
          {FILTROS.map(f => (
            <button key={f.id} onClick={() => onFiltro(f.id)} aria-pressed={filtro === f.id}
              className={cn('rounded-sm px-2.5 h-7 text-xs font-medium transition-colors',
                filtro === f.id ? 'bg-surface text-foreground shadow-xs' : 'text-muted-foreground hover:text-foreground')}>
              {f.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {conversaciones.length === 0 ? (
          <p className="py-10 px-4 text-center text-sm text-muted-foreground">Sin conversaciones.</p>
        ) : (
          <ul>
            {conversaciones.map(c => (
              <ConversacionItem
                key={c.id} conv={c} activa={c.id === seleccionId}
                onClick={() => onSeleccionar(c.id)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function ConversacionItem({ conv, activa, onClick }) {
  const enHumano = conv.estado === 'humano'
  return (
    <li>
      <button onClick={onClick}
        className={cn('w-full text-left flex items-start gap-2.5 px-3 py-2.5 border-b border-border-subtle transition-colors',
          activa ? 'bg-primary-soft' : 'hover:bg-surface-2')}>
        <span className="grid place-items-center size-9 rounded-full bg-primary/15 text-primary text-xs font-bold shrink-0">
          {conv.cliente_telefono.slice(-2)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-[13px] font-semibold truncate tabular-nums">{conv.cliente_telefono}</span>
            <span className="ml-auto text-[10.5px] text-muted-foreground shrink-0">{haceCuanto(conv.ultimo_en || conv.escalada_en || conv.creada_en)}</span>
          </div>
          <p className="text-[12px] text-muted-foreground truncate mt-0.5">{conv.ultimo_texto || conv.motivo || 'Sin mensajes aún'}</p>
          <EstadoBadge estado={conv.estado} enHumano={enHumano} />
        </div>
      </button>
    </li>
  )
}

function EstadoBadge({ estado, enHumano }) {
  return (
    <span className={cn('inline-flex items-center gap-1 mt-1 text-[9.5px] font-bold rounded-full px-2 py-0.5',
      enHumano ? 'bg-warning/15 text-warning' : 'bg-primary/10 text-primary')}>
      {enHumano ? <><Headset className="size-2.5" /> Necesita humano</> : <><Bot className="size-2.5" /> Con el agente</>}
    </span>
  )
}

// ── Panel derecho: hilo + composer ───────────────────────────────────────────
function Hilo({ conversacion, mensajes, loading, onCambio }) {
  if (!conversacion) {
    return (
      <div className="hidden md:grid place-items-center text-center p-8 bg-surface-2/30">
        <div>
          <Inbox className="size-7 mx-auto mb-2 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm font-medium">Elige una conversación</p>
          <p className="text-xs text-muted-foreground mt-1">Verás el hilo y podrás intervenir como humano.</p>
        </div>
      </div>
    )
  }

  const enHumano = conversacion.estado === 'humano'
  const abierta = ventanaAbierta(mensajes)

  return (
    <div className="flex flex-col min-h-0 bg-surface-2/30">
      <CabeceraHilo conversacion={conversacion} enHumano={enHumano} onCambio={onCambio} />
      <BannerEstado enHumano={enHumano} abierta={abierta} />

      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-2">
        {loading ? (
          <p className="m-auto text-sm text-muted-foreground">Cargando hilo…</p>
        ) : mensajes.length === 0 ? (
          <p className="m-auto text-sm text-muted-foreground">Sin mensajes en esta conversación.</p>
        ) : (
          mensajes.map(m => <Burbuja key={m.id} mensaje={m} />)
        )}
      </div>

      <Composer
        conversacionId={conversacion.id} habilitado={enHumano && abierta}
        enHumano={enHumano} abierta={abierta} onEnviado={onCambio}
      />
    </div>
  )
}

function CabeceraHilo({ conversacion, enHumano, onCambio }) {
  const [ocupado, setOcupado] = useState(false)

  async function accion(path, okMsg) {
    setOcupado(true)
    try {
      const res = await api(path, { method: 'POST' })
      if (res.ok) { toast.success(okMsg); onCambio() }
      else toast.error('No se pudo completar la acción')
    } catch { toast.error('Error de conexión') } finally { setOcupado(false) }
  }

  return (
    <div className="flex items-center gap-2.5 px-4 py-2.5 border-b border-border bg-surface">
      <span className="grid place-items-center size-9 rounded-full bg-primary/15 text-primary text-xs font-bold shrink-0">
        {conversacion.cliente_telefono.slice(-2)}
      </span>
      <div className="min-w-0">
        <div className="text-sm font-semibold inline-flex items-center gap-1.5">
          <Phone className="size-3.5 text-muted-foreground" aria-hidden="true" />
          <span className="tabular-nums truncate">{conversacion.cliente_telefono}</span>
        </div>
        {conversacion.motivo && <div className="text-[11px] text-muted-foreground truncate">{conversacion.motivo}</div>}
      </div>
      <div className="ml-auto shrink-0">
        {enHumano ? (
          <Button size="sm" variant="outline" disabled={ocupado}
            aria-label="Devolver al bot"
            onClick={() => accion(`/conversaciones/${conversacion.id}/resolver`, 'Conversación devuelta al bot')}>
            <Bot className="size-3.5" /> Devolver al bot
          </Button>
        ) : (
          <Button size="sm" disabled={ocupado}
            aria-label="Tomar conversación"
            onClick={() => accion(`/conversaciones/${conversacion.id}/tomar`, 'Tomaste la conversación')}>
            <UserCheck className="size-3.5" /> Tomar conversación
          </Button>
        )}
      </div>
    </div>
  )
}

function BannerEstado({ enHumano, abierta }) {
  if (!enHumano) {
    return (
      <div className="px-4 py-1.5 text-[11.5px] text-center bg-primary/5 text-primary border-b border-border">
        Bot activo — el agente está atendiendo a este cliente.
      </div>
    )
  }
  if (!abierta) {
    return (
      <div className="px-4 py-1.5 text-[11.5px] text-center bg-warning/10 text-warning border-b border-border">
        Bot en pausa · Fuera de la ventana de 24h — solo puedes contactarlo con una plantilla.
      </div>
    )
  }
  return (
    <div className="px-4 py-1.5 text-[11.5px] text-center bg-warning/10 text-warning border-b border-border">
      Bot en pausa — estás atendiendo tú. Devuélvelo al bot cuando termines.
    </div>
  )
}

const AUTOR_META = {
  cliente: { label: 'Cliente', icon: User },
  bot: { label: 'Agente', icon: Bot },
  asesor: { label: 'Tú (asesor)', icon: UserCheck },
}

function Burbuja({ mensaje }) {
  const entrante = mensaje.direccion === 'entrante'
  const meta = AUTOR_META[mensaje.autor] || AUTOR_META.cliente
  const Icon = meta.icon
  return (
    <div className={cn('max-w-[75%] rounded-2xl px-3.5 py-2 text-[13px] leading-snug',
      entrante
        ? 'self-start bg-surface border border-border rounded-bl-sm'
        : 'self-end bg-primary-soft text-foreground rounded-br-sm')}>
      {!entrante && (
        <div className="text-[10px] font-bold text-primary mb-0.5 inline-flex items-center gap-1">
          <Icon className="size-2.5" aria-hidden="true" /> {meta.label}
        </div>
      )}
      <div className="whitespace-pre-wrap break-words">{mensaje.texto}</div>
      <div className={cn('text-[9.5px] mt-0.5 tabular-nums', entrante ? 'text-muted-foreground' : 'text-primary/70')}>
        {fmtHora(mensaje.creada_en)}
      </div>
    </div>
  )
}

function Composer({ conversacionId, habilitado, enHumano, abierta, onEnviado }) {
  const [texto, setTexto] = useState('')
  const [enviando, setEnviando] = useState(false)

  const placeholder = !enHumano
    ? 'Toma la conversación para escribir…'
    : !abierta
      ? 'Fuera de la ventana de 24h — solo plantillas'
      : 'Escribe para intervenir como humano…'

  async function enviar() {
    const limpio = texto.trim()
    if (!limpio || enviando) return
    setEnviando(true)
    try {
      const res = await api(`/conversaciones/${conversacionId}/responder`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texto: limpio }),
      })
      if (res.ok) { setTexto(''); onEnviado() }
      else if (res.status === 409) toast.error('La conversación no está en modo humano: tómala primero')
      else toast.error('No se pudo enviar el mensaje')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <form
      className="flex items-center gap-2 p-3 border-t border-border bg-surface"
      onSubmit={(e) => { e.preventDefault(); enviar() }}
    >
      <Input
        value={texto} onChange={e => setTexto(e.target.value)}
        disabled={!habilitado || enviando} placeholder={placeholder}
        aria-label="Mensaje para el cliente" className="h-10 flex-1"
      />
      <Button type="submit" disabled={!habilitado || enviando || !texto.trim()} aria-label="Enviar mensaje" className="h-10">
        <Send className="size-4" /> {enviando ? 'Enviando…' : 'Enviar'}
      </Button>
    </form>
  )
}
