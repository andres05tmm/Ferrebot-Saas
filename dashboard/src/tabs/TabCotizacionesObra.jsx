/*
 * TabCotizacionesObra — cotizador AIU del vertical construcción (Fase 2, flag `cotizaciones_aiu`).
 * La cotización por AIU (Administración/Imprevistos/Utilidad, IVA SÓLO sobre la utilidad) es la puerta
 * de entrada del vertical: al ganarse se convierte en Obra (1-1). Esta pestaña: listar/filtrar por
 * estado, el BUILDER (ítems dinámicos + desglose AIU en vivo), transicionar el estado por su ciclo de
 * vida, exportar el Excel (formato provisional) y convertir una GANADA en obra.
 *
 * Contrato de API (pinneado, contrato Ola A §3.1): /api/v1/cotizaciones-obra — GET lista, POST crea
 * (número PIM-0XX-AAAA autogenerado, editable), GET /{id} (detalle con `items` + `totales`), PUT /{id}
 * (builder), POST /{id}/estado, GET /{id}/exportar-excel (descarga .xlsx), POST /{id}/convertir-obra
 * (sólo admin, sólo si GANADA, idempotente). Los porcentajes AIU viajan como FRACCIÓN (0.05); en la UI
 * se capturan como PORCENTAJE (5) y se convierten. El total autoritativo es el del backend (función
 * pura money-safe); el desglose en vivo del builder es sólo una previsualización.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import {
  FileText, ChevronDown, ChevronRight, Plus, Search, Pencil, Trash2, Download,
  MapPin, Calculator, ArrowRightLeft, CalendarClock,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Semaforo, Chips, Campo, EstadoVacio, Esqueleto, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS } from './construccion/comunes.jsx'

// Estado de cotización (enum del ORM) → tono del semáforo + etiqueta humana.
const ESTADO = {
  BORRADOR: { tono: 'gris',  label: 'Borrador' },
  ENVIADA:  { tono: 'azul',  label: 'Enviada' },
  GANADA:   { tono: 'verde', label: 'Ganada' },
  PERDIDA:  { tono: 'rojo',  label: 'Perdida' },
  VENCIDA:  { tono: 'ambar', label: 'Vencida' },
}
const ORDEN_ESTADOS = ['BORRADOR', 'ENVIADA', 'GANADA', 'PERDIDA', 'VENCIDA']

// Transiciones permitidas por estado (espejo de modules/cotizacion_obra/service.py::_TRANSICIONES).
const TRANSICIONES = {
  BORRADOR: [{ estado: 'ENVIADA', label: 'Enviar' }, { estado: 'PERDIDA', label: 'Marcar perdida' }],
  ENVIADA:  [{ estado: 'GANADA', label: 'Marcar ganada' }, { estado: 'PERDIDA', label: 'Marcar perdida' }, { estado: 'VENCIDA', label: 'Marcar vencida' }],
  VENCIDA:  [{ estado: 'ENVIADA', label: 'Reenviar' }],
  GANADA:   [],
  PERDIDA:  [],
}

function metaEstado(estado) {
  return ESTADO[estado] || { tono: 'gris', label: estado || '—' }
}

// Desglose AIU en VIVO para el builder (previsualización; el total real lo da el backend). Espeja la
// función pura calcular_totales_cotizacion: IVA sólo sobre la utilidad. `pcts` en porcentaje (5 = 5%).
function calcularTotales(items, pcts) {
  const subtotal = items.reduce((acc, it) => acc + (Number(it.cantidad) || 0) * (Number(it.valor_unitario) || 0), 0)
  const administracion = subtotal * (Number(pcts.administracion_pct) || 0) / 100
  const imprevistos = subtotal * (Number(pcts.imprevistos_pct) || 0) / 100
  const utilidad = subtotal * (Number(pcts.utilidad_pct) || 0) / 100
  const iva_utilidad = utilidad * (Number(pcts.iva_pct) || 0) / 100
  return { subtotal, administracion, imprevistos, utilidad, iva_utilidad, total: subtotal + administracion + imprevistos + utilidad + iva_utilidad }
}

export default function TabCotizacionesObra() {
  const { refreshKey } = useOutletContext() ?? {}
  const listaQ = useFetch('/cotizaciones-obra', [refreshKey])
  useRealtimeEvent(['reconnected'], listaQ.refetch)

  const [q, setQ] = useState('')
  const [estado, setEstado] = useState(null)      // null = todas
  const [editando, setEditando] = useState(null)  // null | 'nueva' | detalle

  const cotizaciones = Array.isArray(listaQ.data) ? listaQ.data : []

  const conteos = cotizaciones.reduce((acc, c) => { acc[c.estado] = (acc[c.estado] || 0) + 1; return acc }, {})
  const chips = [
    { valor: null, label: 'Todas', conteo: cotizaciones.length },
    ...ORDEN_ESTADOS.filter((e) => conteos[e]).map((e) => ({ valor: e, label: metaEstado(e).label, tono: metaEstado(e).tono, conteo: conteos[e] })),
  ]

  const termino = q.trim().toLowerCase()
  const visibles = cotizaciones.filter((c) => {
    if (estado && c.estado !== estado) return false
    if (!termino) return true
    return [c.numero, c.nombre_obra, c.ubicacion].filter(Boolean).some((s) => String(s).toLowerCase().includes(termino))
  })

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar por número, obra o ubicación…" aria-label="Buscar cotización" className="pl-9" />
          </div>
          <button onClick={() => setEditando(editando === 'nueva' ? null : 'nueva')} className={`${BTN_PRIMARY} h-9 shrink-0`}>
            <Plus className="size-4" /> Nueva cotización
          </button>
        </div>
        {chips.length > 1 && (
          <div className="mt-2.5">
            <Chips opciones={chips} valor={estado} onChange={setEstado} ariaLabel="Filtrar cotizaciones por estado" />
          </div>
        )}
      </Card>

      {editando && (
        <CotizacionForm
          cotizacion={editando === 'nueva' ? null : editando}
          onClose={() => setEditando(null)}
          onGuardada={() => { setEditando(null); listaQ.refetch() }}
        />
      )}

      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <FileText className="size-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Cotizaciones {cotizaciones.length > 0 && <span className="tabular">· {visibles.length}</span>}
          </h2>
        </div>

        {listaQ.loading ? (
          <Esqueleto filas={4} />
        ) : cotizaciones.length === 0 ? (
          <EstadoVacio
            icono={FileText}
            titulo="Todavía no hay cotizaciones"
            descripcion="Arma una cotización por AIU con sus ítems; el total se calcula con el IVA sólo sobre la utilidad. Cuando el cliente la acepte, se convierte en obra con un clic."
          >
            <button onClick={() => setEditando('nueva')} className={`${BTN_PRIMARY} h-9`}>
              <Plus className="size-4" /> Crear la primera cotización
            </button>
          </EstadoVacio>
        ) : visibles.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Ninguna cotización coincide con el filtro.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {visibles.map((c) => (
              <CotizacionFila key={c.id} cotizacion={c} onEditar={setEditando} onCambio={listaQ.refetch} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

// ── Fila: cabecera clicable (expande) + detalle perezoso ────────────────────────────────────────
function CotizacionFila({ cotizacion, onEditar, onCambio }) {
  const [abierta, setAbierta] = useState(false)
  const est = metaEstado(cotizacion.estado)
  const panelId = `cotizacion-detalle-${cotizacion.id}`

  return (
    <li>
      <button
        type="button"
        onClick={() => setAbierta((v) => !v)}
        aria-expanded={abierta}
        aria-controls={panelId}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-fast hover:bg-surface-2"
      >
        <span className="grid size-9 shrink-0 place-items-center rounded-md bg-surface-2 text-muted-foreground">
          <FileText className="size-[18px]" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="tabular text-[12px] font-semibold text-muted-foreground">{cotizacion.numero}</span>
            <span className="truncate text-[14px] font-medium text-foreground">{cotizacion.nombre_obra}</span>
            <Semaforo tono={est.tono}>{est.label}</Semaforo>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
            {cotizacion.ubicacion && <span className="inline-flex items-center gap-1 truncate"><MapPin className="size-3" aria-hidden="true" />{cotizacion.ubicacion}</span>}
            <span className="inline-flex items-center gap-1"><CalendarClock className="size-3" aria-hidden="true" />{cotizacion.vigencia_dias} días</span>
          </div>
        </div>
        <span className="tabular text-[13px] font-semibold text-foreground">{cop(cotizacion.total)}</span>
        {abierta ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
      </button>

      {abierta && <CotizacionDetalle id={panelId} cotizacion={cotizacion} onEditar={onEditar} onCambio={onCambio} />}
    </li>
  )
}

function CotizacionDetalle({ id, cotizacion, onEditar, onCambio }) {
  // Detalle bajo demanda: GET /cotizaciones-obra/{id} trae `items` + `totales` (desglose AIU).
  const detalleQ = useFetch(`/cotizaciones-obra/${cotizacion.id}`)
  const detalle = detalleQ.data || cotizacion
  const items = Array.isArray(detalle.items) ? detalle.items : []
  const totales = detalle.totales
  const [ocupado, setOcupado] = useState(false)

  async function transicionar(dest) {
    setOcupado(true)
    try {
      const res = await api(`/cotizaciones-obra/${cotizacion.id}/estado`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ estado: dest.estado }),
      })
      if (!res.ok) { toast.error('No se pudo cambiar el estado'); return }
      toast.success(`Cotización ${metaEstado(dest.estado).label.toLowerCase()}`)
      detalleQ.refetch(); onCambio()
    } catch { toast.error('Error de conexión') } finally { setOcupado(false) }
  }

  async function descargarExcel() {
    try {
      const res = await api(`/cotizaciones-obra/${cotizacion.id}/exportar-excel`)
      if (!res.ok) { toast.error('No se pudo generar el Excel'); return }
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${detalle.numero || cotizacion.numero}.xlsx`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch { toast.error('Error de conexión') }
  }

  async function convertir() {
    if (!window.confirm('Convertir esta cotización ganada en obra. La obra hereda cliente, nombre y ubicación. ¿Continuar?')) return
    setOcupado(true)
    try {
      const res = await api(`/cotizaciones-obra/${cotizacion.id}/convertir-obra`, { method: 'POST' })
      if (res.ok) {
        const obra = await res.json()
        toast.success(`Obra creada (#${obra.id})`)
        onCambio()
      } else if (res.status === 403) {
        toast.error('Sólo un administrador convierte una cotización en obra')
      } else {
        toast.error('No se pudo convertir a obra')
      }
    } catch { toast.error('Error de conexión') } finally { setOcupado(false) }
  }

  const transiciones = TRANSICIONES[detalle.estado] || []
  const editable = detalle.estado === 'BORRADOR' || detalle.estado === 'ENVIADA'

  return (
    <div id={id} className="border-t border-border-subtle bg-surface-2/40 px-4 py-3.5">
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        {/* Ítems */}
        <div className="rounded-md border border-border-subtle bg-surface p-3">
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Ítems</h3>
          {detalleQ.loading ? (
            <p className="py-6 text-center text-[12px] text-muted-foreground">Cargando ítems…</p>
          ) : items.length === 0 ? (
            <p className="py-6 text-center text-[12px] text-muted-foreground">Sin ítems.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                    <th className="py-1 pr-2 font-medium">Descripción</th>
                    <th className="py-1 px-2 font-medium">Und</th>
                    <th className="py-1 px-2 text-right font-medium">Cant.</th>
                    <th className="py-1 px-2 text-right font-medium">Vr unit.</th>
                    <th className="py-1 pl-2 text-right font-medium">Vr total</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {items.map((it) => (
                    <tr key={it.id}>
                      <td className="py-1.5 pr-2 text-secondary-foreground">{it.descripcion}</td>
                      <td className="py-1.5 px-2 text-muted-foreground">{it.unidad}</td>
                      <td className="py-1.5 px-2 text-right tabular">{it.cantidad}</td>
                      <td className="py-1.5 px-2 text-right tabular">{cop(it.valor_unitario)}</td>
                      <td className="py-1.5 pl-2 text-right tabular font-medium">{cop(it.subtotal)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Desglose AIU + acciones */}
        <div className="space-y-3">
          <BloqueAIU totales={totales} pcts={detalle} />
          {transiciones.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-muted-foreground">Estado:</span>
              {transiciones.map((t) => (
                <button key={t.estado} onClick={() => transicionar(t)} disabled={ocupado} className={`${BTN_OUTLINE} h-8`}>{t.label}</button>
              ))}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
            {editable && (
              <button onClick={() => onEditar(detalle)} className={`${BTN_OUTLINE} h-8`}><Pencil className="size-3.5" /> Editar</button>
            )}
            <button onClick={descargarExcel} className={`${BTN_OUTLINE} h-8`}><Download className="size-3.5" /> Excel</button>
            {detalle.estado === 'GANADA' && (
              <button onClick={convertir} disabled={ocupado} className={`${BTN_PRIMARY} h-8`}><ArrowRightLeft className="size-3.5" /> Convertir a obra</button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// Desglose AIU (reusado por el detalle —desde `totales` del backend— y el builder —cálculo en vivo).
// `pcts` expone los porcentajes; en el detalle son FRACCIÓN (0.05), en el builder PORCENTAJE (5).
function BloqueAIU({ totales, pcts, enVivo = false }) {
  if (!totales) return null
  const pct = (frac) => {
    if (frac == null) return ''
    const v = enVivo ? Number(frac) : Number(frac) * 100
    return Number.isFinite(v) ? ` (${(+v.toFixed(2))}%)` : ''
  }
  const filas = [
    ['Subtotal', totales.subtotal, null],
    ['Administración', totales.administracion, pcts?.administracion_pct],
    ['Imprevistos', totales.imprevistos, pcts?.imprevistos_pct],
    ['Utilidad', totales.utilidad, pcts?.utilidad_pct],
    ['IVA sobre utilidad', totales.iva_utilidad, enVivo ? pcts?.iva_pct : pcts?.iva_sobre_utilidad_pct],
  ]
  return (
    <div className="rounded-md border border-border-subtle bg-surface p-3">
      <div className="mb-2 flex items-center gap-2">
        <Calculator className="size-4 text-muted-foreground" aria-hidden="true" />
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Desglose AIU</h3>
      </div>
      <dl className="space-y-1 text-[12px]">
        {filas.map(([etiqueta, valor, frac]) => (
          <div key={etiqueta} className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">{etiqueta}{frac != null && <span className="text-[11px]">{pct(frac)}</span>}</dt>
            <dd className="tabular text-secondary-foreground">{cop(valor)}</dd>
          </div>
        ))}
        <div className="mt-1 flex items-center justify-between gap-2 border-t border-border pt-1.5">
          <dt className="text-[12px] font-semibold text-foreground">Total contrato</dt>
          <dd className="tabular text-[14px] font-semibold text-primary">{cop(totales.total)}</dd>
        </div>
      </dl>
    </div>
  )
}

// ── Builder de cotización (alta / edición) ──────────────────────────────────────────────────────
const ITEM_VACIO = { descripcion: '', unidad: '', cantidad: '', valor_unitario: '' }

function CotizacionForm({ cotizacion, onClose, onGuardada }) {
  const edicion = !!cotizacion
  const clientesQ = useFetch('/clientes')
  const clientes = Array.isArray(clientesQ.data) ? clientesQ.data : []

  // Al editar, los porcentajes vienen como FRACCIÓN (0.05); en el builder se capturan como PORCENTAJE (5).
  const aPct = (frac) => (frac == null || frac === '' ? '' : String(+(Number(frac) * 100).toFixed(4)))
  const [f, setF] = useState({
    numero: cotizacion?.numero || '',
    cliente_id: cotizacion?.cliente_id ? String(cotizacion.cliente_id) : '',
    nombre_obra: cotizacion?.nombre_obra || '',
    ubicacion: cotizacion?.ubicacion || '',
    vigencia_dias: cotizacion?.vigencia_dias != null ? String(cotizacion.vigencia_dias) : '15',
    administracion_pct: aPct(cotizacion?.administracion_pct) || '0',
    imprevistos_pct: aPct(cotizacion?.imprevistos_pct) || '0',
    utilidad_pct: aPct(cotizacion?.utilidad_pct) || '0',
    iva_pct: aPct(cotizacion?.iva_sobre_utilidad_pct) || '19',
    condiciones: cotizacion?.condiciones || '',
  })
  const [items, setItems] = useState(
    cotizacion?.items?.length
      ? cotizacion.items.map((it) => ({ descripcion: it.descripcion, unidad: it.unidad, cantidad: String(it.cantidad), valor_unitario: String(it.valor_unitario) }))
      : [{ ...ITEM_VACIO }],
  )
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))
  const setItem = (i, k) => (e) => setItems((prev) => prev.map((it, idx) => (idx === i ? { ...it, [k]: e.target.value } : it)))
  const addItem = () => setItems((prev) => [...prev, { ...ITEM_VACIO }])
  const rmItem = (i) => setItems((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev))

  const totales = useMemo(() => calcularTotales(items, f), [items, f])

  async function guardar() {
    if (!f.nombre_obra.trim()) { toast.error('El nombre de la obra es obligatorio'); return }
    if (!f.cliente_id) { toast.error('Elige el cliente de la cotización'); return }
    const itemsLimpios = items.filter((it) => it.descripcion.trim() && it.unidad.trim())
    if (itemsLimpios.length === 0) { toast.error('Agrega al menos un ítem con descripción y unidad'); return }

    // Porcentaje (5) → fracción (0.05) como STRING (Decimal exacto en el backend).
    const frac = (v) => String((Number(v) || 0) / 100)
    const payload = {
      cliente_id: Number(f.cliente_id),
      nombre_obra: f.nombre_obra.trim(),
      ubicacion: f.ubicacion.trim() || null,
      vigencia_dias: Number(f.vigencia_dias) || 0,
      administracion_pct: frac(f.administracion_pct),
      imprevistos_pct: frac(f.imprevistos_pct),
      utilidad_pct: frac(f.utilidad_pct),
      iva_sobre_utilidad_pct: frac(f.iva_pct),
      condiciones: f.condiciones.trim() || null,
      items: itemsLimpios.map((it, i) => ({
        orden: i + 1, descripcion: it.descripcion.trim(), unidad: it.unidad.trim(),
        cantidad: String(Number(it.cantidad) || 0), valor_unitario: String(Number(it.valor_unitario) || 0),
      })),
    }
    // El número sólo se envía al CREAR (editable, autogenerado si va vacío); el PUT no lo acepta.
    if (!edicion && f.numero.trim()) payload.numero = f.numero.trim()

    setEnviando(true)
    try {
      const res = edicion
        ? await api(`/cotizaciones-obra/${cotizacion.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        : await api('/cotizaciones-obra', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!res.ok) { toast.error(edicion ? 'No se pudo guardar la cotización' : 'No se pudo crear la cotización'); return }
      toast.success(edicion ? 'Cotización actualizada' : 'Cotización creada')
      onGuardada()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-4">
      <h2 className="mb-3 inline-flex items-center gap-1.5 text-sm font-semibold">
        <FileText className="size-4" aria-hidden="true" /> {edicion ? `Editar cotización ${cotizacion.numero}` : 'Nueva cotización'}
      </h2>

      {/* Cabecera */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {!edicion && (
          <Campo label="Número" hint="Autogenerado si se deja vacío">
            <Input value={f.numero} onChange={set('numero')} placeholder="PIM-0XX-2026" className="h-9" />
          </Campo>
        )}
        <Campo label="Cliente" requerido>
          <select value={f.cliente_id} onChange={set('cliente_id')} className={SELECT_CLS}>
            <option value="">{clientesQ.loading ? 'Cargando clientes…' : 'Elige un cliente…'}</option>
            {clientes.map((c) => <option key={c.id} value={c.id}>{c.nombre}</option>)}
          </select>
        </Campo>
        <Campo label="Nombre de la obra" requerido>
          <Input value={f.nombre_obra} onChange={set('nombre_obra')} placeholder="Ej. Pavimentación vía La Estrella" className="h-9" />
        </Campo>
        <Campo label="Ubicación">
          <Input value={f.ubicacion} onChange={set('ubicacion')} placeholder="Municipio, tramo o dirección" className="h-9" />
        </Campo>
        <Campo label="Vigencia (días)">
          <Input type="number" min="0" value={f.vigencia_dias} onChange={set('vigencia_dias')} className="h-9" />
        </Campo>
      </div>

      {/* Ítems del builder */}
      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Ítems</h3>
          <button onClick={addItem} className={`${BTN_OUTLINE} h-8`}><Plus className="size-3.5" /> Agregar ítem</button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-[12px]">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                <th className="pb-1 pr-2 font-medium">Descripción</th>
                <th className="pb-1 px-2 font-medium">Unidad</th>
                <th className="pb-1 px-2 text-right font-medium">Cantidad</th>
                <th className="pb-1 px-2 text-right font-medium">Vr unitario</th>
                <th className="pb-1 px-2 text-right font-medium">Subtotal</th>
                <th className="pb-1 pl-2 w-8" aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {items.map((it, i) => {
                const sub = (Number(it.cantidad) || 0) * (Number(it.valor_unitario) || 0)
                return (
                  <tr key={i}>
                    <td className="py-1 pr-2"><Input value={it.descripcion} onChange={setItem(i, 'descripcion')} aria-label={`Descripción ítem ${i + 1}`} placeholder="Concepto" className="h-8" /></td>
                    <td className="py-1 px-2"><Input value={it.unidad} onChange={setItem(i, 'unidad')} aria-label={`Unidad ítem ${i + 1}`} placeholder="m3, m2, gl…" className="h-8 w-20" /></td>
                    <td className="py-1 px-2"><Input type="number" min="0" value={it.cantidad} onChange={setItem(i, 'cantidad')} aria-label={`Cantidad ítem ${i + 1}`} className="h-8 w-24 text-right" /></td>
                    <td className="py-1 px-2"><Input type="number" min="0" value={it.valor_unitario} onChange={setItem(i, 'valor_unitario')} aria-label={`Valor unitario ítem ${i + 1}`} className="h-8 w-28 text-right" /></td>
                    <td className="py-1 px-2 text-right tabular text-secondary-foreground">{cop(sub)}</td>
                    <td className="py-1 pl-2">
                      <button onClick={() => rmItem(i)} aria-label={`Quitar ítem ${i + 1}`} disabled={items.length === 1}
                        className="grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40">
                        <Trash2 className="size-3.5" />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* AIU: porcentajes + desglose en vivo */}
      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_1fr]">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-2">
          <Campo label="Administración %"><Input type="number" min="0" step="0.01" value={f.administracion_pct} onChange={set('administracion_pct')} className="h-9 text-right" /></Campo>
          <Campo label="Imprevistos %"><Input type="number" min="0" step="0.01" value={f.imprevistos_pct} onChange={set('imprevistos_pct')} className="h-9 text-right" /></Campo>
          <Campo label="Utilidad %"><Input type="number" min="0" step="0.01" value={f.utilidad_pct} onChange={set('utilidad_pct')} className="h-9 text-right" /></Campo>
          <Campo label="IVA sobre utilidad %"><Input type="number" min="0" step="0.01" value={f.iva_pct} onChange={set('iva_pct')} className="h-9 text-right" /></Campo>
        </div>
        <BloqueAIU totales={totales} pcts={f} enVivo />
      </div>

      <div className="mt-3">
        <Campo label="Condiciones y observaciones">
          <Input value={f.condiciones} onChange={set('condiciones')} placeholder="Validez, alcance, variaciones por combustible/materiales…" className="h-9" />
        </Campo>
      </div>

      <div className="mt-4 flex items-center justify-end gap-2">
        <button onClick={onClose} className={`${BTN_OUTLINE} h-10`}>Cancelar</button>
        <button onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-10`}>
          {enviando ? 'Guardando…' : edicion ? 'Guardar cambios' : 'Crear cotización'}
        </button>
      </div>
    </Card>
  )
}
