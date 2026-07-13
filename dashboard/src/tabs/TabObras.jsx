/*
 * TabObras — obras del vertical construcción (Fase 1, flag `obras`). Corazón operativo del tenant:
 * cada obra es contra la que luego se imputan horas, asistencia, gastos y compras (Fases 3–5). Aquí,
 * la capa de Fase 1: listar/filtrar por estado, crear/editar, transicionar el estado por su ciclo de
 * vida y ver el detalle expandible con la bitácora de reportes diarios (que normalmente llega del bot).
 *
 * Contrato de API (pinneado): /api/v1/obras — GET lista, POST crea, GET /{id} (detalle con
 * `reportes_diarios`), PATCH /{id} (incl. cambio de `estado`), DELETE /{id} = soft delete. Los campos
 * JSON son los nombres de columna del ORM en español (nombre, ubicacion, fecha_inicio,
 * fecha_fin_estimada, estado…). El filtro por estado se resuelve en cliente (lista chica). Reusa
 * /clientes (módulo existente) para elegir el cliente al crear. Live: re-fetch ante 'reconnected'.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import {
  Building2, ChevronDown, ChevronRight, Plus, Search, Pencil, Trash2,
  CalendarDays, MapPin, ClipboardList, Camera, TriangleAlert, Ruler,
} from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog.jsx'
import PageHeader from '@/components/PageHeader.jsx'
import { Semaforo, Chips, Campo, EstadoVacio, Esqueleto, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS } from './construccion/comunes.jsx'
import PanelPresupuestoReal from './construccion/PanelPresupuestoReal.jsx'
import ResumenPortafolio from './construccion/ResumenPortafolio.jsx'

// Estado de obra (enum del ORM) → tono del semáforo + etiqueta humana. Cada estado un tono distinto:
// planificada (por arrancar) azul · en ejecución (sana) verde · suspendida (atención) ámbar ·
// finalizada (cerrada) gris · liquidada (cerrada e inmutable) violeta.
const OBRA = {
  PLANIFICADA:  { tono: 'azul',    label: 'Planificada' },
  EN_EJECUCION: { tono: 'verde',   label: 'En ejecución' },
  SUSPENDIDA:   { tono: 'ambar',   label: 'Suspendida' },
  FINALIZADA:   { tono: 'gris',    label: 'Finalizada' },
  LIQUIDADA:    { tono: 'violeta', label: 'Liquidada' },
}
const ORDEN_ESTADOS = ['PLANIFICADA', 'EN_EJECUCION', 'SUSPENDIDA', 'FINALIZADA', 'LIQUIDADA']

// Transiciones permitidas por estado actual (ciclo de vida). `confirmar` para pasos irreversibles.
const TRANSICIONES = {
  PLANIFICADA:  [{ estado: 'EN_EJECUCION', label: 'Iniciar ejecución' }],
  EN_EJECUCION: [{ estado: 'SUSPENDIDA', label: 'Suspender' }, { estado: 'FINALIZADA', label: 'Finalizar' }],
  SUSPENDIDA:   [{ estado: 'EN_EJECUCION', label: 'Reanudar' }],
  FINALIZADA:   [{ estado: 'LIQUIDADA', label: 'Liquidar', confirmar: true }],
  LIQUIDADA:    [],
}

function metaEstado(estado) {
  return OBRA[estado] || { tono: 'gris', label: estado || '—' }
}

export default function TabObras() {
  const { refreshKey } = useOutletContext() ?? {}
  const obrasQ = useFetch('/obras', [refreshKey])
  useRealtimeEvent(['reconnected'], obrasQ.refetch)

  const [q, setQ] = useState('')
  const [estado, setEstado] = useState(null)   // null = todas
  const [editando, setEditando] = useState(null)  // null | 'nueva' | obra

  const obras = Array.isArray(obrasQ.data) ? obrasQ.data : []

  // Conteo por estado para los chips (sobre el set completo, no el filtrado: es el panorama del portafolio).
  const conteos = obras.reduce((acc, o) => { acc[o.estado] = (acc[o.estado] || 0) + 1; return acc }, {})
  const chips = [
    { valor: null, label: 'Todas', conteo: obras.length },
    ...ORDEN_ESTADOS
      .filter((e) => conteos[e])
      .map((e) => ({ valor: e, label: metaEstado(e).label, tono: metaEstado(e).tono, conteo: conteos[e] })),
  ]

  const termino = q.trim().toLowerCase()
  const visibles = obras.filter((o) => {
    if (estado && o.estado !== estado) return false
    if (!termino) return true
    return [o.nombre, o.ubicacion, o.cliente_nombre].filter(Boolean).some((s) => String(s).toLowerCase().includes(termino))
  })

  return (
    <div className="space-y-3">
      {/* Header compartido de página (F2.5): título + acción principal; la toolbar queda para buscar/filtrar. */}
      <PageHeader
        icono={Building2}
        titulo="Obras"
        sublinea="Presupuesto vs real de cada obra: contra ellas se cargan horas, gastos y compras."
        acciones={(
          <button onClick={() => setEditando(editando === 'nueva' ? null : 'nueva')} className={`${BTN_PRIMARY} h-9 shrink-0`}>
            <Plus className="size-4" /> Nueva obra
          </button>
        )}
      />

      {/* Home de obra (Fase 8): foto agregada y cacheada del portafolio, arriba de la lista. */}
      <ResumenPortafolio refreshKey={refreshKey} />

      <Card className="p-3">
        <div className="relative">
          <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar obra por nombre, cliente o ubicación…" aria-label="Buscar obra" className="pl-9" />
        </div>
        {chips.length > 1 && (
          <div className="mt-2.5">
            <Chips opciones={chips} valor={estado} onChange={setEstado} ariaLabel="Filtrar obras por estado" />
          </div>
        )}
      </Card>

      {editando && (
        <ObraForm
          obra={editando === 'nueva' ? null : editando}
          onClose={() => setEditando(null)}
          onGuardada={() => { setEditando(null); obrasQ.refetch() }}
        />
      )}

      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <Building2 className="size-4 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Obras {obras.length > 0 && <span className="tabular">· {visibles.length}</span>}
          </h2>
        </div>

        {obrasQ.loading ? (
          <Esqueleto filas={4} />
        ) : obras.length === 0 ? (
          <EstadoVacio
            icono={Building2}
            titulo="Todavía no hay obras"
            descripcion="Una obra concentra su presupuesto y su gasto real: contra ella se cargan horas de máquina, asistencia, compras y gastos. Crea la primera para empezar a seguir su margen."
          >
            <button onClick={() => setEditando('nueva')} className={`${BTN_PRIMARY} h-9`}>
              <Plus className="size-4" /> Crear la primera obra
            </button>
          </EstadoVacio>
        ) : visibles.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Ninguna obra coincide con el filtro.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {visibles.map((o) => (
              <ObraFila key={o.id} obra={o} onEditar={() => setEditando(o)} onCambio={obrasQ.refetch} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

// ── Fila de obra: cabecera clicable (expande) + detalle perezoso ────────────────────────────────
function ObraFila({ obra, onEditar, onCambio }) {
  const [abierta, setAbierta] = useState(false)
  const est = metaEstado(obra.estado)
  const panelId = `obra-detalle-${obra.id}`

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
          <Building2 className="size-[18px]" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[14px] font-medium text-foreground">{obra.nombre}</span>
            <Semaforo tono={est.tono}>{est.label}</Semaforo>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
            <span className="truncate">{obra.cliente_nombre || `Cliente #${obra.cliente_id}`}</span>
            {obra.ubicacion && <span className="inline-flex items-center gap-1 truncate"><MapPin className="size-3" aria-hidden="true" />{obra.ubicacion}</span>}
            {obra.fecha_inicio && <span className="inline-flex items-center gap-1"><CalendarDays className="size-3" aria-hidden="true" />{obra.fecha_inicio}{obra.fecha_fin_estimada ? ` → ${obra.fecha_fin_estimada}` : ''}</span>}
          </div>
        </div>
        {abierta ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /> : <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
      </button>

      {abierta && <ObraDetalle id={panelId} obra={obra} onEditar={onEditar} onCambio={onCambio} />}
    </li>
  )
}

function ObraDetalle({ id, obra, onEditar, onCambio }) {
  // Detalle bajo demanda al expandir: GET /obras/{id} da los metadatos + conteos (en el resumen,
  // `reportes_diarios` es un CONTEO, no la lista). La bitácora completa vive en su sub-recurso dedicado
  // GET /obras/{id}/reportes-diarios (array plano), que se trae por separado.
  const detalleQ = useFetch(`/obras/${obra.id}`)
  const reportesQ = useFetch(`/obras/${obra.id}/reportes-diarios`)
  const detalle = detalleQ.data || obra
  const reportes = Array.isArray(reportesQ.data) ? reportesQ.data : []
  const [ocupado, setOcupado] = useState(false)
  const [confirmando, setConfirmando] = useState(null)   // 'liquidar' | 'archivar' | null

  async function transicionar(dest) {
    setOcupado(true)
    try {
      // La transición va por el endpoint dedicado PATCH /obras/{id}/estado (valida el ciclo de vida en
      // el servicio); el PATCH /obras/{id} de metadatos NO acepta `estado`. Un salto no permitido → 409.
      const res = await api(`/obras/${obra.id}/estado`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ estado: dest.estado }),
      })
      if (!res.ok) { toast.error('No se pudo cambiar el estado de la obra'); return }
      toast.success(`Obra ${metaEstado(dest.estado).label.toLowerCase()}`)
      detalleQ.refetch(); onCambio()
    } catch { toast.error('Error de conexión') } finally { setOcupado(false) }
  }

  async function eliminar() {
    try {
      const res = await api(`/obras/${obra.id}`, { method: 'DELETE' })
      if (res.ok) { toast.success('Obra archivada'); onCambio() }
      else toast.error('No se pudo archivar la obra')
    } catch { toast.error('Error de conexión') }
  }

  const transiciones = TRANSICIONES[detalle.estado] || []

  return (
    <div id={id} className="border-t border-border-subtle bg-surface-2/40 px-4 py-3.5 space-y-4">
      {/* PRESUPUESTO VS REAL — el diferenciador de Fase 3, arriba y a lo ancho. Al liquidar, refresca el
          detalle (para que el estado pase a LIQUIDADA) y el listado padre. */}
      <PanelPresupuestoReal obra={detalle} onCambio={() => { detalleQ.refetch(); onCambio() }} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_1.2fr]">
        {/* Metadatos + acciones */}
        <div className="space-y-3">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[12px]">
            <Dato etiqueta="Cliente" valor={detalle.cliente_nombre || `#${detalle.cliente_id}`} />
            <Dato etiqueta="Ubicación" valor={detalle.ubicacion} />
            <Dato etiqueta="Inicio" valor={detalle.fecha_inicio} />
            <Dato etiqueta="Fin estimada" valor={detalle.fecha_fin_estimada} />
            {detalle.fecha_fin_real && <Dato etiqueta="Fin real" valor={detalle.fecha_fin_real} />}
            {detalle.cotizacion_numero && <Dato etiqueta="Cotización" valor={detalle.cotizacion_numero} />}
          </dl>
          {detalle.notas && <p className="rounded-md bg-surface px-3 py-2 text-[12px] leading-relaxed text-secondary-foreground">{detalle.notas}</p>}

          {transiciones.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-muted-foreground">Cambiar estado:</span>
              {transiciones.map((t) => (
                <button key={t.estado} disabled={ocupado} className={`${BTN_OUTLINE} h-8`}
                  onClick={() => (t.confirmar ? setConfirmando({ tipo: 'liquidar', dest: t }) : transicionar(t))}>
                  {t.label}
                </button>
              ))}
            </div>
          )}

          <div className="flex items-center gap-1.5 pt-0.5">
            <button onClick={onEditar} className={`${BTN_OUTLINE} h-8`}><Pencil className="size-3.5" /> Editar</button>
            <button onClick={() => setConfirmando({ tipo: 'archivar' })} className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-surface px-3 text-sm font-medium text-destructive transition-colors duration-fast hover:bg-destructive/10">
              <Trash2 className="size-3.5" /> Archivar
            </button>
          </div>

          {/* Confirmaciones destructivas con contexto (F2.5): el alert-dialog reemplaza al window.confirm
              genérico; archivar una obra con plata encima muestra su gasto real acumulado. */}
          <AlertDialog open={confirmando != null} onOpenChange={(o) => { if (!o) setConfirmando(null) }}>
            {confirmando?.tipo === 'archivar' && (
              <DialogoArchivar obra={detalle}
                onConfirmar={() => { setConfirmando(null); eliminar() }} />
            )}
            {confirmando?.tipo === 'liquidar' && (
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>¿Liquidar la obra «{detalle.nombre}»?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Liquidar congela el snapshot financiero de forma definitiva: la obra no admite más
                    gastos, horas ni compras. Esta acción no se puede deshacer.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancelar</AlertDialogCancel>
                  <AlertDialogAction onClick={() => { const d = confirmando.dest; setConfirmando(null); transicionar(d) }}>
                    Liquidar obra
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            )}
          </AlertDialog>
        </div>

        {/* Reportes diarios (bitácora de campo) */}
        <div className="rounded-md border border-border-subtle bg-surface p-3">
          <div className="mb-2.5 flex items-center gap-2">
            <ClipboardList className="size-4 text-muted-foreground" aria-hidden="true" />
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Reportes diarios</h3>
          </div>
          {reportesQ.loading ? (
            <p className="py-6 text-center text-[12px] text-muted-foreground">Cargando reportes…</p>
          ) : reportes.length === 0 ? (
            <div className="py-6 text-center">
              <p className="text-[12px] font-medium text-foreground">Sin reportes de campo todavía</p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">Llegan del bot de Telegram o se registran en obra: avance, m² / m³ e incidentes del día.</p>
            </div>
          ) : (
            <ol className="space-y-2.5">
              {reportes.map((r) => <ReporteItem key={r.id} reporte={r} />)}
            </ol>
          )}
        </div>
      </div>
    </div>
  )
}

