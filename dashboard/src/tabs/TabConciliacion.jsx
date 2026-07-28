/*
 * TabConciliacion — la plata que entra por transferencia (ADR 0028 + 0073). Gateada por
 * 'conciliacion_bancaria', SOLO admin.
 *
 * Cruza los movimientos bancarios —los del extracto Y los que llegan por el correo del banco— con
 * los movimientos internos: ventas por transferencia, la parte transferencia de las mixtas, abonos
 * de fiado, gastos y abonos a proveedores. El match automático (POST /bancos/sugerir) marca
 * 'sugerido' SOLO los de candidato único; los AMBIGUOS exigen que un humano elija antes de conciliar.
 * Nunca concilia solo un ambiguo. Enlazar no toca saldos: solo cruza.
 *
 * A las cuentas del negocio también entra plata personal y de la casa, así que cada movimiento tiene
 * una salida además de "es esta venta": "No es venta" (POST .../descarte) lo saca del pendiente sin
 * borrarlo, y se puede deshacer. Ese botón está SIEMPRE, haya candidatos o no.
 */
import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { useQueryClient } from '@tanstack/react-query'
import { Landmark, Wand2, ArrowDownLeft, ArrowUpRight, Link2, CheckCircle2, XCircle, Undo2, Users } from '@/lib/icons.jsx'
import { cop } from '@/components/shared.jsx'
import { PERIODOS, hoyCO, periodo } from '@/lib/gastos.js'
import {
  useMovimientosBancarios, useTotalesBancarios, useRemitentesRecurrentes, useSugerirConciliacion,
  useConciliar, useDescartarMovimiento, keyPrefix,
} from '@/lib/queries'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

/* Estados del cruce, y nada más. "No son ventas" salió de acá porque no es un estado de la
 * conciliación sino una salida del flujo, y competía con los tres que sí lo son. Los descartados
 * siguen alcanzables: aparecen atenuados dentro de "Todos", que es donde se puede deshacerlos. */
const FILTROS = [
  { id: '', label: 'Todos' },
  { id: 'no_conciliado', label: 'Sin conciliar' },
  { id: 'sugerido', label: 'Sugeridos' },
  { id: 'conciliado', label: 'Conciliados' },
]

// Espeja `CUENTA_SIN_IDENTIFICAR` del backend (modules/bancos/schemas.py).
const SIN_CUENTA = 'sin_cuenta'

const ESTADO_BADGE = {
  no_conciliado: 'bg-muted text-muted-foreground border-border',
  sugerido: 'bg-warning/10 text-warning border-warning/20',
  conciliado: 'bg-success/10 text-success border-success/20',
}
const ESTADO_LABEL = { no_conciliado: 'sin conciliar', sugerido: 'sugerido', conciliado: 'conciliado' }

function fechaCorta(f) {
  if (!f) return '—'
  return new Date(f).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })
}

// Quién mandó la plata. El remitente que trae el correo del banco es lo que el dueño reconoce; la
// referencia del extracto es un código y solo sirve cuando no hay nombre.
function titulo(m) {
  return m.remitente || m.referencia_bancaria || 'movimiento'
}

