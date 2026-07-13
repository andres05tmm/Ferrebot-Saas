/*
 * comunes.jsx — átomos de UI compartidos por las pestañas del vertical construcción
 * (Obras, Máquinas, Herramientas, Trabajadores). Calca el vocabulario del dashboard (tokens
 * semánticos, Card, Input) y concentra lo que las cuatro repiten, para que cada tab se lea como el
 * vecino sin duplicar el mismo markup cuatro veces:
 *
 *   - `Semaforo`  — píldora de estado con PUNTO de color + etiqueta (nunca color solo: regla de
 *                   accesibilidad color-not-only). Es el semáforo visual de obra/máquina/herramienta.
 *   - `Chips`     — filtros de estado/tipo con conteo, resueltos en cliente (listas chicas del vertical).
 *   - `Campo`     — etiqueta VISIBLE asociada al control (mejor que placeholder-only); los tests la
 *                   ubican con getByLabelText por el texto de la etiqueta.
 *   - `EstadoVacio` — vacío CON PROPÓSITO (icono + qué es + cómo se llena), no una tabla desnuda.
 *   - `Esqueleto` — carga con placeholders (skeleton), no un spinner en medio del contenido.
 *
 * Presentación pura: sin fetch ni estado de datos. El color primario del tenant llega solo por los
 * tokens (`primary`), así que el theming white-label aplica sin tocar nada aquí.
 */

import { cloneElement, useId } from 'react'
import { Card } from '@/components/ui/card.jsx'

// Tonos del semáforo → clases de token semántico. `violeta` usa la var de chart (púrpura) por valor
// arbitrario porque no hay utilidad Tailwind directa; sirve para el estado "cerrado" (obra liquidada).
const TONOS = {
  verde:   { pildora: 'text-success bg-success/10 border-success/25',           punto: 'bg-success' },
  ambar:   { pildora: 'text-warning bg-warning/10 border-warning/25',           punto: 'bg-warning' },
  rojo:    { pildora: 'text-destructive bg-destructive/10 border-destructive/25', punto: 'bg-destructive' },
  azul:    { pildora: 'text-info bg-info/10 border-info/25',                     punto: 'bg-info' },
  gris:    { pildora: 'text-muted-foreground bg-surface-2 border-border',        punto: 'bg-muted-foreground' },
  violeta: {
    pildora: 'text-[hsl(var(--chart-5))] bg-[hsl(var(--chart-5)/0.12)] border-[hsl(var(--chart-5)/0.28)]',
    punto:   'bg-[hsl(var(--chart-5))]',
  },
}

/** Píldora de estado: punto de color + texto. El punto es decorativo (aria-hidden); el texto lleva el
 *  significado, así que el estado nunca depende solo del color. */
export function Semaforo({ tono = 'gris', children, className = '' }) {
  const t = TONOS[tono] || TONOS.gris
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold leading-none ${t.pildora} ${className}`}>
      <span className={`size-1.5 shrink-0 rounded-full ${t.punto}`} aria-hidden="true" />
      {children}
    </span>
  )
}

/** Chips de filtro (un solo valor activo). `opciones`: [{ valor, label, tono?, conteo? }]. El valor
 *  `null` representa "todas/todos". Filtro en cliente: las listas del vertical son chicas. */
export function Chips({ opciones, valor, onChange, ariaLabel }) {
  return (
    <div role="group" aria-label={ariaLabel} className="flex flex-wrap items-center gap-1.5">
      {opciones.map((op) => {
        const activo = op.valor === valor
        return (
          <button
            key={op.label}
            type="button"
            onClick={() => onChange(op.valor)}
            aria-pressed={activo}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 h-7 text-[12px] font-medium transition-colors duration-fast ${
              activo
                ? 'border-primary bg-primary-soft text-primary'
                : 'border-border text-muted-foreground hover:text-foreground hover:bg-surface-2'
            }`}
          >
            {op.tono && <span className={`size-1.5 shrink-0 rounded-full ${(TONOS[op.tono] || TONOS.gris).punto}`} aria-hidden="true" />}
            <span>{op.label}</span>
            {typeof op.conteo === 'number' && (
              <span className={`tabular text-[11px] ${activo ? 'text-primary' : 'text-muted-foreground'}`}>{op.conteo}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/** Campo de formulario: etiqueta VISIBLE asociada al control por `htmlFor`/`id` (no `aria-label`, que
 *  duplicaría el nombre). El asterisco de requerido y el `hint` quedan FUERA del <label> —el asterisco
 *  como hermano decorativo, el hint referenciado por `aria-describedby`— para que el nombre accesible
 *  del control sea exactamente la etiqueta (así los tests lo ubican con getByLabelText('Etiqueta')).
 *  `children` debe ser un único control (Input o select): se le inyecta el id por cloneElement. */
export function Campo({ label, requerido = false, hint, className = '', children }) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const control = cloneElement(children, {
    id,
    ...(hintId ? { 'aria-describedby': hintId } : {}),
  })
  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <span className="flex items-center gap-0.5">
        <label htmlFor={id} className="text-[11px] font-medium text-secondary-foreground">{label}</label>
        {requerido && <span className="text-destructive" aria-hidden="true">*</span>}
      </span>
      {control}
      {hint && <span id={hintId} className="text-[11px] text-muted-foreground">{hint}</span>}
    </div>
  )
}

