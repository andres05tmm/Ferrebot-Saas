/*
 * TabResultados — "¿este mes gané o perdí, y por qué?", respondido en una sola página.
 *
 * Antes eran tres sub-tabs con cinco cifras planas. El problema no era falta de datos: era que
 * ninguna cifra tenía CONTEXTO — "utilidad $14.500.000" no dice si el mes va bien o mal. Ahora:
 *   1. Una métrica héroe (la utilidad) con su Δ contra el periodo anterior NOMBRADO y su peso
 *      sobre las ventas. El "vs. 16–30 jun" es lo que convierte un número en información.
 *   2. Cuatro KPIs con dirección semántica: en costo de mercancía subir es MALO, y se pinta así.
 *   3. Una cascada de la venta a la utilidad — el gráfico de barras viejo ponía ingresos y utilidad
 *      neta en la misma escala, con lo cual la utilidad se veía como una rayita.
 *   4. El punto de equilibrio, que ya se calculaba en el tab Gastos pero pertenece aquí.
 * Flujo de dinero y el detalle por producto bajan a secciones colapsadas: siguen a un clic, pero no
 * compiten con la pregunta principal.
 *
 * Honestidad del margen: `cobertura_pct` dice qué parte de lo vendido tiene costo registrado. Un
 * margen sobre el 60% de cobertura es una estimación y se rotula como tal.
 * Es la vista GERENCIAL; /estados-financieros es la contable del ledger (ADR 0030) — dan cifras
 * distintas a propósito.
 * Solo admin. Live: re-fetch ante venta / gasto / inventario / devolución / reconnected.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Boxes, Coins, Percent, Receipt, Scale, TrendingUp, Wallet } from '@/lib/icons.jsx'
import { useFetch, cop, num, SkeletonFilas } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import KpiCard from '@/components/KpiCard.jsx'
import PageHeader from '@/components/PageHeader.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import {
  CATEGORIA_LABEL as CATEGORIAS_LABEL_GASTO, PERIODOS_RESULTADOS, TIPO_LABEL, hoyCO, periodo,
} from '@/lib/gastos.js'

const EVENTOS = [
  'venta_registrada', 'gasto_registrado', 'inventario_actualizado', 'devolucion_registrada',
  'reconnected',
]

const METODO_LABEL = {
  efectivo: 'Efectivo', transferencia: 'Transferencia', tarjeta: 'Tarjeta',
  nequi: 'Nequi', daviplata: 'Daviplata', datafono: 'Datáfono',
}
// Categorías de gasto + los renglones que el flujo separa por TIPO (0071): un retiro o una inversión
// salieron de la caja de verdad, pero no son gasto y no se disfrazan de uno.
const CATEGORIA_LABEL = { ...CATEGORIAS_LABEL_GASTO, ...TIPO_LABEL }

const n = (v) => Number(v ?? 0)

/** Variación porcentual. `null` cuando no hay base contra la cual comparar (no es "0%"). */
function delta(actual, anterior) {
  if (anterior === null || anterior === undefined || Number(anterior) === 0) return null
  return ((Number(actual) - Number(anterior)) / Math.abs(Number(anterior))) * 100
}

/** Porcentaje de `parte` sobre `todo`, o null si no hay base. */
const pct = (parte, todo) => (Number(todo) ? (Number(parte) / Number(todo)) * 100 : null)

const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic']

/** "16–30 jun" · "16 jun – 5 jul". Se nombra el rango real, nunca el mes: la ventana comparada es
 *  del mismo LARGO que la elegida, así que casi nunca coincide con un mes calendario. */
function rangoCorto(desde, hasta) {
  if (!desde || !hasta) return ''
  const [, m1, d1] = desde.split('-')
  const [, m2, d2] = hasta.split('-')
  const mes1 = MESES[Number(m1) - 1]
  const mes2 = MESES[Number(m2) - 1]
  return m1 === m2
    ? `${Number(d1)}–${Number(d2)} ${mes2}`
    : `${Number(d1)} ${mes1} – ${Number(d2)} ${mes2}`
}