function Movimiento({ item, onConciliar, onDescartar }) {
  const m = item.movimiento
  const candidatos = arr(item.candidatos)
  const credito = m.naturaleza === 'credito'
  const ambiguo = candidatos.length > 1
  const descartado = m.descartado_en != null
  const conciliado = m.estado_conciliacion === 'conciliado'
  // Sin `cuenta_destino`: a qué cuenta cayó la plata lo dice la lente de arriba, y repetirlo en cada
  // fila gastaba la línea que necesita la hora y el canal. El nombre grande NO es el dueño de la
  // cuenta: es el remitente, o sea quién mandó la plata — el dato más útil de todos.
  const detalle = [fechaCorta(m.fecha), m.hora, m.tipo_transaccion].filter(Boolean).join(' · ')

  return (
    <li className={`px-3.5 py-2.5 space-y-2 text-body-sm ${descartado ? 'opacity-60' : ''}`}>
      <div className="flex items-center gap-3">
        {credito ? <ArrowDownLeft className="size-4 text-success shrink-0" /> : <ArrowUpRight className="size-4 text-destructive shrink-0" />}
        <div className="min-w-0 flex-1">
          <div className="font-medium truncate">{titulo(m)}</div>
          <div className="text-caption text-muted-foreground truncate">{detalle || (credito ? 'entrada' : 'salida')}</div>
        </div>
        <span className={`tabular-nums font-semibold shrink-0 ${credito ? 'text-success' : 'text-destructive'}`}>{cop(m.monto)}</span>
        {descartado ? (
          <Badge variant="outline" className="h-5 text-micro shrink-0 bg-muted text-muted-foreground border-border">
            no es venta
          </Badge>
        ) : (
          <Badge variant="outline" className={`h-5 text-micro shrink-0 ${ESTADO_BADGE[m.estado_conciliacion] || ''}`}>
            {ESTADO_LABEL[m.estado_conciliacion] || m.estado_conciliacion}
          </Badge>
        )}
      </div>

      {descartado ? (
        <div className="ml-7 flex items-center gap-2">
          <span className="text-caption text-muted-foreground flex-1">Marcado como plata que no es del negocio.</span>
          <Button size="sm" variant="ghost" className="h-7 px-2 shrink-0"
            aria-label={`Deshacer no es venta de ${titulo(m)}`}
            onClick={() => onDescartar(m.id, false)}>
            <Undo2 className="size-3.5 mr-1" /> Deshacer
          </Button>
        </div>
      ) : conciliado ? (
        <div className="ml-7 text-caption text-success inline-flex items-center gap-1">
          <CheckCircle2 className="size-3.5" /> enlazado con {m.conciliado_con_tipo} #{m.conciliado_con_id}
        </div>
      ) : (
        <div className="ml-7 space-y-1.5">
          {ambiguo && (
            <div className="text-caption text-warning">Varios candidatos: elige cuál corresponde (no se concilia solo).</div>
          )}
          {candidatos.length === 0 && (
            <div className="text-caption text-muted-foreground">
              Todavía no hay una venta que calce. Anótala y vuelve a correr el cruce.
            </div>
          )}
          {/* Qué se vendió primero, y el consecutivo degradado a rastro. El dueño nunca vio el
              número de la venta: para decidir si ESA es la que le pagaron necesita los productos y
              el cliente. "venta #22 · $10.000" era el mismo dato que ya tenía (el monto) más uno
              que no le dice nada. */}
          {candidatos.map(cand => (
            <div key={`${cand.tipo}-${cand.id}`} className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <div className="text-body-sm truncate">
                  {cand.detalle || cand.descripcion || cand.tipo}
                </div>
                <div className="text-caption text-muted-foreground truncate">
                  {[cand.cliente, cand.detalle ? cand.descripcion : null, fechaCorta(cand.fecha), cop(cand.monto)]
                    .filter(Boolean).join(' · ')}
                </div>
              </div>
              <Button size="sm" variant="ghost" className="h-7 px-2 text-primary shrink-0"
                aria-label={`Conciliar ${m.id} con ${cand.tipo} ${cand.id}`}
                onClick={() => onConciliar(m.id, cand)}>
                <Link2 className="size-3.5 mr-1" /> Es esta venta
              </Button>
            </div>
          ))}
          {/* Siempre visible, haya candidatos o no: a estas cuentas también entra plata de la casa. */}
          <div className="flex">
            <Button size="sm" variant="ghost" className="h-7 px-2 text-muted-foreground"
              aria-label={`Marcar que ${titulo(m)} no es una venta`}
              onClick={() => onDescartar(m.id, true)}>
              <XCircle className="size-3.5 mr-1" /> No es venta
            </Button>
          </div>
        </div>
      )}
    </li>
  )
}

// Etiqueta de una cuenta: el alias que puso la empresa, si no el número, si no que el parser no la leyó.
const nombreCuenta = (c) => c.alias || c.cuenta || 'Sin identificar'
const idCuenta = (c) => c.cuenta || SIN_CUENTA

/* Una sola cifra: lo que entró y ES del negocio, en la lente elegida.
 *
 * Antes eran tres tarjetas ("Entró", "Del negocio", "Personal"). Las otras dos se fueron porque
 * cuánta plata personal pasó por la cuenta no es asunto del negocio, y ponerla al lado del número
 * que sí importa le disputaba la atención al único dato accionable. Lo personal no se pierde: sale
 * del total al marcar "no es venta", que sigue siendo la salida de cada movimiento.
 *
 * Sin tarjeta a propósito: una tarjeta sola alrededor de un número grande es decoración, no
 * jerarquía. El número ya es lo más pesado de la pantalla.
 *
 * `sin_clasificar` se queda: sin él la cifra se lee más firme de lo que es. */
