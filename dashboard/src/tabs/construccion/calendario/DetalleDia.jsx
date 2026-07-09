/*
 * DetalleDia — tarjeta de detalle del día seleccionado en el calendario de obra.
 *
 * GET /obras/calendario/dia?fecha[+filtros] devuelve TODO lo del día en secciones. Aquí se presentan en
 * bloques COLAPSABLES con conteo en el header; la VISTA activa (Todos | Obras | Máquinas | Trabajadores)
 * decide qué bloques se muestran ('todos' = todos). SIN cifras de dinero: el contrato no las trae.
 *
 *   Máquinas       (todos|maquinas)     horas por máquina: operador · obra · trabajadas/facturables
 *   Obras          (todos|obras)        reportes diarios (avance, m²/m³, incidentes, fotos) + consumos
 *   Trabajadores   (todos|trabajadores) asistencia: horas + extras + ausencia (Semaforo)
 *   Mantenimientos (todos|maquinas)     hechos + próximos (Semaforo ámbar)
 *   Planeado       (todos + porción)    asignaciones máquina→obra, trabajador→obra e hitos de obra
 */
import { useState } from 'react'
import {
  Truck, HardHat, Users, Wrench, Package, ClipboardList, CalendarDays, TriangleAlert, Camera, Ruler, X, Plus,
} from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { useAuth } from '@/hooks/useAuth'
import { Card } from '@/components/ui/card.jsx'
import { Semaforo, EstadoVacio, Esqueleto, BTN_OUTLINE } from '../comunes.jsx'
import { qsEntidad, fechaLarga, hoyStrCO } from './util.js'
import FormAsignacionMaquina from './FormAsignacionMaquina.jsx'
import FormAsignacionTrabajador from './FormAsignacionTrabajador.jsx'

const arr = (x) => (Array.isArray(x) ? x : [])

export default function DetalleDia({ fecha, filtros, onCerrar, onCambio }) {
  const q = useFetch(fecha ? `/obras/calendario/dia?fecha=${fecha}${qsEntidad(filtros)}` : null, [])
  const admin = useAuth().isAdmin()
  // Refresca el detalle del día y avisa al contenedor para que repida el mes (los dots) sin esperar SSE.
  const recargar = () => { q.refetch(); onCambio?.() }
  const d = q.data || {}
  const vista = filtros.vista
  const ver = (s) => vista === 'todos' || vista === s

  const horas = arr(d.horas_maquina)
  const reportes = arr(d.reportes)
  const consumos = arr(d.consumos)
  const asistencia = arr(d.asistencia)
  const mantenimientos = arr(d.mantenimientos)
  const proximos = arr(d.proximos_mantenimientos)
  const planeadoMaq = arr(d.planeado_maquinas)
  const planeadoTrab = arr(d.planeado_trabajadores)
  const hitos = arr(d.hitos)

  // Planeado según la vista: en 'todos' todo; en cada vista solo su porción (máquinas/hitos/trabajadores).
  const plMaq = vista === 'trabajadores' ? [] : planeadoMaq
  const plTrab = vista === 'maquinas' || vista === 'obras' ? [] : planeadoTrab
  const plHitos = vista === 'maquinas' || vista === 'trabajadores' ? [] : hitos
  const planeadoTotal = plMaq.length + plTrab.length + plHitos.length

  const total =
    (ver('maquinas') ? horas.length + mantenimientos.length + proximos.length : 0) +
    (ver('obras') ? reportes.length + consumos.length : 0) +
    (ver('trabajadores') ? asistencia.length : 0) +
    planeadoTotal

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border-subtle px-3.5 py-2.5">
        <CalendarDays className="size-4 text-primary" aria-hidden="true" />
        <h2 className="flex-1 text-[13px] font-semibold text-foreground">{fechaLarga(fecha)}</h2>
        <button type="button" onClick={onCerrar} aria-label="Cerrar detalle del día"
          className="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-surface-2">
          <X className="size-4" />
        </button>
      </div>

      {q.loading ? (
        <Esqueleto filas={3} />
      ) : total === 0 ? (
        <EstadoVacio icono={CalendarDays} titulo="Sin actividad este día"
          descripcion="No hay horas de máquina, reportes, asistencia, mantenimientos ni planeación para el filtro seleccionado." />
      ) : (
        <div className="space-y-2 p-3">
          {ver('maquinas') && <SeccionMaquinas horas={horas} />}
          {ver('obras') && <SeccionObras reportes={reportes} consumos={consumos} />}
          {ver('trabajadores') && <SeccionTrabajadores asistencia={asistencia} />}
          {ver('maquinas') && <SeccionMantenimientos hechos={mantenimientos} proximos={proximos} />}
          <SeccionPlaneado
            maquinas={plMaq} trabajadores={plTrab} hitos={plHitos}
            admin={admin} fecha={fecha} onCambio={recargar}
          />
        </div>
      )}
    </Card>
  )
}

