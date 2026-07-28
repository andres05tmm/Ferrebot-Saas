/*
 * VistaMes — el calendario del mes, con la forma del dashboard viejo: cada celda muestra el día Y
 * el monto abreviado, arriba van los cuatro KPIs del mes y abajo el desglose por método de pago.
 *
 * Se replica ese formato a propósito y no uno nuevo: es el que el dueño lleva meses leyendo, y un
 * heatmap sin cifras lo obligaba a pasar el mouse celda por celda para saber cuánto fue cada día.
 *
 * Dos formas de anotar los días ANTERIORES al sistema (`historico_ventas`):
 *   - tocando la celda de un día pasado sin ventas → escribir el total ahí mismo;
 *   - pegando la lista completa, para el atraso de meses.
 * Los dos caminos van al mismo endpoint idempotente por fecha. Esos días se pintan distinto y NUNCA
 * se suman con lo que registró el sistema: no tienen gastos ni caja detrás, así que mezclarlos daría
 * un mes que ningún reporte financiero puede respaldar.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { ChevronLeft, ChevronRight, CalendarDays, TrendingUp, Trophy, Plus } from '@/lib/icons.jsx'
import { anioMesCO as hoyCO, hoyStrCO } from '@/lib/fechas'
import { api } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import KpiCard from '@/components/KpiCard.jsx'
import CargarDiasViejos from './CargarDiasViejos.jsx'
import { montoCorto } from './montoCorto.js'

const DIAS_SEMANA = ['LUN', 'MAR', 'MIÉ', 'JUE', 'VIE', 'SÁB', 'DOM']
const MESES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
  'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']

// Intensidad 0..4 relativa al mejor día del mes, sobre el color de marca del tenant.
function nivel(total, max) {
  if (!total || !max) return 0
  const r = total / max
  if (r > 0.75) return 4
  if (r > 0.5) return 3
  if (r > 0.25) return 2
  return 1
}
// Escala NEUTRA de tinta sobre la superficie, no el color de marca. Dos razones:
//
// 1. La cifra manda. Con el rojo de marca al 55% detrás, el número de la celda quedaba ilegible —
//    y el número es justamente lo que se vino a recuperar del dashboard viejo. El tope es 20% de
//    tinta: suficiente para leer la intensidad de un vistazo, nunca para tapar lo que dice.
// 2. `foreground` se invierte solo con el tema: tinta oscura sobre claro, clara sobre oscuro. Una
//    escala fija en gris se rompería en modo oscuro.
//
// El único acento de marca que queda en la grilla es el anillo del día de hoy, y por eso resalta.
const NIVEL_CLS = [
  'bg-surface border border-border-subtle',
  'bg-foreground/[0.05] border border-transparent',
  'bg-foreground/[0.09] border border-transparent',
  'bg-foreground/[0.14] border border-transparent',
  'bg-foreground/[0.20] border border-transparent',
]
// Los días anotados a mano: contorno punteado, sin relleno. No compiten con la escala del heatmap
// porque no son la misma clase de dato.
const CLS_HISTORICO = 'bg-surface border border-dashed border-border-strong'

const esHistorico = (d) => Number(d?.historico || 0) > 0 && Number(d?.total || 0) === 0
const montoDia = (d) => Number(d?.total || 0) || Number(d?.historico || 0)

export default function VistaMes() {
  const { isAdmin } = useAuth()
  const admin = isAdmin()
  const actual = hoyCO()
  const [{ anio, mes }, setPeriodo] = useState(actual)
  const [pegando, setPegando] = useState(false)
  const [editando, setEditando] = useState(null)     // fecha ISO del día en edición
  const [valor, setValor] = useState('')
  const [guardando, setGuardando] = useState(false)

  const q = useFetch(`/reportes/calendario?anio=${anio}&mes=${mes}`, [anio, mes])
  const desde = `${anio}-${String(mes).padStart(2, '0')}-01`
  const hasta = `${anio}-${String(mes).padStart(2, '0')}-${new Date(Date.UTC(anio, mes, 0)).getUTCDate()}`
  const resumenQ = useFetch(`/reportes/resumen?desde=${desde}&hasta=${hasta}`, [anio, mes])
  useRealtimeEvent(['venta_registrada', 'venta_anulada', 'venta_editada', 'reconnected'], q.refetch)

  const dias = Array.isArray(q.data) ? q.data : []
  const porFecha = useMemo(() => Object.fromEntries(dias.map(d => [d.fecha, d])), [dias])
  // El máximo de la escala sale SOLO de las ventas del sistema. Los días anotados a mano no se
  // pintan con intensidad (van con contorno punteado), así que meterlos acá solo lograba que un día
  // viejo grande aplastara el contraste entre los días reales.
  const max = useMemo(() => Math.max(0, ...dias.map(d => Number(d.total || 0))), [dias])
  const total = useMemo(() => dias.reduce((a, d) => a + Number(d.total), 0), [dias])
  const totalHistorico = useMemo(
    () => dias.reduce((a, d) => a + Number(d.historico || 0), 0), [dias])
  const conVenta = dias.filter(d => Number(d.total) > 0)
  const mejorDia = useMemo(() => Math.max(0, ...conVenta.map(d => Number(d.total))), [conVenta])
  const promedio = conVenta.length ? total / conVenta.length : 0
  const porMetodo = resumenQ.data?.por_metodo_pago ?? {}
  const esMesActual = anio === actual.anio && mes === actual.mes

  function mover(delta) {
    setPeriodo(({ anio, mes }) => {
      const m = mes + delta
      if (m < 1) return { anio: anio - 1, mes: 12 }
      if (m > 12) return { anio: anio + 1, mes: 1 }
      return { anio, mes: m }
    })
    setEditando(null)
  }

  const celdas = useMemo(() => {
    const primero = new Date(Date.UTC(anio, mes - 1, 1))
    const offset = (primero.getUTCDay() + 6) % 7      // lunes = 0
    const nDias = new Date(Date.UTC(anio, mes, 0)).getUTCDate()
    const out = Array.from({ length: offset }, () => null)
    for (let d = 1; d <= nDias; d++) {
      const fecha = `${anio}-${String(mes).padStart(2, '0')}-${String(d).padStart(2, '0')}`
      out.push({ dia: d, fecha, datos: porFecha[fecha] })
    }
    return out
  }, [anio, mes, porFecha])

  // Un día se puede anotar a mano solo si es PASADO y no tiene ventas del sistema. Sobre un día con
  // ventas registradas no se escribe: ese número lo produjo el sistema y anotarlo encima sería
  // inventar una segunda verdad para la misma fecha.
  const anotable = (c) => admin && c.fecha < hoyStrCO() && Number(c.datos?.total || 0) === 0

  function abrirEdicion(c) {
    if (!anotable(c)) return
    setEditando(c.fecha)
    setValor(String(Number(c.datos?.historico || 0) || ''))
  }

  async function guardarDia() {
    const monto = Number(String(valor).replace(/[^\d]/g, ''))
    if (!Number.isFinite(monto) || monto < 0) { toast.error('Escribe un total válido'); return }
    setGuardando(true)
    try {
      const res = await api('/historico-ventas/cargar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dias: [{ fecha: editando, total: monto }] }),
      })
      if (res.ok) {
        toast.success(`${editando}: ${cop(monto)}`)
        setEditando(null)
        q.refetch()
      } else if (res.status === 403) toast.error('Solo un administrador puede anotar días anteriores')
      else toast.error('No se pudo guardar el día')
    } catch { toast.error('Error de conexión') } finally { setGuardando(false) }
  }

  return (
    <div className="space-y-3">
      {/* Selector de período: mes y año como listas, no solo flechas — saltar a marzo del año
          pasado con flechas son 16 clics. */}
      <div className="flex flex-wrap items-center justify-center gap-2">
        <button onClick={() => mover(-1)} aria-label="Mes anterior"
          className="size-8 grid place-items-center rounded-md border border-border hover:bg-surface-2">
          <ChevronLeft className="size-4" />
        </button>
        <select value={mes} onChange={(e) => setPeriodo(p => ({ ...p, mes: Number(e.target.value) }))}
          aria-label="Mes"
          className="h-9 rounded-md border border-border bg-surface px-2 text-body-sm">
          {MESES.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
        </select>
        <select value={anio} onChange={(e) => setPeriodo(p => ({ ...p, anio: Number(e.target.value) }))}
          aria-label="Año"
          className="h-9 rounded-md border border-border bg-surface px-2 text-body-sm tabular">
          {Array.from({ length: 6 }, (_, i) => actual.anio - 4 + i).map(a => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <button onClick={() => mover(1)} aria-label="Mes siguiente"
          className="size-8 grid place-items-center rounded-md border border-border hover:bg-surface-2">
          <ChevronRight className="size-4" />
        </button>
        {esMesActual && (
          <span className="text-micro uppercase tracking-wider font-semibold px-2 py-1 rounded-md bg-primary/10 text-primary">
            Mes actual
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
        <KpiCard tone="primary" headerBand coloredValue icon={CalendarDays}
          label="Total del mes" value={cop(total)} />
        <KpiCard tone="warning" headerBand icon={CalendarDays}
          label="Días con venta" value={conVenta.length} />
        <KpiCard tone="success" headerBand coloredValue icon={TrendingUp}
          label="Promedio / día" value={cop(promedio)}
          sub={conVenta.length ? 'sobre los días con venta' : null} />
        <KpiCard tone="info" headerBand coloredValue icon={Trophy}
          label="Mejor día" value={cop(mejorDia)} />
      </div>

      <Card className="p-3.5">
        <div className="grid grid-cols-7 gap-1.5 text-center">
          {DIAS_SEMANA.map(d => (
            <div key={d} className="text-micro uppercase tracking-wider text-muted-foreground py-1">{d}</div>
          ))}
          {celdas.map((c, i) => {
            if (c === null) return <div key={`v-${i}`} />
            const historico = esHistorico(c.datos)
            const hoy = c.fecha === hoyStrCO()
            const monto = montoDia(c.datos)
            const editable = anotable(c)
            const Celda = editable ? 'button' : 'div'
            return (
              <Celda key={c.fecha}
                {...(editable ? {
                  type: 'button',
                  onClick: () => abrirEdicion(c),
                  'aria-label': `Anotar el total del ${c.fecha}`,
                } : {})}
                title={historico
                  ? `${c.fecha}: ${cop(Number(c.datos.historico))} — anotado a mano, antes del sistema`
                  : c.datos && Number(c.datos.total) > 0
                    ? `${c.fecha}: ${cop(Number(c.datos.total))} en ${c.datos.num_ventas} venta(s)`
                    : editable ? `${c.fecha}: sin ventas — toca para anotar el total`
                      : `${c.fecha}: sin movimiento`}
                className={`rounded-lg py-2 px-1 transition-colors ${
                  historico ? CLS_HISTORICO : NIVEL_CLS[nivel(monto, max)]
                } ${hoy ? 'ring-2 ring-primary' : ''} ${
                  editable ? 'hover:border-primary hover:bg-primary/5 cursor-pointer' : ''
                } ${editando === c.fecha ? 'ring-2 ring-primary' : ''}`}>
                {/* Sobre el relleno el texto va en `foreground`, no en `muted`: con tinta detrás,
                    el gris medio es el primero en desaparecer. El día sin ventas sí va atenuado —
                    ahí no hay nada que leer. */}
                <div className={`text-caption tabular ${monto ? 'text-foreground/70' : 'text-muted-foreground'}`}>
                  {c.dia}
                </div>
                <div className={`text-body-sm tabular font-semibold ${
                  monto ? 'text-foreground' : 'text-muted-foreground/50'}`}>
                  {montoCorto(monto)}
                </div>
              </Celda>
            )
          })}
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3 text-caption text-muted-foreground">
            {totalHistorico > 0 && (
              <span className="inline-flex items-center gap-1.5">
                <span className={`inline-block size-3 rounded ${CLS_HISTORICO}`} />
                Anotado a mano · {cop(totalHistorico)} (no entra a los reportes financieros)
              </span>
            )}
            {admin && (
              <span className="hidden sm:inline">Toca un día pasado sin ventas para anotar su total.</span>
            )}
          </div>
          <div className="flex items-center gap-1.5 text-caption text-muted-foreground">
            Menos
            {NIVEL_CLS.map((cls, i) => <span key={i} className={`inline-block size-3 rounded ${cls}`} />)}
            Más
          </div>
        </div>
      </Card>

      {editando && (
        <Card className="p-3.5 flex flex-wrap items-end gap-3">
          <div>
            <p className="text-caption text-muted-foreground">Total vendido el {editando}</p>
            <p className="text-caption text-muted-foreground">
              Solo el total: estos días no llevan gastos ni detalle, y no entran a los reportes.
            </p>
          </div>
          <Input autoFocus inputMode="numeric" value={valor}
            onChange={(e) => setValor(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') guardarDia(); if (e.key === 'Escape') setEditando(null) }}
            aria-label={`Total del ${editando}`} placeholder="1030500" className="h-9 w-40 tabular" />
          <button type="button" onClick={guardarDia} disabled={guardando}
            className="h-9 px-3 rounded-md bg-primary text-primary-foreground text-meta hover:bg-primary-hover disabled:opacity-60">
            {guardando ? 'Guardando…' : 'Guardar'}
          </button>
          <button type="button" onClick={() => setEditando(null)}
            className="h-9 px-3 rounded-md border border-border text-meta hover:bg-surface-2">
            Cancelar
          </button>
        </Card>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
        {['efectivo', 'transferencia', 'datafono'].map((m, i) => (
          <KpiCard key={m} tone={['success', 'info', 'warning'][i]} headerBand coloredValue
            label={m} value={cop(Number(porMetodo[m] || 0))} />
        ))}
      </div>

      {admin && (
        <div className="flex justify-end">
          <button type="button" onClick={() => setPegando(true)}
            className="inline-flex items-center gap-1.5 text-meta h-9 px-3 rounded-md border border-border hover:bg-surface-2">
            <Plus className="size-4" /> Cargar varios días de una vez
          </button>
        </div>
      )}

      {pegando && (
        <CargarDiasViejos anio={anio} onClose={() => setPegando(false)}
          onGuardado={() => { setPegando(false); q.refetch() }} />
      )}
    </div>
  )
}
