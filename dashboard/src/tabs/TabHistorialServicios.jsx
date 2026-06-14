/*
 * TabHistorialServicios — historial vertical-aware para la familia "atención a cliente" (ADR 0018).
 *
 * La familia POS tiene su /historial de ventas (TabHistorial); la de servicios no tenía vista de
 * historial, solo el estado en vivo. Aquí componemos el pasado a partir de lo que el agente ya
 * registra, sin inventar endpoints y eligiendo el contenido por vertical (como TabInicioAgente):
 *   - pack_pedidos (restaurante) → pedidos en estado FINAL (entregado/cancelado): GET /pedidos
 *     filtrado por esos estados; el rango de fechas se aplica client-side sobre `creado_en`.
 *   - pack_reservas (hotel)      → reservas (citas sobre recursos `habitacion`) con check-out ya
 *     pasado (diaCO(fin) < hoy) o canceladas: GET /agenda/citas en ventana amplia + GET /agenda/recursos.
 *   - pack_agenda sin reservas (barbería/clínica) → citas pasadas en estado final
 *     (cumplida/cancelada/no_show): GET /agenda/citas?desde&hasta + GET /agenda/servicios.
 *
 * Filtros comunes: rango de fechas (default últimos 30 días) y chips de estado (derivados de los datos
 * presentes). Todas las fechas en hora Colombia (regla #4). El gating de la ruta vive en lib/features
 * (/historial visible para ambas familias); App elige este componente vs. TabHistorial por familia.
 */
import { useState } from 'react'
import {
  ChevronDown, ChevronRight, MessageCircle, Monitor,
  ClipboardList, CalendarClock, BedDouble,
} from 'lucide-react'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { EstadoBadge, fmtHora, diaCO, hoyCO, masDiasCO, sumarDias } from './agenda/util.jsx'

// Una reserva dura ≤30 noches: para captar check-outs en el rango pedimos check-ins hasta 30 días antes
// (el endpoint /agenda/citas filtra por `inicio`/check-in; el bucket de historial es el check-out).
const VENTANA_RESERVAS_DIAS = 30

// Estados FINALES por vertical (orden canónico de los chips).
const PEDIDOS_FINALES = ['entregado', 'cancelado']
const CITAS_FINALES = ['cumplida', 'cancelada', 'no_show']
const RESERVAS_ORDEN = ['cumplida', 'confirmada', 'pendiente', 'no_show', 'cancelada']

const arr = (d) => (Array.isArray(d) ? d : [])
const enRango = (ymd, desde, hasta) => ymd >= desde && ymd <= hasta   // YYYY-MM-DD comparable como texto

// Día + hora en Colombia: '12 jun 14:00'. Para columnas fecha/hora de pedidos y citas.
function fmtFechaHora(iso) {
  return `${fmtDia(iso)} ${fmtHora(iso)}`
}
// Solo la fecha (día + mes) en Colombia: '12 jun'. Para check-in → check-out de reservas.
function fmtDia(iso) {
  return new Date(iso).toLocaleDateString('es-CO', {
    timeZone: 'America/Bogota', day: '2-digit', month: 'short',
  })
}

export default function TabHistorialServicios() {
  const features = useFeatures()
  // Prioridad de contenido por vertical (espeja resolveHomePath): pedidos → reservas → citas.
  const vertical = features.includes('pack_pedidos')
    ? 'pedidos'
    : features.includes('pack_reservas')
      ? 'reservas'
      : 'citas'

  const [desde, setDesde] = useState(() => masDiasCO(-30))
  const [hasta, setHasta] = useState(() => hoyCO())
  const [estado, setEstado] = useState('todos')

  const titulo = {
    pedidos: 'Historial de pedidos',
    reservas: 'Historial de reservas',
    citas: 'Historial de citas',
  }[vertical]

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">{titulo}</h1>
        <p className="text-xs text-muted-foreground mt-0.5 capitalize">
          {new Date().toLocaleDateString('es-CO', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'America/Bogota' })}
        </p>
      </header>

      <Card className="p-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          Desde
          <Input type="date" value={desde} max={hasta} onChange={(e) => setDesde(e.target.value)} aria-label="Desde" className="h-9 w-40" />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-muted-foreground">
          Hasta
          <Input type="date" value={hasta} min={desde} onChange={(e) => setHasta(e.target.value)} aria-label="Hasta" className="h-9 w-40" />
        </label>
      </Card>

      {vertical === 'pedidos' && (
        <HistorialPedidos desde={desde} hasta={hasta} estado={estado} onEstado={setEstado} />
      )}
      {vertical === 'reservas' && (
        <HistorialReservas desde={desde} hasta={hasta} estado={estado} onEstado={setEstado} />
      )}
      {vertical === 'citas' && (
        <HistorialCitas desde={desde} hasta={hasta} estado={estado} onEstado={setEstado} />
      )}
    </div>
  )
}