/** "31 de julio", para la nota de periodo en curso. */
const MESES_LARGO = [
  'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
  'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]
function fechaLarga(ymd) {
  const [, m, d] = ymd.split('-')
  return `${Number(d)} de ${MESES_LARGO[Number(m) - 1]}`
}

export default function TabResultados() {
  const { isAdmin } = useAuth()
  // Analítica del negocio completo: oculta para el vendedor (no se piden los endpoints).
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-body-sm text-muted-foreground">
        Los resultados financieros son solo para administradores.
      </Card>
    )
  }
  return <ResultadosContenido />
}

function ResultadosContenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const [periodoId, setPeriodoId] = useState('mes')
  const hoy = useMemo(hoyCO, [])
  // `personalizado` gana sobre el preset mientras esté activo (el chip queda sin marcar).
  const [custom, setCustom] = useState(null)
  const rango = custom ?? periodo(periodoId, hoy)
  const clave = [refreshKey, rango.desde, rango.hasta]
  const qs = `desde=${rango.desde}&hasta=${rango.hasta}`

  const resQ = useFetch(`/reportes/resultados?${qs}`, clave)
  const resumenQ = useFetch(`/reportes/resumen?${qs}`, clave)          // nº de ventas y ticket
  const gastosQ = useFetch(`/reportes/gastos?${qs}`, clave)            // punto de equilibrio
  const flujoQ = useFetch(`/reportes/flujo-dinero?${qs}`, clave)
  const catQ = useFetch(`/reportes/margen-productos?${qs}&por=categoria&limite=50`, clave)
  const serieQ = useFetch('/reportes/serie-ventas?dias=30', [refreshKey])

  const refrescar = () => {
    resQ.refetch(); resumenQ.refetch(); gastosQ.refetch()
    flujoQ.refetch(); catQ.refetch(); serieQ.refetch()
  }
  useRealtimeEvent(EVENTOS, refrescar)

  const d = resQ.data || {}
  const ant = d.anterior || null
  const enCurso = rango.hasta >= hoy

  const elegirPreset = (id) => { setCustom(null); setPeriodoId(id) }
  const setCampo = (k) => (e) => setCustom({ ...rango, [k]: e.target.value })

  return (
    <div className="space-y-3">
      <PageHeader
        icono={TrendingUp}
        titulo="Resultados financieros"
        sublinea={
          enCurso
            ? `Periodo en curso · datos al ${fechaLarga(rango.hasta)}`
            : `${rangoCorto(rango.desde, rango.hasta)} · periodo cerrado`
        }
      >
        <div className="flex flex-wrap items-end gap-2">
          {PERIODOS_RESULTADOS.map(([id, label]) => (
            <button
              key={id} onClick={() => elegirPreset(id)}
              aria-pressed={!custom && periodoId === id}
              className={`px-2.5 py-1 rounded-md border text-body-sm ${
                !custom && periodoId === id
                  ? 'border-primary bg-primary/10 text-primary'
                  : 'border-border hover:bg-surface-2'}`}
            >
              {label}
            </button>
          ))}
          <label className="text-caption text-muted-foreground ml-auto">
            Desde
            <Input type="date" value={rango.desde} onChange={setCampo('desde')}
              aria-label="Desde" className="h-9 mt-1" />
          </label>
          <label className="text-caption text-muted-foreground">
            Hasta
            <Input type="date" value={rango.hasta} onChange={setCampo('hasta')}
              aria-label="Hasta" className="h-9 mt-1" />
          </label>
        </div>
      </PageHeader>

      {resQ.error && (
        <Card className="p-4 text-body-sm text-danger">No se pudieron cargar los resultados.</Card>
      )}

      <Heroe d={d} ant={ant} loading={resQ.loading} />

      <FilaKpis
        d={d} ant={ant} loading={resQ.loading}
        resumen={resumenQ.data} serie={serieQ.data}
      />

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
        <div className="lg:col-span-3">
          <Cascada d={d} loading={resQ.loading} />
        </div>
        <div className="lg:col-span-2 space-y-3">
          <PuntoEquilibrio g={gastosQ.data} rango={rango} hoy={hoy} />
          <FlujoResumen f={flujoQ.data} />
        </div>
      </div>

      <MargenCategorias filas={catQ.data} loading={catQ.loading} ingresos={n(d.ingresos)} />

      <DetalleProductos qs={qs} clave={clave} />
      <DetalleFlujo f={flujoQ.data} loading={flujoQ.loading} />

      <p className="text-caption text-muted-foreground px-1">
        Vista gerencial del negocio. Para los estados formales que le entregas a tu contador, ve a
        Estados financieros: se arman con el ledger de doble partida y por eso dan cifras distintas.
      </p>
    </div>
  )
}