// ── Bloque colapsable genérico (nativo <details>): oculto si no hay nada que mostrar ─────────────
function Seccion({ icono: Icono, titulo, conteo, children }) {
  if (!conteo) return null
  return (
    <details open className="rounded-md border border-border-subtle bg-surface">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-[12px] font-semibold text-foreground">
        <Icono className="size-4 text-muted-foreground" aria-hidden="true" />
        <span>{titulo}</span>
        <span className="ml-auto tabular text-[11px] text-muted-foreground">{conteo}</span>
      </summary>
      <div className="space-y-2 px-3 pb-3 pt-1">{children}</div>
    </details>
  )
}

function Linea({ children }) {
  return <div className="rounded-md bg-surface-2/50 px-2.5 py-1.5 text-[12px] text-secondary-foreground">{children}</div>
}

function SeccionMaquinas({ horas }) {
  return (
    <Seccion icono={Truck} titulo="Máquinas" conteo={horas.length}>
      {horas.map((h) => (
        <Linea key={h.id}>
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">{h.maquina || `Máquina #${h.maquina_id}`}</span>
            <span className="ml-auto tabular text-[11px] text-muted-foreground">{h.horas_trabajadas}/{h.horas_facturables} h</span>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
            {h.operador && <span>{h.operador}</span>}
            {h.obra && <span>· {h.obra}</span>}
            {h.origen_registro && <span>· {h.origen_registro.toLowerCase()}</span>}
          </div>
          {h.observaciones && <p className="mt-0.5 text-[11px] text-muted-foreground">{h.observaciones}</p>}
        </Linea>
      ))}
    </Seccion>
  )
}

function SeccionObras({ reportes, consumos }) {
  return (
    <Seccion icono={HardHat} titulo="Obras" conteo={reportes.length + consumos.length}>
      {reportes.map((r) => (
        <Linea key={`r-${r.id}`}>
          <div className="font-medium text-foreground">{r.obra || `Obra #${r.obra_id}`}</div>
          {r.avance_descripcion && <p className="mt-0.5 leading-relaxed">{r.avance_descripcion}</p>}
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
            {r.m2_ejecutados != null && <span className="inline-flex items-center gap-1"><Ruler className="size-3" aria-hidden="true" />{r.m2_ejecutados} m²</span>}
            {r.m3_ejecutados != null && <span className="inline-flex items-center gap-1"><Ruler className="size-3" aria-hidden="true" />{r.m3_ejecutados} m³</span>}
            {arr(r.foto_urls).length > 0 && <span className="inline-flex items-center gap-1"><Camera className="size-3" aria-hidden="true" />{arr(r.foto_urls).length} foto(s)</span>}
            {r.reportado_por && <span>· {r.reportado_por}</span>}
          </div>
          {r.incidentes && (
            <p className="mt-1 inline-flex items-start gap-1 text-[11px] text-warning">
              <TriangleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />{r.incidentes}
            </p>
          )}
        </Linea>
      ))}
      {consumos.map((c) => (
        <Linea key={`c-${c.id}`}>
          <span className="inline-flex items-center gap-1.5">
            <Package className="size-3.5 text-muted-foreground" aria-hidden="true" />
            <span className="font-medium text-foreground">{c.producto || `Producto #${c.producto_id}`}</span>
            <span className="tabular text-muted-foreground">× {c.cantidad}</span>
          </span>
          {c.obra && <span className="ml-1 text-[11px] text-muted-foreground">· {c.obra}</span>}
        </Linea>
      ))}
    </Seccion>
  )
}