// ── Filtros / piezas compartidas ──────────────────────────────────────────────
const CHIP_LABEL = {
  todos: 'Todos',
  entregado: 'Entregados', cancelado: 'Cancelados',
  cumplida: 'Cumplidas', confirmada: 'Confirmadas', pendiente: 'Pendientes',
  cancelada: 'Canceladas', no_show: 'No asistió',
}

function estadosPresentes(rows, orden) {
  const set = new Set(rows.map((r) => r.estado))
  return orden.filter((e) => set.has(e))
}

function ChipsEstado({ estados, estado, onEstado, total }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {['todos', ...estados].map((op) => (
        <button
          key={op} type="button" onClick={() => onEstado(op)} aria-pressed={estado === op}
          className={`px-2.5 py-1 rounded-full text-[12px] font-medium border transition-colors ${estado === op ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground hover:text-foreground'}`}
        >
          {CHIP_LABEL[op] || op}
        </button>
      ))}
      <span className="ml-auto text-[12px] text-muted-foreground tabular-nums">
        {total} {total === 1 ? 'resultado' : 'resultados'}
      </span>
    </div>
  )
}

function OrigenIcon({ origen }) {
  return origen === 'whatsapp'
    ? <MessageCircle className="size-3 shrink-0 text-success" aria-label="Por WhatsApp" />
    : <Monitor className="size-3 shrink-0 text-muted-foreground" aria-label="Por dashboard" />
}

// Estado de pedido: sus valores (entregado/cancelado) no están en el mapa de citas, así que llevan su
// propio badge; citas y reservas reusan EstadoBadge (sus estados sí están en agenda/util).
const ESTADO_PEDIDO = {
  entregado: { label: 'Entregado', clase: 'bg-success/15 text-success' },
  cancelado: { label: 'Cancelado', clase: 'bg-surface-2 text-muted-foreground' },
}
function EstadoPedidoBadge({ estado }) {
  const e = ESTADO_PEDIDO[estado]
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold shrink-0 ${e?.clase || 'bg-surface-2 text-muted-foreground'}`}>
      {e?.label || estado}
    </span>
  )
}

function Contenedor({ loading, vacioIcon: Icono, vacioTexto, children, hayFilas }) {
  return (
    <Card className="p-0 overflow-hidden">
      {loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : !hayFilas ? (
        <div className="py-12 flex flex-col items-center gap-2 text-muted-foreground">
          <Icono className="size-5 opacity-60" aria-hidden="true" />
          <p className="text-sm">{vacioTexto}</p>
        </div>
      ) : (
        <ul className="divide-y divide-border-subtle">{children}</ul>
      )}
    </Card>
  )
}

// ── Pedidos (restaurante) ─────────────────────────────────────────────────────
function HistorialPedidos({ desde, hasta, estado, onEstado }) {
  // El endpoint filtra por estado final; reforzamos client-side (defaults seguros) y acotamos por fecha.
  const q = useFetch('/pedidos?estado=entregado&estado=cancelado')
  const enVentana = arr(q.data)
    .filter((p) => PEDIDOS_FINALES.includes(p.estado) && enRango(diaCO(p.creado_en), desde, hasta))
  const estados = estadosPresentes(enVentana, PEDIDOS_FINALES)
  const filtrados = (estado === 'todos' ? enVentana : enVentana.filter((p) => p.estado === estado))
    .sort((a, b) => (a.creado_en < b.creado_en ? 1 : -1))

  return (
    <div className="space-y-3">
      <ChipsEstado estados={estados} estado={estado} onEstado={onEstado} total={filtrados.length} />
      <Contenedor loading={q.loading} hayFilas={filtrados.length > 0}
        vacioIcon={ClipboardList} vacioTexto="Sin pedidos en el rango.">
        {filtrados.map((p) => <FilaPedido key={p.id} p={p} />)}
      </Contenedor>
    </div>
  )
}

function FilaPedido({ p }) {
  const [abierto, setAbierto] = useState(false)
  const items = arr(p.items)
  return (
    <li>
      <button
        type="button" onClick={() => setAbierto((o) => !o)}
        aria-label={`Pedido ${p.id}`}
        className="w-full flex items-center gap-3 px-3.5 py-2 text-left hover:bg-surface-2 transition-colors"
      >
        {abierto ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
          : <ChevronRight className="size-4 shrink-0 text-muted-foreground" />}
        <span className="text-[11px] text-muted-foreground tabular-nums w-24 shrink-0">{fmtFechaHora(p.creado_en)}</span>
        <span className="text-[13px] font-medium truncate flex-1 inline-flex items-center gap-1.5">
          <OrigenIcon origen={p.origen} />
          <span className="truncate">{p.cliente_nombre || p.cliente_telefono}</span>
        </span>
        <span className="text-[12px] text-muted-foreground shrink-0 hidden sm:inline">
          {items.length} {items.length === 1 ? 'ítem' : 'ítems'}
        </span>
        <span className="text-[13px] font-semibold tabular-nums w-24 text-right shrink-0">{cop(p.total)}</span>
        <EstadoPedidoBadge estado={p.estado} />
      </button>
      {abierto && (
        <div className="px-9 py-2.5 bg-surface-2/40 border-t border-border-subtle">
          <ul className="space-y-1 text-[12px]">
            {items.map((i) => (
              <li key={i.id} className="flex items-center justify-between gap-2">
                <span className="truncate">{num(i.cantidad)}× {i.nombre}</span>
                <span className="tabular-nums text-muted-foreground shrink-0">{cop(i.subtotal)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  )
}

// ── Citas (barbería / clínica) ────────────────────────────────────────────────
function HistorialCitas({ desde, hasta, estado, onEstado }) {
  // El backend acota por rango (filtra por `inicio`); aquí solo nos quedamos con los estados finales.
  const citasQ = useFetch(`/agenda/citas?desde=${desde}&hasta=${hasta}`)
  const serviciosQ = useFetch('/agenda/servicios')
  const nombreServicio = Object.fromEntries(arr(serviciosQ.data).map((s) => [s.id, s.nombre]))

  const finales = arr(citasQ.data).filter((c) => CITAS_FINALES.includes(c.estado))
  const estados = estadosPresentes(finales, CITAS_FINALES)
  const filtradas = (estado === 'todos' ? finales : finales.filter((c) => c.estado === estado))
    .sort((a, b) => (a.inicio < b.inicio ? 1 : -1))

  return (
    <div className="space-y-3">
      <ChipsEstado estados={estados} estado={estado} onEstado={onEstado} total={filtradas.length} />
      <Contenedor loading={citasQ.loading} hayFilas={filtradas.length > 0}
        vacioIcon={CalendarClock} vacioTexto="Sin citas en el rango.">
        {filtradas.map((c) => (
          <li key={c.id} className="flex items-center gap-3 px-3.5 py-2">
            <span className="text-[11px] text-muted-foreground tabular-nums w-24 shrink-0">{fmtFechaHora(c.inicio)}</span>
            <span className="text-[13px] font-medium truncate flex-1 inline-flex items-center gap-1.5">
              <OrigenIcon origen={c.origen} />
              <span className="truncate">{c.cliente_nombre}</span>
            </span>
            <span className="text-[12px] text-muted-foreground truncate w-36 shrink-0 hidden sm:block">
              {nombreServicio[c.servicio_id] || `Servicio #${c.servicio_id}`}
            </span>
            <EstadoBadge estado={c.estado} />
          </li>
        ))}
      </Contenedor>
    </div>
  )
}