function Cifra({ datos, cargando, cuenta }) {
  if (cargando || !datos) {
    return <div className="h-[68px] w-56 rounded-md bg-surface-2 animate-pulse" />
  }
  const cuentas = arr(datos.por_cuenta)
  // Si a la cuenta elegida no le entró nada en el período, `por_cuenta` ni la trae. Sin este cero
  // explícito, `find` devuelve undefined y la cifra caía al total de TODAS bajo la etiqueta de esa
  // cuenta: el número más equivocado posible, porque se lee como cierto.
  const elegida = cuenta
    ? (cuentas.find(c => idCuenta(c) === cuenta)
       ?? { cuenta: cuenta === SIN_CUENTA ? null : cuenta, movimientos: 0, total_negocio: 0, sin_clasificar: 0 })
    : null
  const monto = elegida ? elegida.total_negocio : datos.total_negocio
  const movimientos = elegida ? elegida.movimientos : cuentas.reduce((s, c) => s + c.movimientos, 0)
  const pendientes = elegida ? elegida.sin_clasificar : datos.sin_clasificar

  return (
    <div>
      <p className="text-caption text-muted-foreground">
        Cobrado por transferencia{elegida ? ` · ${nombreCuenta(elegida)}` : ''}
      </p>
      <p className="text-3xl font-semibold tabular-nums text-success leading-tight">{cop(monto)}</p>
      <p className="text-caption text-muted-foreground">
        {movimientos} movimiento{movimientos === 1 ? '' : 's'}
        {pendientes > 0 && (
          <>
            {' · '}
            <span className="text-warning">
              {pendientes} sin resolver, {pendientes === 1 ? 'cuenta' : 'cuentan'} como del negocio
            </span>
          </>
        )}
      </p>
    </div>
  )
}

/* La lente por cuenta. Reemplaza al desglose que antes era una lista de solo lectura: ver cuánto
 * entró a cada cuenta y poder trabajar una sola cuenta son la misma intención, así que es un
 * control y no un informe. Acota TODO el panel: la cifra, la bandeja y quién repite.
 *
 * Control segmentado y no otra fila de píldoras: el período y el estado ya usan píldoras, y tres
 * filas iguales se leen como una sola cosa. Con una sola cuenta no se muestra: elegir entre una
 * opción no es elegir. */
function LenteCuenta({ cuentas, valor, onCambiar }) {
  if (cuentas.length < 2) return null
  const opciones = [{ id: '', label: 'Todas' }, ...cuentas.map(c => ({ id: idCuenta(c), label: nombreCuenta(c) }))]
  // Cambiar de período puede dejar sin movimientos a la cuenta elegida. Se conserva como opción en
  // vez de dejar el control sin ninguna pestaña activa mientras la lista sigue filtrada por ella.
  if (valor && !opciones.some(o => o.id === valor)) {
    opciones.push({ id: valor, label: valor === SIN_CUENTA ? 'Sin identificar' : valor })
  }
  return (
    <div role="tablist" aria-label="Filtrar por cuenta bancaria"
      className="inline-flex flex-wrap gap-0.5 rounded-lg border border-border bg-surface p-0.5">
      {opciones.map(o => {
        const activa = valor === o.id
        return (
          <button key={o.id || 'todas'} type="button" role="tab" aria-selected={activa}
            onClick={() => onCambiar(o.id)}
            className={`text-meta h-8 px-3 rounded-md transition-colors ${
              activa ? 'bg-primary/10 text-primary font-medium' : 'text-muted-foreground hover:bg-surface-2'
            }`}>
            {o.label}
          </button>
        )
      })}
    </div>
  )
}

/* Quién repite. Colapsado por defecto: es el objetivo terciario, no lo que el dueño viene a hacer.
 * Agrupa por el nombre que trae el correo del banco y NO escribe en `clientes`: ese nombre es texto
 * sin documento ni teléfono, y volcarlo llenaría la tabla de duplicados que después se limpian a mano. */