function ReporteItem({ reporte }) {
  const fotos = Array.isArray(reporte.foto_urls) ? reporte.foto_urls : []
  return (
    <li className="relative border-l border-border pl-3.5">
      <span className="absolute -left-[3px] top-1.5 size-1.5 rounded-full bg-primary" aria-hidden="true" />
      <div className="flex items-center gap-2">
        <span className="tabular text-[12px] font-medium text-foreground">{reporte.fecha}</span>
        {reporte.reportado_por && <span className="text-[11px] text-muted-foreground">· {reporte.reportado_por}</span>}
        {reporte.origen_registro === 'TELEGRAM_BOT' && <span className="text-[10px] text-muted-foreground">(bot)</span>}
      </div>
      {reporte.avance_descripcion && <p className="mt-0.5 text-[12px] leading-relaxed text-secondary-foreground">{reporte.avance_descripcion}</p>}
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
        {reporte.m2_ejecutados != null && <span className="inline-flex items-center gap-1"><Ruler className="size-3" aria-hidden="true" />{reporte.m2_ejecutados} m²</span>}
        {reporte.m3_ejecutados != null && <span className="inline-flex items-center gap-1"><Ruler className="size-3" aria-hidden="true" />{reporte.m3_ejecutados} m³</span>}
        {fotos.length > 0 && <span className="inline-flex items-center gap-1"><Camera className="size-3" aria-hidden="true" />{fotos.length} foto(s)</span>}
      </div>
      {reporte.incidentes && (
        <p className="mt-1 inline-flex items-start gap-1 text-[11px] text-warning">
          <TriangleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />{reporte.incidentes}
        </p>
      )}
    </li>
  )
}