/** Estado vacío con propósito: qué es esta pestaña y cómo se llena, con una acción opcional. */
export function EstadoVacio({ icono: Icono, titulo, descripcion, children }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {Icono && (
        <div className="mb-4 grid size-12 place-items-center rounded-full bg-primary-soft text-primary">
          <Icono className="size-6" aria-hidden="true" />
        </div>
      )}
      <h3 className="text-[15px] font-semibold text-foreground">{titulo}</h3>
      {descripcion && <p className="mt-1 max-w-sm text-[13px] leading-relaxed text-muted-foreground">{descripcion}</p>}
      {children && <div className="mt-5 flex flex-wrap items-center justify-center gap-2">{children}</div>}
    </div>
  )
}

/** Carga: filas placeholder (skeleton) en vez de un spinner en medio del contenido. */
export function Esqueleto({ filas = 4 }) {
  return (
    <ul className="divide-y divide-border-subtle" aria-hidden="true">
      {Array.from({ length: filas }).map((_, i) => (
        <li key={i} className="flex items-center gap-3 px-4 py-3">
          <div className="size-9 shrink-0 rounded-md bg-surface-2 animate-pulse" />
          <div className="flex-1 space-y-2">
            <div className="h-3 w-1/3 rounded bg-surface-2 animate-pulse" />
            <div className="h-2.5 w-1/2 rounded bg-surface-2 animate-pulse" />
          </div>
          <div className="h-5 w-16 rounded-full bg-surface-2 animate-pulse" />
        </li>
      ))}
    </ul>
  )
}

// Tonos del VALOR de un KPI → clase de color de texto. Nunca color-solo: cuando el signo importa, el
// consumidor añade flecha/etiqueta (Δ%, semáforo). `marca` = ámbar (acento), reservado a hitos, no a riesgo.
const KPI_TONO = {
  neutro:   'text-foreground',
  positivo: 'text-success',
  negativo: 'text-destructive',
  marca:    'text-primary',
}

/**
 * Kpi — tesela de indicador del vertical construcción. Concentra el KPI que repetían ResumenPortafolio
 * y CarteraAlquiler. El `valor` llega YA formateado (string o nodo: cop(), un conteo, '…' al cargar): el
 * átomo presenta, no formatea. Props:
 *   - label     etiqueta corta en versalitas.
 *   - valor     el número/nodo grande (tabular).
 *   - sublinea  texto pequeño debajo (desglose o hint); opcional.
 *   - tono      tiñe el valor: 'neutro'(def) | 'positivo' | 'negativo' | 'marca'.
 *   - tendencia nodo a la derecha del valor (flecha Δ% del cockpit); opcional.
 *   - variante  'plana'(def): caja compacta sobre bg-surface-2 (densa, dentro de otra Card).
 *               'card': envuelta en Card, teselas sueltas en una fila (uso de CarteraAlquiler).
 */
export function Kpi({ label, valor, sublinea, tono = 'neutro', tendencia = null, variante = 'plana', className = '' }) {
  const tonoCls = KPI_TONO[tono] || KPI_TONO.neutro
  const esCard = variante === 'card'
  const cuerpo = (
    <>
      <div className={`uppercase tracking-wider text-muted-foreground ${esCard ? 'text-[11px]' : 'text-[10px]'}`}>{label}</div>
      <div className={`flex items-baseline gap-1.5 font-semibold tabular-nums ${esCard ? 'text-lg' : 'text-[14px]'} ${tonoCls}`}>
        <span className="min-w-0 truncate">{valor}</span>
        {tendencia}
      </div>
      {sublinea && <div className="mt-0.5 text-[11px] text-muted-foreground">{sublinea}</div>}
    </>
  )
  return esCard
    ? <Card className={`p-3 ${className}`}>{cuerpo}</Card>
    : <div className={`rounded-md bg-surface-2 px-3 py-2 ${className}`}>{cuerpo}</div>
}

// Taxonomía de gasto del vertical construcción (spec 09, enum `categoria_gasto` del backend) con sus
// labels humanos. Única copia: la consumen el form de gasto de obra y la bandeja de revisión.
export const CATEGORIAS_GASTO_VERTICAL = [
  ['REPUESTOS', 'Repuestos'], ['MANTENIMIENTO_MAQUINA', 'Mantenimiento de máquina'], ['ALMUERZOS', 'Almuerzos'],
  ['TRANSPORTE_PERSONAL', 'Transporte de personal'], ['COMBUSTIBLE', 'Combustible'], ['PAPELERIA', 'Papelería'],
  ['SERVICIOS_PUBLICOS', 'Servicios públicos'], ['ARRIENDO', 'Arriendo'], ['IMPUESTOS', 'Impuestos'], ['OTRO', 'Otro'],
]

// Clases de botón compartidas (calcan el botón primario del vecino). La altura se pasa por `className`
// en cada uso (h-9 en toolbars, h-10 en formularios).
// `min-h-10 sm:min-h-0`: target táctil ≥40px en móvil sin tocar la altura por-uso (h-9/h-10); en sm+
// el min-height se libera y manda la altura densa de escritorio. Alinea con SELECT_CLS (h-10 en móvil).
export const BTN_PRIMARY =
  'inline-flex min-h-10 items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors duration-fast hover:bg-primary-hover disabled:opacity-60 sm:min-h-0'
export const BTN_OUTLINE =
  'inline-flex min-h-10 items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-3 text-sm font-medium text-secondary-foreground transition-colors duration-fast hover:bg-surface-2 disabled:opacity-60 sm:min-h-0'

// Select nativo con el mismo look que el Input del design system (borde/foco tokenizados).
// h-10 en móvil (target táctil), h-9 denso en escritorio.
export const SELECT_CLS =
  'h-10 w-full rounded-md border border-input bg-surface px-2 text-sm text-foreground transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 sm:h-9'