function SeccionTrabajadores({ asistencia }) {
  return (
    <Seccion icono={Users} titulo="Trabajadores" conteo={asistencia.length}>
      {asistencia.map((a) => {
        const extras = Number(a.horas_extra_diurnas || 0) + Number(a.horas_extra_nocturnas || 0) + Number(a.horas_dominical_festivo || 0)
        return (
          <Linea key={a.id}>
            <div className="flex items-center gap-2">
              <span className="font-medium text-foreground">{a.trabajador || `Trabajador #${a.trabajador_id}`}</span>
              {a.ausencia
                ? <Semaforo tono="ambar" className="ml-auto">{a.ausencia}</Semaforo>
                : <Semaforo tono="verde" className="ml-auto">Presente</Semaforo>}
            </div>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-foreground">
              <span>{a.obra || 'Administrativo'}</span>
              <span>· {a.horas_trabajadas} h</span>
              {extras > 0 && <span>· {extras} h extra</span>}
            </div>
          </Linea>
        )
      })}
    </Seccion>
  )
}

function SeccionMantenimientos({ hechos, proximos }) {
  return (
    <Seccion icono={Wrench} titulo="Mantenimientos" conteo={hechos.length + proximos.length}>
      {hechos.map((m) => (
        <Linea key={`m-${m.id}`}>
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">{m.maquina || `Máquina #${m.maquina_id}`}</span>
            {m.tipo && <Semaforo tono="azul" className="ml-auto">{m.tipo}</Semaforo>}
          </div>
          {m.descripcion && <p className="mt-0.5 text-[11px] text-muted-foreground">{m.descripcion}</p>}
          {m.proximo_en_fecha && <p className="mt-0.5 text-[11px] text-muted-foreground">Próximo: {m.proximo_en_fecha}</p>}
        </Linea>
      ))}
      {proximos.map((m, i) => (
        <Linea key={`p-${m.maquina_id}-${i}`}>
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">{m.maquina || `Máquina #${m.maquina_id}`}</span>
            <Semaforo tono="ambar" className="ml-auto">Próximo</Semaforo>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
            {m.tipo && <span>{m.tipo}</span>}
            {m.descripcion && <span>· {m.descripcion}</span>}
          </div>
        </Linea>
      ))}
    </Seccion>
  )
}