function Recurrentes({ rango, cuenta }) {
  const [abierto, setAbierto] = useState(false)
  const q = useRemitentesRecurrentes(rango.desde, rango.hasta, abierto, cuenta)
  const filas = arr(q.data)

  return (
    <Card className="p-0 overflow-hidden">
      <button type="button" onClick={() => setAbierto(a => !a)} aria-expanded={abierto}
        className="w-full px-4 py-3 flex items-center gap-2 text-body-sm hover:bg-surface-2 transition-colors">
        <Users className="size-4 text-primary shrink-0" />
        <span className="font-medium flex-1 text-left">Quién repite</span>
        <span className="text-caption text-muted-foreground">{abierto ? 'ocultar' : 'ver'}</span>
      </button>
      {abierto && (
        q.isLoading ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : filas.length === 0 ? (
          <p className="py-8 px-4 text-center text-sm text-muted-foreground">
            Nadie ha mandado plata más de una vez en este período.
          </p>
        ) : (
          /* Cada persona es un bloque vertical, no una fila con el nombre a la izquierda y la plata
           * pegada al borde derecho: con nombres largos esa fila se comprimía y el ojo tenía que
           * cruzar toda la pantalla para juntar quién con cuánto. Ahora se leen de arriba abajo,
           * en el orden en que importan: quién, cuánto, con qué frecuencia. */
          <ul className="divide-y divide-border-subtle border-t border-border-subtle">
            {filas.map(r => (
              <li key={r.nombre} className="px-4 py-3.5 space-y-0.5">
                <div className="font-medium truncate">{r.nombre}</div>
                <div className="text-xl font-semibold tabular-nums leading-tight">{cop(r.total)}</div>
                <div className="text-caption text-muted-foreground">
                  {r.veces} transferencias · última {fechaCorta(r.ultima)}
                </div>
                {r.conciliados > 0 && (
                  <div className="text-caption text-success inline-flex items-center gap-1">
                    <CheckCircle2 className="size-3.5" />
                    {r.conciliados} ya {r.conciliados === 1 ? 'enlazada' : 'enlazadas'} a una venta
                  </div>
                )}
              </li>
            ))}
          </ul>
        )
      )}
    </Card>
  )
}

export default function TabConciliacion() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        La conciliación bancaria es solo para administradores.
      </Card>
    )
  }
  return <ConciliacionContenido />
}