// ── Reservas (hotel) ──────────────────────────────────────────────────────────
function HistorialReservas({ desde, hasta, estado, onEstado }) {
  // Ventana amplia hacia atrás para captar check-ins previos cuyo check-out cae en el rango.
  const citasQ = useFetch(`/agenda/citas?desde=${sumarDias(desde, -VENTANA_RESERVAS_DIAS)}&hasta=${hasta}`)
  const recursosQ = useFetch('/agenda/recursos')
  const nombreHabitacion = Object.fromEntries(
    arr(recursosQ.data).filter((r) => r.tipo === 'habitacion').map((r) => [r.id, r.nombre]),
  )

  const hoy = hoyCO()
  const reservas = arr(citasQ.data)
    .filter((c) => nombreHabitacion[c.recurso_id] != null)          // solo recursos `habitacion`
    .filter((c) => diaCO(c.fin) < hoy || c.estado === 'cancelada')  // check-out pasado o cancelada
    .filter((c) => enRango(diaCO(c.fin), desde, hasta))             // el check-out cae en el rango
  const estados = estadosPresentes(reservas, RESERVAS_ORDEN)
  const filtradas = (estado === 'todos' ? reservas : reservas.filter((c) => c.estado === estado))
    .sort((a, b) => (a.fin < b.fin ? 1 : -1))

  return (
    <div className="space-y-3">
      <ChipsEstado estados={estados} estado={estado} onEstado={onEstado} total={filtradas.length} />
      <Contenedor loading={citasQ.loading || recursosQ.loading} hayFilas={filtradas.length > 0}
        vacioIcon={BedDouble} vacioTexto="Sin reservas en el rango.">
        {filtradas.map((c) => (
          <li key={c.id} className="flex items-center gap-3 px-3.5 py-2">
            <span className="text-[12px] tabular-nums w-32 shrink-0 inline-flex items-center gap-1">
              <span className="font-medium">{fmtDia(c.inicio)}</span>
              <span className="text-muted-foreground">→</span>
              <span className="font-medium">{fmtDia(c.fin)}</span>
            </span>
            <span className="text-[13px] font-medium truncate flex-1 inline-flex items-center gap-1.5">
              <OrigenIcon origen={c.origen} />
              <span className="truncate">{c.cliente_nombre}</span>
            </span>
            <span className="text-[12px] text-muted-foreground truncate w-32 shrink-0 hidden sm:block">
              {nombreHabitacion[c.recurso_id] || `Habitación #${c.recurso_id}`}
            </span>
            <EstadoBadge estado={c.estado} />
          </li>
        ))}
      </Contenedor>
    </div>
  )
}