// Planeado: además de listar asignaciones/hitos, el ADMIN puede asignar máquina/trabajador (forms inline)
// y CERRAR una asignación activa (PATCH activa=false + fecha_fin hoy). Se renderiza también con conteo 0
// para el admin (necesita el toolbar en un día sin planeado); para el vendedor se oculta si está vacío.
function SeccionPlaneado({ maquinas, trabajadores, hitos, admin, fecha, onCambio }) {
  const [form, setForm] = useState(null) // 'maquina' | 'trabajador' | null
  const conteo = maquinas.length + trabajadores.length + hitos.length
  if (!admin && !conteo) return null

  async function cerrar(path, nombre) {
    if (!window.confirm(`¿Cerrar la asignación de ${nombre}? Se marcará como finalizada hoy.`)) return
    try {
      const res = await api(path, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ activa: false, fecha_fin: hoyStrCO() }),
      })
      if (res.ok) { toast.success('Asignación cerrada'); onCambio?.() }
      else toast.error('No se pudo cerrar la asignación')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <details open className="rounded-md border border-border-subtle bg-surface">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-[12px] font-semibold text-foreground">
        <ClipboardList className="size-4 text-muted-foreground" aria-hidden="true" />
        <span>Planeado</span>
        <span className="ml-auto tabular text-[11px] text-muted-foreground">{conteo}</span>
      </summary>
      <div className="space-y-2 px-3 pb-3 pt-1">
        {admin && (
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => setForm((v) => (v === 'maquina' ? null : 'maquina'))}
              aria-expanded={form === 'maquina'} className={`${BTN_OUTLINE} h-7 px-2 text-[12px]`}>
              <Plus className="size-3.5" /> Asignar máquina
            </button>
            <button type="button" onClick={() => setForm((v) => (v === 'trabajador' ? null : 'trabajador'))}
              aria-expanded={form === 'trabajador'} className={`${BTN_OUTLINE} h-7 px-2 text-[12px]`}>
              <Plus className="size-3.5" /> Asignar trabajador
            </button>
          </div>
        )}
        {admin && form === 'maquina' && (
          <FormAsignacionMaquina fechaInicioDefault={fecha}
            onExito={() => { setForm(null); onCambio?.() }} onCancelar={() => setForm(null)} />
        )}
        {admin && form === 'trabajador' && (
          <FormAsignacionTrabajador fechaInicioDefault={fecha}
            onExito={() => { setForm(null); onCambio?.() }} onCancelar={() => setForm(null)} />
        )}
        {maquinas.map((p) => (
          <Linea key={`pm-${p.asignacion_id}`}>
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <span className="inline-flex items-center gap-1.5">
                  <Truck className="size-3.5 text-muted-foreground" aria-hidden="true" />
                  <span className="font-medium text-foreground">{p.maquina || `Máquina #${p.maquina_id}`}</span>
                  <span className="text-muted-foreground">→ {p.obra || `Obra #${p.obra_id}`}</span>
                </span>
                <div className="mt-0.5 text-[11px] text-muted-foreground">
                  {p.operador && <span>{p.operador} · </span>}{p.fecha_inicio} → {p.fecha_fin || '—'}
                </div>
              </div>
              {admin && (
                <button type="button"
                  onClick={() => cerrar(`/maquinas/${p.maquina_id}/asignaciones/${p.asignacion_id}`, p.maquina || `Máquina #${p.maquina_id}`)}
                  className="shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:bg-surface-2 hover:text-destructive">
                  Cerrar
                </button>
              )}
            </div>
          </Linea>
        ))}
        {trabajadores.map((p) => (
          <Linea key={`pt-${p.asignacion_id}`}>
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <span className="inline-flex items-center gap-1.5">
                  <Users className="size-3.5 text-muted-foreground" aria-hidden="true" />
                  <span className="font-medium text-foreground">{p.trabajador || `Trabajador #${p.trabajador_id}`}</span>
                  <span className="text-muted-foreground">→ {p.obra || `Obra #${p.obra_id}`}</span>
                </span>
                <div className="mt-0.5 text-[11px] text-muted-foreground">{p.fecha_inicio} → {p.fecha_fin || '—'}</div>
              </div>
              {admin && (
                <button type="button"
                  onClick={() => cerrar(`/trabajadores/${p.trabajador_id}/asignaciones/${p.asignacion_id}`, p.trabajador || `Trabajador #${p.trabajador_id}`)}
                  className="shrink-0 rounded-md px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:bg-surface-2 hover:text-destructive">
                  Cerrar
                </button>
              )}
            </div>
          </Linea>
        ))}
        {hitos.map((h, i) => (
          <Linea key={`h-${h.obra_id}-${i}`}>
            <div className="flex items-center gap-2">
              <span className="font-medium text-foreground">{h.obra || `Obra #${h.obra_id}`}</span>
              <Semaforo tono="azul" className="ml-auto">{h.hito}</Semaforo>
            </div>
            {h.estado && <p className="mt-0.5 text-[11px] text-muted-foreground">{h.estado}</p>}
          </Linea>
        ))}
      </div>
    </details>
  )
}