// --- Métrica héroe: la utilidad, con su porqué --------------------------------
function Heroe({ d, ant, loading }) {
  const neta = n(d.utilidad_neta)
  const dNeta = delta(neta, ant?.utilidad_neta)
  const margenNeto = pct(neta, n(d.ingresos))
  const cobertura = d.cobertura_pct == null ? null : Number(d.cobertura_pct)

  return (
    <Card className="p-4 sm:p-5">
      <div className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">
        Utilidad del periodo
      </div>
      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className={`font-display text-3xl sm:text-4xl font-semibold tabular tracking-tight ${
          neta >= 0 ? 'text-foreground' : 'text-danger'} ${loading ? 'opacity-50' : ''}`}>
          {cop(neta)}
        </span>
        {dNeta !== null && (
          <span className={`text-body-sm font-semibold tabular ${
            dNeta >= 0 ? 'text-success' : 'text-danger'}`}>
            {dNeta >= 0 ? '▲' : '▼'} {dNeta >= 0 ? '+' : ''}{dNeta.toFixed(1)}%
            <span className="font-normal text-muted-foreground">
              {' '}vs. {rangoCorto(ant.desde, ant.hasta)}
            </span>
          </span>
        )}
        {margenNeto !== null && (
          <span className="text-body-sm text-muted-foreground tabular">
            {margenNeto.toFixed(1)}% de las ventas
          </span>
        )}
      </div>
      {/* Solo cuando los datos LLEGARON y no traían comparado: en error o cargando no se afirma nada. */}
      {ant === null && !loading && d.desde && (
        <p className="mt-2 text-caption text-muted-foreground">
          No hay periodo anterior con movimiento para comparar.
        </p>
      )}
      {cobertura !== null && cobertura < 100 && (
        <p className="mt-2 text-caption text-warning">
          Margen calculado sobre el {num(cobertura)}% de lo vendido:
          el resto no tiene costo registrado, así que la utilidad real puede ser menor.
        </p>
      )}
    </Card>
  )
}

// --- Los cuatro KPIs ----------------------------------------------------------
function FilaKpis({ d, ant, loading, resumen, serie }) {
  const ingresos = n(d.ingresos)
  const costo = n(d.costo_ventas)
  const bruta = n(d.utilidad_bruta)
  const numVentas = Number(resumen?.num_ventas ?? 0)
  const ticket = Number(resumen?.ticket_promedio ?? 0)
  const pesoSobreVentas = (v) => {
    const p = pct(v, ingresos)
    return p === null ? undefined : `${p.toFixed(1)}% de las ventas`
  }

  return (
    <div role="group" aria-label="Métricas del periodo"
      className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <KpiCard
        tone="info" icon={Coins} label="Ventas netas" value={cop(ingresos)} loading={loading}
        deltaPct={delta(ingresos, ant?.ingresos) ?? undefined}
        spark={Array.isArray(serie) ? serie : undefined}
        sub={n(d.devoluciones) > 0 ? `ya sin ${cop(n(d.devoluciones))} devueltos` : undefined}
      />
      <KpiCard
        tone="warning" icon={Boxes} label="Costo de mercancía" value={cop(costo)} loading={loading}
        deltaPct={delta(costo, ant?.costo_ventas) ?? undefined}
        direccion="menos_es_mejor"
        sub={pesoSobreVentas(costo)}
      />
      <KpiCard
        tone="success" icon={Percent} label="Utilidad bruta" value={cop(bruta)} loading={loading}
        deltaPct={delta(bruta, ant?.utilidad_bruta) ?? undefined}
        sub={pesoSobreVentas(bruta)}
      />
      <KpiCard
        tone="primary" icon={Receipt} label="Ticket promedio" value={cop(ticket)}
        sub={`${num(numVentas)} ${numVentas === 1 ? 'venta' : 'ventas'} en el periodo`}
      />
    </div>
  )
}

