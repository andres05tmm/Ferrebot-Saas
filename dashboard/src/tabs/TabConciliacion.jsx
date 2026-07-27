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

const FILTROS = [
  { id: '', label: 'Todos' },
  { id: 'no_conciliado', label: 'Sin conciliar' },
  { id: 'sugerido', label: 'Sugeridos' },
  { id: 'conciliado', label: 'Conciliados' },
  { id: 'descartado', label: 'No son ventas' },
]

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
  const detalle = [fechaCorta(m.fecha), m.hora, m.cuenta_destino, m.tipo_transaccion]
    .filter(Boolean).join(' · ')

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
          {candidatos.map(cand => (
            <div key={`${cand.tipo}-${cand.id}`} className="flex items-center gap-2">
              <span className="text-meta text-muted-foreground flex-1 truncate">
                {/* La descripción ya dice qué es ("venta #77", "abono al fiado #3"); el tipo crudo
                    solo aparece si el backend no mandó ninguna. */}
                {cand.descripcion || cand.tipo} · {fechaCorta(cand.fecha)} · {cop(cand.monto)}
                {cand.cliente ? ` · ${cand.cliente}` : ''}
              </span>
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

/* Cuánta plata entró en el período. Tres cifras y no una: `total` es lo que llegó a las cuentas,
 * `total_negocio` descuenta lo que el dueño marcó como plata de la casa, y la diferencia es
 * justamente lo personal — mostrar solo una escondería que a esas cuentas entra de todo. Los
 * egresos no van: se mezclan con gastos personales (decisión del dueño). */
function Totales({ datos, cargando }) {
  if (cargando || !datos) {
    return (
      <div className="grid grid-cols-3 gap-2">
        {Array.from({ length: 3 }, (_, i) => <Card key={i} className="p-3 h-[74px] animate-pulse" />)}
      </div>
    )
  }
  const cuentas = arr(datos.por_cuenta)
  const cifras = [
    ['Entró', datos.total, 'text-foreground'],
    ['Del negocio', datos.total_negocio, 'text-success'],
    ['Personal', datos.total_personal, 'text-muted-foreground'],
  ]
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-2">
        {cifras.map(([titulo, valor, tono]) => (
          <Card key={titulo} className="p-3">
            <div className="text-caption text-muted-foreground">{titulo}</div>
            <div className={`text-lg font-semibold tabular-nums truncate ${tono}`}>{cop(valor)}</div>
          </Card>
        ))}
      </div>
      {cuentas.length > 0 && (
        <Card className="p-0 overflow-hidden">
          <ul className="divide-y divide-border-subtle">
            {cuentas.map(c => (
              <li key={c.cuenta || 'sin-cuenta'} className="px-3.5 py-2 flex items-center gap-3 text-body-sm">
                <div className="min-w-0 flex-1">
                  {/* Sin alias configurado se muestra el número; sin cuenta, que el parser no la leyó. */}
                  <div className="font-medium truncate">{c.alias || c.cuenta || 'Cuenta sin identificar'}</div>
                  <div className="text-caption text-muted-foreground">
                    {c.alias && c.cuenta ? `${c.cuenta} · ` : ''}{c.movimientos} movimiento(s)
                  </div>
                </div>
                <span className="tabular-nums font-semibold shrink-0">{cop(c.total)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {datos.sin_clasificar > 0 && (
        <p className="text-caption text-muted-foreground px-1">
          {datos.sin_clasificar} movimiento(s) sin resolver: hasta que digas cuáles no son ventas,
          cuentan como plata del negocio.
        </p>
      )}
    </div>
  )
}

/* Quién repite. Colapsado por defecto: es el objetivo terciario, no lo que el dueño viene a hacer.
 * Agrupa por el nombre que trae el correo del banco y NO escribe en `clientes`: ese nombre es texto
 * sin documento ni teléfono, y volcarlo llenaría la tabla de duplicados que después se limpian a mano. */
function Recurrentes({ rango }) {
  const [abierto, setAbierto] = useState(false)
  const q = useRemitentesRecurrentes(rango.desde, rango.hasta, abierto)
  const filas = arr(q.data)

  return (
    <Card className="p-0 overflow-hidden">
      <button type="button" onClick={() => setAbierto(a => !a)} aria-expanded={abierto}
        className="w-full px-3.5 py-2.5 flex items-center gap-2 text-body-sm hover:bg-surface-2 transition-colors">
        <Users className="size-4 text-primary shrink-0" />
        <span className="font-medium flex-1 text-left">Quién repite</span>
        <span className="text-caption text-muted-foreground">{abierto ? 'ocultar' : 'ver'}</span>
      </button>
      {abierto && (
        q.isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : filas.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nadie ha mandado plata más de una vez en este período.
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle border-t border-border-subtle">
            {filas.map(r => (
              <li key={r.nombre} className="px-3.5 py-2 flex items-center gap-3 text-body-sm">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{r.nombre}</div>
                  <div className="text-caption text-muted-foreground">
                    {`${r.veces} transferencias · última ${fechaCorta(r.ultima)}`
                      + (r.conciliados > 0 ? ` · ${r.conciliados} ya enlazadas a una venta` : '')}
                  </div>
                </div>
                <span className="tabular-nums font-semibold shrink-0">{cop(r.total)}</span>
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
  const [sugiriendo, setSugiriendo] = useState(false)
  const qc = useQueryClient()
  const hoy = useMemo(hoyCO, [])
  const rango = useMemo(() => periodo(periodoId, hoy), [periodoId, hoy])
  const verDescartados = filtro === 'descartado'
  const movsQ = useMovimientosBancarios(verDescartados ? '' : filtro, verDescartados)
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

  const todos = arr(movsQ.data)
  const movimientos = verDescartados
    ? todos.filter(i => i.movimiento.descartado_en != null)
    : todos

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

      <Totales datos={totalesQ.data} cargando={totalesQ.isLoading} />

      <Recurrentes rango={rango} />

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
          <p className="py-10 text-center text-sm text-muted-foreground">
            {filtro ? 'Sin movimientos en ese estado.' : 'Todavía no ha entrado ninguna transferencia.'}
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {movimientos.map(item => (
              <Movimiento key={item.movimiento.id} item={item} onConciliar={conciliar} onDescartar={descartar} />
            ))}
          </ul>
        )}
      </Card>

      <p className="text-caption text-muted-foreground px-1">
        El cruce automático solo sugiere los movimientos con un único candidato. Los ambiguos requieren
        que elijas la contraparte a mano. Conciliar solo enlaza el movimiento: no mueve saldos.
      </p>
    </div>
  )
}
