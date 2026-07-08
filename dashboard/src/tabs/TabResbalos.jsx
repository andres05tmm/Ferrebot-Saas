/*
 * TabResbalos — vigilancia de márgenes del vertical construcción (Fase 8), gated por la flag `resbalos`.
 * Dos reportes de SOLO LECTURA sobre las compras del período:
 *
 *   1) Reporte de resbalos (spec 11): los "viajes de material" (asfalto/arena comprados para revender al
 *      cliente de la obra) con su margen $ y % + alerta de baja rentabilidad (margen < 5% o negativo).
 *      Los márgenes reales del negocio son de 3–4%: un resbalo silencioso se come la utilidad.
 *      Contrato: GET /compras/resbalos?desde&hasta → [CompraLeer] (proveedor_nombre, total, resbalo,
 *      resbalo_pct, resbalo_alerta, precio_venta_cliente, categoria), ordenado del mayor margen al menor.
 *
 *   2) Análisis de precios de proveedor (spec 10): costo unitario PONDERADO por (proveedor, categoría) del
 *      período, con su rango y alerta de sobreprecio (el costo máximo superó en >15% el promedio).
 *      Contrato: GET /compras/analisis-precios?desde&hasta&proveedor_id&categoria → [AnalisisPrecioProveedor]
 *      (proveedor_nombre, categoria, n_compras, cantidad_total, costo_unitario_promedio/min/max,
 *      variacion_pct, alerta), ordenado del más caro al más barato.
 *
 * Ambos endpoints viven tras la capacidad `inventario` (router de compras) y son de rol admin. Dinero llega
 * como STRING (Decimal sin float). Presentación tokenizada (design system del repo, comunes.jsx).
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { Truck, TrendingDown, Route, TriangleAlert, ArrowUpNarrowWide } from 'lucide-react'
import { useFetch, cop, num } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Semaforo, EstadoVacio, Esqueleto } from './construccion/comunes.jsx'

const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }

// Etiqueta humana de la categoría de compra (enum del backend). Fallback: el propio código.
const CATEGORIA = {
  MEZCLA_ASFALTICA: 'Mezcla asfáltica', EMULSION_ASFALTICA: 'Emulsión asfáltica',
  ARENA_AGREGADO: 'Arena / agregado', REPUESTO: 'Repuesto', COMBUSTIBLE_GENERAL: 'Combustible',
  TRANSPORTE: 'Transporte', SERVICIO_MANTENIMIENTO: 'Mantenimiento', OTRO: 'Otro',
}
const catLabel = (c) => CATEGORIA[c] || c || '—'

export default function TabResbalos() {
  const { refreshKey } = useOutletContext() ?? {}
  // Rango compartido por ambos reportes (vacío = default del backend: mes en curso para resbalos,
  // últimos 6 meses para el análisis de precios).
  const [desde, setDesde] = useState('')
  const [hasta, setHasta] = useState('')
  const qs = [desde && `desde=${desde}`, hasta && `hasta=${hasta}`].filter(Boolean).join('&')
  const suf = qs ? `?${qs}` : ''

  const resbalosQ = useFetch(`/compras/resbalos${suf}`, [refreshKey, suf])
  const preciosQ = useFetch(`/compras/analisis-precios${suf}`, [refreshKey, suf])
  useRealtimeEvent(['reconnected'], () => { resbalosQ.refetch(); preciosQ.refetch() })

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex items-center gap-2">
            <Route className="size-4 text-muted-foreground" aria-hidden="true" />
            <h1 className="text-sm font-semibold text-foreground">Resbalos y precios de proveedor</h1>
          </div>
          <div className="ml-auto flex items-end gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-[11px] font-medium text-secondary-foreground">Desde</span>
              <Input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} className="h-9" aria-label="Desde" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[11px] font-medium text-secondary-foreground">Hasta</span>
              <Input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} className="h-9" aria-label="Hasta" />
            </label>
          </div>
        </div>
      </Card>

      <ReporteResbalos query={resbalosQ} />
      <AnalisisPrecios query={preciosQ} />
    </div>
  )
}

// ── Reporte de resbalos (viajes de material con margen) ──────────────────────────────────────────────
function ReporteResbalos({ query }) {
  const filas = Array.isArray(query.data) ? query.data : []
  const totalMargen = filas.reduce((acc, c) => acc + n(c.resbalo), 0)
  const enAlerta = filas.filter((c) => c.resbalo_alerta).length

  return (
    <Card className="p-0 overflow-hidden">
      <SeccionCabecera icono={TrendingDown} titulo="Reporte de resbalos" conteo={filas.length}>
        {filas.length > 0 && (
          <span className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
            <span>Margen total <span className="tabular font-medium text-foreground">{cop(totalMargen)}</span></span>
            {enAlerta > 0 && (
              <span className="inline-flex items-center gap-1 text-destructive">
                <TriangleAlert className="size-3" aria-hidden="true" />{enAlerta} en alerta
              </span>
            )}
          </span>
        )}
      </SeccionCabecera>

      {query.loading ? (
        <Esqueleto filas={3} />
      ) : query.error ? (
        <ErrorLinea>No se pudo cargar el reporte de resbalos.</ErrorLinea>
      ) : filas.length === 0 ? (
        <EstadoVacio
          icono={Truck}
          titulo="Sin viajes de material en el período"
          descripcion="Registra una compra marcada como viaje de material (asfalto/arena para revender al cliente) con su precio de venta y aquí verás el margen y la alerta de baja rentabilidad."
        />
      ) : (
        <TablaScroll cabeceras={['Proveedor', 'Categoría', 'Costo viaje', 'Venta cliente', 'Margen', '%', '']}>
          {filas.map((c) => (
            <tr key={c.id} className="border-t border-border-subtle">
              <Td className="font-medium text-foreground">{c.proveedor_nombre || '—'}</Td>
              <Td className="text-muted-foreground">{catLabel(c.categoria)}</Td>
              <TdNum>{cop(n(c.total))}</TdNum>
              <TdNum>{cop(n(c.precio_venta_cliente))}</TdNum>
              <TdNum className={n(c.resbalo) < 0 ? 'text-destructive' : 'text-foreground'}>{cop(n(c.resbalo))}</TdNum>
              <TdNum>{c.resbalo_pct != null ? `${num(c.resbalo_pct)}%` : '—'}</TdNum>
              <Td>
                {c.resbalo_alerta
                  ? <Semaforo tono="rojo">Baja rentabilidad</Semaforo>
                  : <Semaforo tono="verde">Sano</Semaforo>}
              </Td>
            </tr>
          ))}
        </TablaScroll>
      )}
    </Card>
  )
}

// ── Análisis de precios de proveedor ─────────────────────────────────────────────────────────────────
function AnalisisPrecios({ query }) {
  const filas = Array.isArray(query.data) ? query.data : []
  const enAlerta = filas.filter((f) => f.alerta).length

  return (
    <Card className="p-0 overflow-hidden">
      <SeccionCabecera icono={ArrowUpNarrowWide} titulo="Análisis de precios de proveedor" conteo={filas.length}>
        {enAlerta > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-destructive">
            <TriangleAlert className="size-3" aria-hidden="true" />{enAlerta} con sobreprecio
          </span>
        )}
      </SeccionCabecera>

      {query.loading ? (
        <Esqueleto filas={3} />
      ) : query.error ? (
        <ErrorLinea>No se pudo cargar el análisis de precios.</ErrorLinea>
      ) : filas.length === 0 ? (
        <EstadoVacio
          icono={Truck}
          titulo="Sin compras en el período"
          descripcion="El análisis compara el costo unitario de cada proveedor (ponderado por cantidad) contra su propio historial. Registra compras con categoría para vigilar sobreprecios."
        />
      ) : (
        <TablaScroll cabeceras={['Proveedor', 'Categoría', 'Compras', 'Cant.', 'Costo prom.', 'Mín', 'Máx', 'Δ vs prom.', '']}>
          {filas.map((f, i) => (
            <tr key={`${f.proveedor_id}-${f.categoria}-${i}`} className="border-t border-border-subtle">
              <Td className="font-medium text-foreground">{f.proveedor_nombre || '—'}</Td>
              <Td className="text-muted-foreground">{catLabel(f.categoria)}</Td>
              <TdNum>{f.n_compras}</TdNum>
              <TdNum>{num(f.cantidad_total)}</TdNum>
              <TdNum className="font-medium text-foreground">{cop(n(f.costo_unitario_promedio))}</TdNum>
              <TdNum className="text-muted-foreground">{cop(n(f.costo_unitario_min))}</TdNum>
              <TdNum className={f.alerta ? 'text-destructive' : 'text-muted-foreground'}>{cop(n(f.costo_unitario_max))}</TdNum>
              <TdNum>{f.variacion_pct != null ? `${num(f.variacion_pct)}%` : '—'}</TdNum>
              <Td>{f.alerta && <Semaforo tono="rojo">Sobreprecio</Semaforo>}</Td>
            </tr>
          ))}
        </TablaScroll>
      )}
    </Card>
  )
}

// ── Átomos de presentación (locales al tab) ──────────────────────────────────────────────────────────
function SeccionCabecera({ icono: Icono, titulo, conteo, children }) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle px-4 py-2.5">
      <Icono className="size-4 text-muted-foreground" aria-hidden="true" />
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {titulo} {conteo > 0 && <span className="tabular">· {conteo}</span>}
      </h2>
      {children}
    </div>
  )
}

function TablaScroll({ cabeceras, children }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
            {cabeceras.map((h, i) => (
              <th key={i} className={`px-3 py-2 font-medium ${i >= 2 && i < cabeceras.length - 1 ? 'text-right' : ''}`}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function Td({ children, className = '' }) {
  return <td className={`px-3 py-2 align-middle ${className}`}>{children}</td>
}
function TdNum({ children, className = '' }) {
  return <td className={`tabular px-3 py-2 text-right align-middle ${className}`}>{children}</td>
}
function ErrorLinea({ children }) {
  return <p className="px-4 py-6 text-center text-[12px] text-destructive">{children}</p>
}