// --- Cascada: de la venta a la utilidad ---------------------------------------
// Barras en CSS y no en recharts a propósito: cada renglón necesita SU cifra y SU % de ventas
// legibles al lado (un tooltip esconde justo lo que el dueño vino a leer), y una etiqueta como
// "Costo de mercancía" no cabe en un eje. `base` es dónde arranca la barra; los subtotales
// arrancan en cero y los restados cuelgan del renglón anterior.
function Cascada({ d, loading }) {
  const vb = n(d.ventas_brutas)
  const dev = n(d.devoluciones)
  const ing = n(d.ingresos)
  const costo = n(d.costo_ventas)
  const bruta = n(d.utilidad_bruta)
  const gastos = n(d.gastos)
  const neta = n(d.utilidad_neta)

  const filas = [
    { nombre: 'Ventas brutas', valor: vb, base: 0, tipo: 'ancla' },
    ...(dev > 0 ? [{ nombre: 'Devoluciones', valor: -dev, base: ing, tipo: 'resta' }] : []),
    { nombre: 'Ventas netas', valor: ing, base: 0, tipo: 'ancla' },
    { nombre: 'Costo de mercancía', valor: -costo, base: bruta, tipo: 'resta' },
    { nombre: 'Utilidad bruta', valor: bruta, base: 0, tipo: 'ancla' },
    { nombre: 'Gastos', valor: -gastos, base: neta > 0 ? neta : 0, tipo: 'resta' },
    { nombre: 'Utilidad neta', valor: neta, base: 0, tipo: 'total' },
  ]
  // Escala común: la barra más larga posible es la venta bruta (todo lo demás sale de ahí).
  const escala = Math.max(vb, Math.abs(neta), 1)
  const ancho = (v) => `${Math.min(100, (Math.abs(v) / escala) * 100)}%`
  const offset = (b) => `${Math.min(100, (Math.abs(b) / escala) * 100)}%`

  const COLOR = {
    ancla: 'bg-info', resta: 'bg-warning',
    total: neta >= 0 ? 'bg-success' : 'bg-danger',
  }

  return (
    <Card className="p-3.5 h-full">
      <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-3">
        De la venta a la utilidad
      </h2>
      {loading ? <SkeletonFilas filas={6} /> : vb === 0 ? (
        <p className="text-body-sm text-muted-foreground py-6 text-center">
          Sin ventas en el periodo.
        </p>
      ) : (
        <ul className="space-y-2">
          {filas.map((f) => {
            // Los % son sobre VENTAS NETAS, que es la base de todo el P&L. En la venta bruta se
            // omite: "110% de ventas" no le dice nada a nadie.
            const p = f.nombre === 'Ventas brutas' ? null : pct(f.valor, ing)
            return (
              <li key={f.nombre} className="grid grid-cols-[9.5rem_1fr] items-center gap-2 sm:grid-cols-[11rem_1fr]">
                <span className={`text-body-sm truncate ${
                  f.tipo === 'total' ? 'font-semibold' : 'text-muted-foreground'}`}>
                  {f.tipo === 'resta' ? '− ' : f.tipo === 'ancla' ? '= ' : ''}{f.nombre}
                </span>
                <div className="min-w-0">
                  <div className="h-4 w-full rounded-sm bg-surface-2 overflow-hidden flex">
                    <div style={{ width: offset(f.base) }} aria-hidden="true" />
                    <div className={`h-full rounded-sm ${COLOR[f.tipo]}`}
                      style={{ width: ancho(f.valor) }} aria-hidden="true" />
                  </div>
                  <div className="mt-0.5 flex justify-between gap-2 text-caption tabular">
                    <span className={f.tipo === 'total' ? 'font-semibold' : ''}>
                      {f.valor < 0 ? `(${cop(Math.abs(f.valor))})` : cop(f.valor)}
                    </span>
                    {p !== null && (
                      <span className="text-muted-foreground">
                        {Math.abs(p).toFixed(1)}% de ventas
                      </span>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

// --- Punto de equilibrio ------------------------------------------------------
// `punto_equilibrio_mes` va SIEMPRE sobre el mes de `hasta` (así lo calcula el backend), así que la
// barra de avance solo se dibuja cuando el rango elegido ES ese mes corrido. Con otro rango se
// muestra la meta sin progreso: comparar la venta de una semana contra la meta del mes mentiría.
function PuntoEquilibrio({ g, rango, hoy }) {
  const meta = g?.punto_equilibrio_mes == null ? null : Number(g.punto_equilibrio_mes)
  const esMesCorrido = rango.desde === `${rango.hasta.slice(0, 8)}01`

  if (!g) return null
  if (meta === null) {
    return (
      <Card className="p-3.5">
        <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
          Punto de equilibrio
        </h2>
        <p className="text-body-sm text-muted-foreground">
          Falta margen bruto positivo en el mes para calcularlo.
        </p>
      </Card>
    )
  }

  const ventas = Number(g.ventas ?? 0)
  const avance = esMesCorrido && meta > 0 ? Math.min(100, (ventas / meta) * 100) : null
  const alcanzado = avance !== null && ventas >= meta
  const diaDelMes = Number(hoy.slice(8, 10))

  return (
    <Card className="p-3.5">
      <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-1.5 inline-flex items-center gap-1.5">
        <Scale className="size-3.5" aria-hidden="true" /> Punto de equilibrio
      </h2>
      {avance !== null ? (
        <>
          <div className="h-2.5 w-full rounded-full bg-surface-2 overflow-hidden">
            <div className={`h-full rounded-full ${alcanzado ? 'bg-success' : 'bg-primary'}`}
              style={{ width: `${avance}%` }}
              role="progressbar" aria-valuenow={Math.round(avance)}
              aria-valuemin={0} aria-valuemax={100}
              aria-label="Avance hacia el punto de equilibrio del mes" />
          </div>
          <p className="mt-1.5 text-body-sm tabular">
            {cop(ventas)} <span className="text-muted-foreground">de {cop(meta)}</span>
            {' '}<span className="font-semibold">({avance.toFixed(0)}%)</span>
          </p>
          <p className="mt-0.5 text-caption text-muted-foreground">
            {alcanzado
              ? `Ya cubriste los gastos fijos del mes; de aquí en adelante es utilidad. Día ${diaDelMes}.`
              : `Día ${diaDelMes} del mes · te faltan ${cop(meta - ventas)} para no perder.`}
          </p>
        </>
      ) : (
        <p className="text-body-sm tabular">
          {cop(meta)} <span className="text-muted-foreground">al mes</span>
          {g.punto_equilibrio_dia != null && (
            <> · {cop(Number(g.punto_equilibrio_dia))}{' '}
              <span className="text-muted-foreground">al día</span></>
          )}
        </p>
      )}
      <p className="mt-1.5 text-caption text-muted-foreground">
        Gastos fijos del mes {cop(Number(g.fijos_mes ?? 0))} ÷ margen bruto{' '}
        {g.margen_bruto_pct == null ? '—' : `${num(Number(g.margen_bruto_pct))}%`}.
      </p>
    </Card>
  )
}

// --- Flujo de caja: el resumen (el desglose vive en la sección colapsada) ------
function FlujoResumen({ f }) {
  if (!f) return null
  const neto = n(f.neto)
  return (
    <Card className="p-3.5">
      <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-1.5 inline-flex items-center gap-1.5">
        <Wallet className="size-3.5" aria-hidden="true" /> Flujo de caja
      </h2>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-body-sm tabular">
        <span className="text-success">Entró {cop(n(f.total_entradas))}</span>
        <span className="text-warning">Salió {cop(n(f.total_salidas))}</span>
        <span className={`font-semibold ${neto >= 0 ? 'text-success' : 'text-danger'}`}>
          Neto {neto < 0 ? `(${cop(Math.abs(neto))})` : cop(neto)}
        </span>
      </div>
      {n(f.ventas_fiado) > 0 && (
        <p className="mt-1.5 text-caption text-muted-foreground">
          Además se vendió {cop(n(f.ventas_fiado))} fiado — eso es cartera, no plata en mano.
        </p>
      )}
    </Card>
  )
}

// --- Margen por categoría -----------------------------------------------------
// Dos números por fila: cuánto DEJA (margen %) y cuánto PESA (mix sobre las ventas). Cemento con 11%
// de margen pero 30% de las ventas es un negocio distinto al de tornillería con 25% y 4%.
function MargenCategorias({ filas, loading, ingresos }) {
  const datos = Array.isArray(filas) ? filas.slice(0, 8) : []
  const maxMargen = Math.max(...datos.map(f => Math.abs(Number(f.margen_pct ?? 0))), 1)

  return (
    <Card className="p-3.5">
      <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-3">
        Margen por categoría
      </h2>
      {loading ? <SkeletonFilas filas={4} /> : datos.length === 0 ? (
        <p className="text-body-sm text-muted-foreground py-4 text-center">
          Sin ventas de catálogo en el periodo.
        </p>
      ) : (
        <ul className="space-y-2">
          {datos.map(f => {
            const margenPct = f.margen_pct == null ? null : Number(f.margen_pct)
            const mix = pct(Number(f.ingresos), ingresos)
            return (
              <li key={f.clave} className="grid grid-cols-[8rem_1fr] items-center gap-2 sm:grid-cols-[12rem_1fr]">
                <span className="text-body-sm truncate" title={f.clave}>
                  {f.clave}
                  {Number(f.cobertura_pct) < 100 && (
                    <Badge variant="outline"
                      className="ml-1.5 text-micro bg-warning/10 text-warning border-warning/20"
                      title="Parte de las unidades vendidas no tiene costo registrado: el margen real puede ser menor.">
                      {num(Number(f.cobertura_pct))}%
                    </Badge>
                  )}
                </span>
                <div className="min-w-0 flex items-center gap-2">
                  <div className="h-3 flex-1 rounded-sm bg-surface-2 overflow-hidden">
                    <div className={`h-full rounded-sm ${margenPct >= 0 ? 'bg-success' : 'bg-danger'}`}
                      style={{ width: `${(Math.abs(margenPct ?? 0) / maxMargen) * 100}%` }}
                      aria-hidden="true" />
                  </div>
                  <span className="shrink-0 w-14 text-right text-body-sm tabular font-medium">
                    {margenPct === null ? '—' : `${margenPct.toFixed(1)}%`}
                  </span>
                  <span className="shrink-0 w-24 text-right text-caption tabular text-muted-foreground">
                    {mix === null ? '' : `${mix.toFixed(0)}% de ventas`}
                  </span>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

// --- Secciones colapsadas -----------------------------------------------------
// `<details>` nativo: sin JS de estado, accesible de fábrica y el navegador recuerda el foco.
function Seccion({ titulo, children }) {
  return (
    <Card className="p-0 overflow-hidden">
      <details className="group">
        <summary className="cursor-pointer list-none px-3.5 py-3 text-caption font-semibold uppercase tracking-wider text-muted-foreground hover:bg-surface-2 flex items-center gap-2">
          <span className="transition-transform group-open:rotate-90" aria-hidden="true">▸</span>
          {titulo}
        </summary>
        <div className="px-3.5 pb-3.5 border-t border-border-subtle pt-3">{children}</div>
      </details>
    </Card>
  )
}

/** El detalle por producto solo se pide cuando el dueño abre la sección (`useFetch` con path
 *  falsy queda en reposo): son hasta 100 filas que casi nunca se miran. */
function DetalleProductos({ qs, clave }) {
  const [abierto, setAbierto] = useState(false)
  const q = useFetch(
    abierto ? `/reportes/margen-productos?${qs}&por=producto&limite=100` : null,
    [abierto, ...clave],
  )
  const filas = Array.isArray(q.data) ? q.data : []

  return (
    <Card className="p-0 overflow-hidden">
      <details className="group" onToggle={(e) => setAbierto(e.currentTarget.open)}>
        <summary className="cursor-pointer list-none px-3.5 py-3 text-caption font-semibold uppercase tracking-wider text-muted-foreground hover:bg-surface-2 flex items-center gap-2">
          <span className="transition-transform group-open:rotate-90" aria-hidden="true">▸</span>
          Margen producto por producto
        </summary>
        <div className="px-3.5 pb-3.5 border-t border-border-subtle pt-3">
          {q.loading ? <SkeletonFilas filas={6} /> : filas.length === 0 ? (
            <p className="text-body-sm text-muted-foreground py-4 text-center">
              Sin ventas de catálogo en el periodo.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-body-sm">
                <thead>
                  <tr className="text-left text-caption text-muted-foreground">
                    <th className="py-1 pr-2 font-normal">Producto</th>
                    <th className="py-1 pr-2 font-normal text-right">Cant.</th>
                    <th className="py-1 pr-2 font-normal text-right">Ingresos</th>
                    <th className="py-1 pr-2 font-normal text-right">Costo</th>
                    <th className="py-1 pr-2 font-normal text-right">Margen</th>
                    <th className="py-1 font-normal text-right">Margen %</th>
                  </tr>
                </thead>
                <tbody>
                  {filas.map(f => (
                    <tr key={f.clave} className="border-t border-border-subtle">
                      <td className="py-1.5 pr-2">
                        {f.clave}
                        {Number(f.cobertura_pct) < 100 && (
                          <Badge variant="outline"
                            className="ml-1.5 text-micro bg-warning/10 text-warning border-warning/20"
                            title="Parte de las unidades vendidas no tiene costo registrado: el margen real puede ser menor.">
                            costo incompleto ({num(Number(f.cobertura_pct))}%)
                          </Badge>
                        )}
                      </td>
                      <td className="py-1.5 pr-2 text-right tabular">{num(Number(f.cantidad))}</td>
                      <td className="py-1.5 pr-2 text-right tabular">{cop(Number(f.ingresos))}</td>
                      <td className="py-1.5 pr-2 text-right tabular">{cop(Number(f.cogs))}</td>
                      <td className={`py-1.5 pr-2 text-right tabular font-medium ${
                        Number(f.margen) >= 0 ? 'text-success' : 'text-destructive'}`}>
                        {cop(Number(f.margen))}
                      </td>
                      <td className="py-1.5 text-right tabular">
                        {f.margen_pct != null ? `${num(Number(f.margen_pct))}%` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </details>
    </Card>
  )
}

function DetalleFlujo({ f, loading }) {
  const d = f || {}
  const filaEntradas = [
    ...Object.entries(d.ventas_por_metodo || {}).map(([m, v]) => [METODO_LABEL[m] || m, v]),
    ['Abonos de clientes (fiados)', d.abonos_fiados],
    ['Otros ingresos de caja', d.ingresos_caja],
  ].filter(([, v]) => Number(v) > 0)

  // Las salidas de CAJA se desglosan por su procedencia (`egresos_por_origen`); si el backend es
  // viejo y no la manda, se cae al total agrupado de siempre.
  const egresosDetallados = Object.entries(d.egresos_por_origen || {})
  const filaSalidas = [
    ...Object.entries(d.gastos_por_categoria || {}).map(([c, v]) => [`Gastos · ${CATEGORIA_LABEL[c] || c}`, v]),
    ['Abonos a proveedores', d.abonos_proveedores],
    ...(egresosDetallados.length > 0 ? egresosDetallados : [['Otros egresos de caja', d.egresos_caja]]),
    // Plata que salió del negocio sin pasar por el cajón (pago mixto / transferencia).
    ...Object.entries(d.fuera_de_caja_por_medio || {}).map(([m, v]) => [`Pagado por fuera de caja · ${m}`, v]),
  ].filter(([, v]) => Number(v) > 0)

  return (
    <Seccion titulo="Flujo de dinero: qué entró y qué salió">
      {loading ? <SkeletonFilas filas={5} /> : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div>
            <h3 className="text-caption font-semibold text-success mb-1.5">Entradas</h3>
            <DesgloseLista filas={filaEntradas} vacio="No entró dinero en el periodo." />
          </div>
          <div>
            <h3 className="text-caption font-semibold text-warning mb-1.5">Salidas</h3>
            <DesgloseLista filas={filaSalidas} vacio="No salió dinero en el periodo." />
          </div>
        </div>
      )}
    </Seccion>
  )
}

function DesgloseLista({ filas, vacio }) {
  if (filas.length === 0) return <p className="text-body-sm text-muted-foreground">{vacio}</p>
  return (
    <ul className="divide-y divide-border-subtle">
      {filas.map(([label, v]) => (
        <li key={label} className="py-1.5 flex justify-between gap-2 text-body-sm">
          <span className="truncate">{label}</span>
          <span className="tabular font-medium shrink-0">{cop(Number(v))}</span>
        </li>
      ))}
    </ul>
  )
}
