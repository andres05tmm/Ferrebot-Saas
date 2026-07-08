/*
 * ResumenPortafolio — home de obra (Fase 8): la foto del portafolio ARRIBA de la lista de obras.
 * Un solo request AGREGADO y CACHEADO (GET /obras/panel, TTL corto por empresa) trae el rollup
 * financiero (ingreso presupuestado, gasto real, utilidad real), el conteo por estado y cuántas obras
 * están en alerta — para que el dueño vea la salud del portafolio sin abrir obra por obra.
 *
 * Contrato: GET /obras/panel → {
 *   total_obras, obras_activas, por_estado: {ESTADO: n}, ingreso_presupuestado_total, gasto_total,
 *   utilidad_real_total, obras_en_alerta,
 *   obras: [{ obra_id, nombre, estado, ingreso_presupuestado, gasto_total, utilidad_real,
 *             tiene_presupuesto, semaforo, alerta_margen }]  // ordenadas por severidad (rojo primero)
 * }.  Dinero llega como STRING (Decimal sin float). Solo lectura; rol admin (financiero).
 *
 * Presentación tokenizada (design system del repo, comunes.jsx). Silencioso si el fetch falla o si no
 * hay obras: no estorba a la lista de abajo (degradación limpia).
 */
import { Gauge, TriangleAlert, TrendingUp, TrendingDown, Minus, Building2 } from 'lucide-react'
import { useFetch, cop } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Semaforo } from './comunes.jsx'

const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }

const ESTADO_META = {
  PLANIFICADA:  { tono: 'azul',    label: 'Planificada' },
  EN_EJECUCION: { tono: 'verde',   label: 'En ejecución' },
  SUSPENDIDA:   { tono: 'ambar',   label: 'Suspendida' },
  FINALIZADA:   { tono: 'gris',    label: 'Finalizada' },
  LIQUIDADA:    { tono: 'violeta', label: 'Liquidada' },
}
const ORDEN = ['PLANIFICADA', 'EN_EJECUCION', 'SUSPENDIDA', 'FINALIZADA', 'LIQUIDADA']

export default function ResumenPortafolio({ refreshKey }) {
  const panelQ = useFetch('/obras/panel', [refreshKey])
  const p = panelQ.data

  // Silencioso mientras carga o si algo falla: la lista de obras (abajo) es la vista principal.
  if (panelQ.loading || panelQ.error || !p || !p.total_obras) return null

  const utilidad = n(p.utilidad_real_total)
  const IconUtil = utilidad > 0 ? TrendingUp : utilidad < 0 ? TrendingDown : Minus
  const tonoUtil = utilidad < 0 ? 'text-destructive' : 'text-success'
  const enAlerta = n(p.obras_en_alerta)

  return (
    <Card className="p-3.5" aria-label="Resumen del portafolio de obras">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Gauge className="size-4 text-muted-foreground" aria-hidden="true" />
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Portafolio de obras</h2>
        <span className="text-[11px] text-muted-foreground">· {p.obras_activas} activas de {p.total_obras}</span>
        {enAlerta > 0 && (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full border border-destructive/25 bg-destructive/10 px-2 py-0.5 text-[11px] font-semibold text-destructive">
            <TriangleAlert className="size-3" aria-hidden="true" />
            {enAlerta} en alerta
          </span>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Kpi etiqueta="Presupuestado" valor={p.ingreso_presupuestado_total} />
        <Kpi etiqueta="Gasto real" valor={p.gasto_total} />
        <div className="rounded-md bg-surface-2 px-3 py-2">
          <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">Utilidad real</dt>
          <dd className={`tabular inline-flex items-center gap-1 text-[14px] font-semibold ${tonoUtil}`}>
            <IconUtil className="size-3.5" aria-hidden="true" />{cop(utilidad)}
          </dd>
        </div>
      </dl>

      {/* Conteo por estado del portafolio (chips informativos, no filtran). */}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {ORDEN.filter((e) => p.por_estado?.[e]).map((e) => (
          <Semaforo key={e} tono={ESTADO_META[e].tono}>
            {ESTADO_META[e].label} <span className="tabular opacity-70">· {p.por_estado[e]}</span>
          </Semaforo>
        ))}
      </div>

      {/* Top de obras que sangran: el backend ya las ordena por severidad (rojo/alerta primero). */}
      {Array.isArray(p.obras) && p.obras.some((o) => o.alerta_margen || o.semaforo === 'rojo') && (
        <ul className="mt-3 space-y-1 border-t border-border-subtle pt-2.5">
          {p.obras.filter((o) => o.alerta_margen || o.semaforo === 'rojo').slice(0, 4).map((o) => (
            <li key={o.obra_id} className="flex items-center gap-2 text-[12px]">
              <Building2 className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate text-secondary-foreground">{o.nombre}</span>
              {o.alerta_margen && <TriangleAlert className="size-3.5 shrink-0 text-warning" aria-hidden="true" />}
              <span className="tabular shrink-0 text-muted-foreground">gasto {cop(n(o.gasto_total))}</span>
              <Semaforo tono={o.semaforo === 'rojo' ? 'rojo' : o.semaforo === 'amarillo' ? 'ambar' : 'verde'}>
                {o.semaforo === 'rojo' ? 'En pérdida' : o.semaforo === 'amarillo' ? 'Ajustada' : 'Rentable'}
              </Semaforo>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function Kpi({ etiqueta, valor }) {
  return (
    <div className="rounded-md bg-surface-2 px-3 py-2">
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{etiqueta}</dt>
      <dd className="tabular text-[14px] font-semibold text-foreground">{cop(n(valor))}</dd>
    </div>
  )
}