// Confirmación de archivado con contexto: pide el gasto real de la obra y lo pone en la advertencia —
// archivar una obra con plata encima saca sus cifras del panel/rollup sin rastro visible (hallazgo F1).
function DialogoArchivar({ obra, onConfirmar }) {
  const gastoQ = useFetch(`/obras/${obra.id}/gasto-real`)
  const d = gastoQ.data || {}
  const gasto = Number(d.gasto_total ?? d.total ?? 0)
  const activa = obra.estado === 'EN_EJECUCION' || obra.estado === 'FINALIZADA'
  return (
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>¿Archivar la obra «{obra.nombre}»?</AlertDialogTitle>
        <AlertDialogDescription>
          {gastoQ.loading
            ? 'Calculando el gasto real de la obra…'
            : gasto > 0
              ? `Esta obra lleva ${cop(gasto)} de gasto real registrado. Al archivarla, sus cifras salen del panel y del rollup del portafolio.`
              : 'Dejará de aparecer en el listado y en el panel.'}
          {activa && ' Además sigue ' + (obra.estado === 'EN_EJECUCION' ? 'EN EJECUCIÓN' : 'FINALIZADA sin liquidar') + '.'}
        </AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel>Cancelar</AlertDialogCancel>
        <AlertDialogAction variant="destructive" onClick={onConfirmar} disabled={gastoQ.loading}>
          Archivar de todas formas
        </AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  )
}