function ConciliacionContenido() {
  const [filtro, setFiltro] = useState('')
  const [periodoId, setPeriodoId] = useState('mes')
  const [cuenta, setCuenta] = useState('')      // '' = todas las cuentas
  const [sugiriendo, setSugiriendo] = useState(false)
  const qc = useQueryClient()
  const hoy = useMemo(hoyCO, [])
  const rango = useMemo(() => periodo(periodoId, hoy), [periodoId, hoy])
  // "Todos" trae también los marcados "no es venta", atenuados. Cuando vivían detrás de su propio
  // filtro, quitarlo habría dejado el botón de deshacer sin ninguna puerta: marcar algo por error
  // era irreversible desde la interfaz.
  const movsQ = useMovimientosBancarios(filtro, filtro === '', cuenta)
  // El período acota los totales, no la lista: la lista es la bandeja de trabajo (lo que falta
  // resolver) y el total es el reporte del mes. Mezclarlos escondería pendientes viejos.
  const totalesQ = useTotalesBancarios(rango.desde, rango.hasta)
  const sugerirM = useSugerirConciliacion()
  const conciliarM = useConciliar()
  const descartarM = useDescartarMovimiento()
  // `transferencia_recibida` ya lo publica el worker al ingerir el correo: el pago aparece en la
  // lista en el mismo momento en que suena Telegram, sin que nadie recargue.
  useRealtimeEvent(
    ['reconnected', 'transferencia_recibida'],
    () => qc.invalidateQueries({ queryKey: keyPrefix.bancos }),
  )
  // Si el pago llegó ANTES que la venta, la transferencia espera sin candidato. El cruce se vuelve a
  // correr solo al abrir el tab y cada vez que se anota una venta, así deja de esperar sin que nadie
  // toque el botón. Callado a propósito: el toast es para el cruce que pide el usuario.
  // La sugerencia solo importa cuando alguien mira; correrla desde aquí evita acoplar ventas↔bancos.
  useEffect(() => { sugerirM.mutate() }, [])   // eslint-disable-line react-hooks/exhaustive-deps
  useRealtimeEvent(['venta_registrada'], () => sugerirM.mutate())

  const movimientos = arr(movsQ.data)

  async function correrSugerencias() {
    setSugiriendo(true)
    try {
      const res = await sugerirM.mutateAsync()
      if (res.ok) {
        const data = await res.json().catch(() => ({}))
        toast.success(`${data.sugeridos ?? 0} movimiento(s) sugerido(s)`)
      } else toast.error('No se pudo correr el match')
    } catch { toast.error('Error de conexión') } finally { setSugiriendo(false) }
  }

  async function conciliar(movId, cand) {
    try {
      const res = await conciliarM.mutateAsync({ movId, tipo: cand.tipo, idInterno: cand.id })
      if (res.ok) toast.success('Movimiento conciliado')
      else if (res.status === 422) toast.error('El enlace no es válido (monto o naturaleza no calzan)')
      else if (res.status === 404) toast.error('El movimiento ya no existe')
      else toast.error('No se pudo conciliar')
    } catch { toast.error('Error de conexión') }
  }

  async function descartar(movId, marcar) {
    try {
      const res = await descartarM.mutateAsync({ movId, descartar: marcar })
      if (res.ok) toast.success(marcar ? 'Marcado como plata que no es del negocio' : 'Vuelve al pendiente')
      else if (res.status === 422) toast.error('Ya está enlazado a una venta: desconcílialo primero')
      else if (res.status === 404) toast.error('El movimiento ya no existe')
      else toast.error('No se pudo marcar')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h1 className="text-base font-semibold inline-flex items-center gap-2">
          <Landmark className="size-4.5 text-primary" /> Conciliación bancaria
        </h1>
        <Button size="sm" className="ml-auto inline-flex items-center gap-1.5" disabled={sugiriendo}
          onClick={correrSugerencias}>
          <Wand2 className="size-4" /> {sugiriendo ? 'Cruzando…' : 'Correr sugerencias'}
        </Button>
      </div>

      {/* La cifra y el período van juntos: el número no significa nada sin el rango que lo produce,
          y separarlos en dos bloques obligaba a mirar arriba para saber de cuándo hablaba. */}
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 pt-1">
        <Cifra datos={totalesQ.data} cargando={totalesQ.isLoading} cuenta={cuenta} />
        <div className="flex flex-wrap gap-1.5">
          {PERIODOS.map(([id, label]) => (
            <button key={id} type="button" onClick={() => setPeriodoId(id)} aria-pressed={periodoId === id}
              className={`text-meta px-2.5 h-8 rounded-md border transition-colors ${
                periodoId === id ? 'border-primary bg-primary/10 text-primary' : 'bg-surface border-border hover:bg-surface-2'
              }`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <LenteCuenta cuentas={arr(totalesQ.data?.por_cuenta)} valor={cuenta} onCambiar={setCuenta} />

      <div className="flex flex-wrap gap-1.5">
        {FILTROS.map(f => (
          <button key={f.id || 'todos'} onClick={() => setFiltro(f.id)}
            className={`text-meta px-2.5 h-8 rounded-md border transition-colors ${
              filtro === f.id ? 'bg-primary text-primary-foreground border-primary' : 'bg-surface border-border hover:bg-surface-2'
            }`}>
            {f.label}
          </button>
        ))}
      </div>

      <Card className="p-0 overflow-hidden">
        {movsQ.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : movsQ.isError ? (
          <p className="py-10 text-center text-sm text-destructive">No se pudieron cargar los movimientos.</p>
        ) : movimientos.length === 0 ? (
          <p className="py-10 px-4 text-center text-sm text-muted-foreground">
            {filtro ? 'Sin movimientos en ese estado.'
              : cuenta ? 'A esa cuenta no ha entrado ninguna transferencia.'
              : 'Todavía no ha entrado ninguna transferencia.'}
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {movimientos.map(item => (
              <Movimiento key={item.movimiento.id} item={item} onConciliar={conciliar} onDescartar={descartar} />
            ))}
          </ul>
        )}
      </Card>

      {/* Debajo de la bandeja, no encima: el tab existe para resolver movimientos, y este reporte
          se consulta de vez en cuando. Estaba partiendo en dos el camino entre la cifra y el trabajo. */}
      <Recurrentes rango={rango} cuenta={cuenta} />

      <p className="text-caption text-muted-foreground px-1">
        El cruce automático solo sugiere los movimientos con un único candidato. Los ambiguos requieren
        que elijas la contraparte a mano. Conciliar solo enlaza el movimiento: no mueve saldos.
      </p>
    </div>
  )
}