function Dato({ etiqueta, valor }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{etiqueta}</dt>
      <dd className="truncate text-secondary-foreground">{valor || '—'}</dd>
    </div>
  )
}

// ── Formulario de alta/edición ──────────────────────────────────────────────────────────────────
function ObraForm({ obra, onClose, onGuardada }) {
  const edicion = !!obra
  const clientesQ = useFetch('/clientes')
  const clientes = Array.isArray(clientesQ.data) ? clientesQ.data : []

  const [f, setF] = useState({
    nombre: obra?.nombre || '',
    cliente_id: obra?.cliente_id ? String(obra.cliente_id) : '',
    ubicacion: obra?.ubicacion || '',
    fecha_inicio: obra?.fecha_inicio || '',
    fecha_fin_estimada: obra?.fecha_fin_estimada || '',
    notas: obra?.notas || '',
  })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function guardar() {
    if (!f.nombre.trim()) { toast.error('El nombre de la obra es obligatorio'); return }
    if (!f.cliente_id) { toast.error('Elige el cliente de la obra'); return }
    if (f.fecha_inicio && f.fecha_fin_estimada && f.fecha_fin_estimada < f.fecha_inicio) {
      toast.error('La fecha fin estimada no puede ser anterior al inicio'); return
    }
    const payload = {
      nombre: f.nombre.trim(),
      cliente_id: Number(f.cliente_id),
      ubicacion: f.ubicacion.trim() || null,
      notas: f.notas.trim() || null,
    }
    // En EDICIÓN, un campo de fecha vacío significa LIMPIARLA (null explícito); antes se omitía del
    // payload y una fecha equivocada quedaba imposible de borrar (hallazgo F1). Al crear, vacío = no enviar.
    if (edicion) {
      payload.fecha_inicio = f.fecha_inicio || null
      payload.fecha_fin_estimada = f.fecha_fin_estimada || null
    } else {
      if (f.fecha_inicio) payload.fecha_inicio = f.fecha_inicio
      if (f.fecha_fin_estimada) payload.fecha_fin_estimada = f.fecha_fin_estimada
    }

    setEnviando(true)
    try {
      const res = edicion
        ? await api(`/obras/${obra.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        : await api('/obras', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (!res.ok) { toast.error(edicion ? 'No se pudo guardar la obra' : 'No se pudo crear la obra'); return }
      toast.success(edicion ? 'Obra actualizada' : 'Obra creada')
      onGuardada()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-4">
      <h2 className="mb-3 inline-flex items-center gap-1.5 text-sm font-semibold">
        <Building2 className="size-4" aria-hidden="true" /> {edicion ? 'Editar obra' : 'Nueva obra'}
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Campo label="Nombre de la obra" requerido className="sm:col-span-2">
          <Input value={f.nombre} onChange={set('nombre')} placeholder="Ej. Pavimentación vía La Estrella" className="h-9" />
        </Campo>
        <Campo label="Cliente" requerido>
          <select value={f.cliente_id} onChange={set('cliente_id')} className={SELECT_CLS}>
            <option value="">{clientesQ.loading ? 'Cargando clientes…' : 'Elige un cliente…'}</option>
            {clientes.map((c) => <option key={c.id} value={c.id}>{c.nombre}</option>)}
          </select>
        </Campo>
        <Campo label="Ubicación">
          <Input value={f.ubicacion} onChange={set('ubicacion')} placeholder="Municipio, tramo o dirección" className="h-9" />
        </Campo>
        <Campo label="Fecha de inicio">
          <Input type="date" value={f.fecha_inicio} onChange={set('fecha_inicio')} className="h-9" />
        </Campo>
        <Campo label="Fin estimada">
          <Input type="date" value={f.fecha_fin_estimada} onChange={set('fecha_fin_estimada')} className="h-9" />
        </Campo>
        <Campo label="Notas" className="sm:col-span-2">
          <Input value={f.notas} onChange={set('notas')} placeholder="Alcance, condiciones o recordatorios" className="h-9" />
        </Campo>
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <button onClick={onClose} className={`${BTN_OUTLINE} h-10`}>Cancelar</button>
        <button onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-10`}>
          {enviando ? 'Guardando…' : edicion ? 'Guardar cambios' : 'Crear obra'}
        </button>
      </div>
    </Card>
  )
}
