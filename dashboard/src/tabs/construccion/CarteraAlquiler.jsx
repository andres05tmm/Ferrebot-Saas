/*
 * CarteraAlquiler — sección de "cartera de alquiler" (crédito por alquiler de maquinaria) del vertical
 * construcción (Fase 5, flag `cartera_alquiler`, que depende de `fiados`). Vive DENTRO de TabCartera
 * (la página del pack cobranza, admin-only) como una segunda sección, visible solo si la empresa tiene
 * la capacidad activa. Reusa el ledger de fiados como fuente del saldo, sin duplicarlo:
 *   consumido = clientes.saldo_fiado   ·   disponible = cupo − consumido
 * (ver docs/research/pim-fase5-cartera-diseno.md §1.2). El cupo solo aporta el TOPE.
 *
 * Contrato de API asumido (a conciliar con el router /cartera-alquiler de F5 — el integrador reconcilia):
 *   GET  /cartera-alquiler/cupos          → [{ id, cliente_id, cliente_nombre, cupo, consumido,
 *                                              disponible?, vigente_desde, vigente_hasta, activo, notas }]
 *   POST /cartera-alquiler/cupos          ← { cliente_id, cupo, vigente_desde, vigente_hasta?, notas? }
 *   PUT  /cartera-alquiler/cupos/{id}     ← { cupo?, vigente_desde?, vigente_hasta?, activo?, notas? }
 *   GET  /cartera-alquiler/colitas        → [{ cliente_id, cliente_nombre, obra_id, obra_nombre, saldo,
 *                                              dias_sin_abono, ultimo_abono_en }]
 *   GET  /cartera-alquiler/obras/{obra_id}→ { obra_id, obra_nombre, cliente_nombre, saldo,
 *                                              cargos: [{ id, registro_horas_id, maquina_nombre, fecha,
 *                                                         horas_facturables, monto }],
 *                                              abonos: [{ id, monto, fecha }] }
 *   GET  /cartera-alquiler/config         → { activo, dias_colita, cadencia_aviso_dias }
 * Los abonos se registran por el router de fiados existente (POST /fiados/{id}/abono), NO aquí. El
 * CONSUMO (cargo por horas de máquina) no es HTTP: lo asienta el hook de registro de horas (Fase 3),
 * idempotente por registro (invariante crítico), en la misma transacción del registro.
 *
 * Tiempo real: refetch ante 'cartera_cupo_excedido' / 'cartera_colita' (avisos SSE al dueño, patrón
 * pack_pagar) y ante 'fiado_registrado' / 'fiado_abonado' (mueven el saldo → el semáforo). El excedido,
 * además, avisa con toast. Todo el backend es admin: esta sección solo se pinta dentro de CarteraAdmin.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import {
  Wallet, Plus, Search, Pencil, Truck, Building2, ChevronDown, ChevronRight,
  CalendarClock, TriangleAlert,
} from 'lucide-react'
import { api } from '@/lib/api'
import { cop, num, useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Semaforo, Campo, EstadoVacio, Esqueleto, Kpi, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS } from './comunes.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])
const n = (v) => Number(v || 0)

// Umbral por defecto de colita (N días sin abono) — espeja cartera_config.dias_colita del backend.
export const UMBRAL_COLITA_DEFAULT = 15

// Eventos que mueven la cartera de alquiler: los avisos propios (SSE al dueño) + los de fiados (un cargo
// o un abono cambian el consumido, y con él el semáforo del cupo).
const EVENTOS = ['cartera_cupo_excedido', 'cartera_colita', 'fiado_registrado', 'fiado_abonado']

// ── Semáforos (funciones puras, testeables sin DOM) ──────────────────────────────────────────────
// Semáforo de UTILIZACIÓN del cupo. Se DERIVA de cupo y consumido (no confía en un flag del backend):
// si el tope se movió, el color sigue al dato. Sin tope definido → 'gris' (no hay contra qué medir).
export function tonoCupo(cupo, consumido) {
  const tope = n(cupo)
  if (tope <= 0) return 'gris'
  const disponible = tope - n(consumido)
  if (disponible < 0) return 'rojo'                 // excedido
  if (disponible <= tope * 0.2) return 'ambar'      // queda ≤20% del cupo
  return 'verde'
}

// Semáforo de COLITA (saldo estancado): escala con la antigüedad sin abono contra el umbral (config).
// Ámbar al cruzar el umbral; rojo al doblarlo (deuda muy añeja que ya debería estar cobrada).
export function tonoColita(diasSinAbono, umbral = UMBRAL_COLITA_DEFAULT) {
  return n(diasSinAbono) >= umbral * 2 ? 'rojo' : 'ambar'
}

const ESTADO_CUPO = { verde: 'Holgado', ambar: 'Al límite', rojo: 'Excedido', gris: 'Sin tope' }
const BARRA = { verde: 'bg-success', ambar: 'bg-warning', rojo: 'bg-destructive', gris: 'bg-muted-foreground' }

function hoyCO() {
  // YYYY-MM-DD de HOY en hora Colombia (regla #4: nunca date.today() crudo).
  return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}

// ── Gate: solo monta el árbol real (y sus fetch/subscripciones) si la empresa tiene la capacidad ──
// Separar el gate del cuerpo evita llamar hooks (useFetch/useRealtimeEvent) cuando la feature está
// apagada: sin la capacidad, esta sección es literalmente nada (ni una petición de más).
export default function CarteraAlquilerSection() {
  const features = useFeatures()
  if (!features.includes('cartera_alquiler')) return null
  return <CarteraAlquilerActiva />
}

function CarteraAlquilerActiva() {
  const cuposQ = useFetch('/cartera-alquiler/cupos')
  const colitasQ = useFetch('/cartera-alquiler/colitas')
  const configQ = useFetch('/cartera-alquiler/config')

  useRealtimeEvent(EVENTOS, (tipo) => {
    if (tipo === 'cartera_cupo_excedido') toast.warning('Un cliente superó su cupo de alquiler')
    cuposQ.refetch(); colitasQ.refetch()
  })

  const [q, setQ] = useState('')
  const [editando, setEditando] = useState(null)   // null | 'nuevo' | cupo

  const cupos = arr(cuposQ.data)
  const colitas = arr(colitasQ.data)
  const umbral = n(configQ.data?.dias_colita) || UMBRAL_COLITA_DEFAULT

  // Índice de colitas por cliente (para el chip en la fila de cupo): nos quedamos con la MÁS añeja.
  const colitaPorCliente = useMemo(() => {
    const m = new Map()
    for (const c of colitas) {
      const prev = m.get(c.cliente_id)
      if (!prev || n(c.dias_sin_abono) > n(prev.dias_sin_abono)) m.set(c.cliente_id, c)
    }
    return m
  }, [colitas])

  // KPIs sobre el set completo (panorama de la cartera, no el filtrado).
  const cupoTotal = cupos.reduce((s, c) => s + n(c.cupo), 0)
  const consumidoTotal = cupos.reduce((s, c) => s + n(c.consumido), 0)
  const nExcedidos = cupos.filter((c) => n(c.cupo) - n(c.consumido) < 0).length

  const termino = q.trim().toLowerCase()
  const visibles = termino
    ? cupos.filter((c) => String(c.cliente_nombre || '').toLowerCase().includes(termino))
    : cupos

  return (
    <section className="space-y-3" aria-label="Cartera de alquiler">
      <div className="flex items-center gap-2 pt-1">
        <h2 className="text-base font-semibold inline-flex items-center gap-2">
          <Wallet className="size-4.5 text-primary" aria-hidden="true" /> Cartera de alquiler
        </h2>
        <span className="text-[11px] text-muted-foreground">crédito por alquiler de maquinaria</span>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi variante="card" label="Cupo otorgado" valor={cuposQ.loading ? '…' : cop(cupoTotal)}
          sublinea={`${cupos.length} cliente${cupos.length === 1 ? '' : 's'} con cupo`} />
        <Kpi variante="card" label="Consumido" valor={cuposQ.loading ? '…' : cop(consumidoTotal)}
          sublinea="saldo de alquiler en el ledger" />
        <Kpi variante="card" label="Colitas" valor={colitasQ.loading ? '…' : colitas.length}
          sublinea={`obras estancadas · +${umbral} d sin abono`} />
        <Kpi variante="card" label="Cupos excedidos" valor={cuposQ.loading ? '…' : nExcedidos}
          tono={nExcedidos > 0 ? 'negativo' : 'neutro'} sublinea="pasaron su tope de crédito" />
      </div>

      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar cupo por cliente…" aria-label="Buscar cupo por cliente" className="pl-9" />
          </div>
          <button onClick={() => setEditando(editando === 'nuevo' ? null : 'nuevo')} className={`${BTN_PRIMARY} h-9 shrink-0`}>
            <Plus className="size-4" /> Nuevo cupo
          </button>
        </div>
      </Card>

      {editando && (
        <CupoForm
          cupo={editando === 'nuevo' ? null : editando}
          onClose={() => setEditando(null)}
          onGuardado={() => { setEditando(null); cuposQ.refetch() }}
        />
      )}

      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <Wallet className="size-4 text-muted-foreground" aria-hidden="true" />
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Cupos por cliente {cupos.length > 0 && <span className="tabular">· {visibles.length}</span>}
          </h3>
        </div>
        {cuposQ.loading ? (
          <Esqueleto filas={3} />
        ) : cupos.length === 0 ? (
          <EstadoVacio
            icono={Wallet}
            titulo="Sin cupos de alquiler todavía"
            descripcion="Un cupo es el tope de crédito que le das a un cliente para alquilar maquinaria. Las horas de máquina se le cargan contra ese tope; aquí ves cuánto lleva consumido y cuánto le queda."
          >
            <button onClick={() => setEditando('nuevo')} className={`${BTN_PRIMARY} h-9`}>
              <Plus className="size-4" /> Definir el primer cupo
            </button>
          </EstadoVacio>
        ) : visibles.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Ningún cliente coincide con la búsqueda.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {visibles.map((c) => (
              <CupoFila key={c.id} cupo={c} colita={colitaPorCliente.get(c.cliente_id)} umbral={umbral}
                onEditar={() => setEditando(c)} />
            ))}
          </ul>
        )}
      </Card>

      <SeccionColitas colitas={colitas} umbral={umbral} loading={colitasQ.loading} />
    </section>
  )
}

// ── Fila de cupo: cliente + colita, tres métricas (cupo/consumido/disponible) y semáforo de tope ──
function CupoFila({ cupo, colita, umbral, onEditar }) {
  const tope = n(cupo.cupo)
  const consumido = n(cupo.consumido)
  // `disponible` puede venir del backend; si no, se deriva (robusto ante respuestas parciales).
  const disponible = cupo.disponible != null ? n(cupo.disponible) : tope - consumido
  const tono = tonoCupo(tope, consumido)
  const excedido = disponible < 0
  const pct = tope > 0 ? Math.min(100, Math.round((consumido / tope) * 100)) : 0

  return (
    <li className={`px-4 py-3 ${excedido ? 'bg-destructive/5' : ''}`}>
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-[14px] font-medium text-foreground">
              {cupo.cliente_nombre || `Cliente #${cupo.cliente_id}`}
            </span>
            {colita && (
              <Semaforo tono={tonoColita(colita.dias_sin_abono, umbral)}>
                Colita · {num(colita.dias_sin_abono)} d
              </Semaforo>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
            <span>Cupo <b className="tabular font-semibold text-secondary-foreground">{cop(tope)}</b></span>
            <span>Consumido <b className="tabular font-semibold text-secondary-foreground">{cop(consumido)}</b></span>
            <span>Disponible <b className={`tabular font-semibold ${excedido ? 'text-destructive' : 'text-foreground'}`}>{cop(disponible)}</b></span>
            {cupo.vigente_hasta && <span>· vence {cupo.vigente_hasta}</span>}
          </div>
          {/* Medidor de utilización: refuerza el semáforo (decorativo, aria-hidden; el estado ya va en la píldora). */}
          <div className="mt-1.5 h-1 w-full max-w-xs overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
            <div className={`h-full rounded-full ${BARRA[tono]}`} style={{ width: `${pct}%` }} />
          </div>
        </div>
        <Semaforo tono={tono}>{ESTADO_CUPO[tono]}</Semaforo>
        <button onClick={onEditar} aria-label={`Editar cupo de ${cupo.cliente_nombre || `cliente ${cupo.cliente_id}`}`}
          className="grid size-8 shrink-0 place-items-center rounded-md border border-border bg-surface text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground">
          <Pencil className="size-4" />
        </button>
      </div>
    </li>
  )
}

// ── Colitas / cargos por obra (vista de liquidación): obras con saldo estancado, expandibles ──────
function SeccionColitas({ colitas, umbral, loading }) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border-subtle flex items-center gap-2">
        <Truck className="size-4 text-muted-foreground" aria-hidden="true" />
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Cargos por obra {colitas.length > 0 && <span className="tabular">· {colitas.length} estancada{colitas.length === 1 ? '' : 's'}</span>}
        </h3>
      </div>
      {loading ? (
        <Esqueleto filas={2} />
      ) : colitas.length === 0 ? (
        <EstadoVacio
          icono={CalendarClock}
          titulo="Ninguna obra con cartera estancada"
          descripcion="Cuando una obra se finaliza y su saldo de alquiler lleva días sin abono, aparece aquí para que la cobres. Cada obra abre el detalle de sus cargos por horas de máquina."
        />
      ) : (
        <ul className="divide-y divide-border-subtle">
          {colitas.map((c) => <ColitaFila key={`${c.obra_id}`} colita={c} umbral={umbral} />)}
        </ul>
      )}
    </Card>
  )
}

function ColitaFila({ colita, umbral }) {
  const [abierta, setAbierta] = useState(false)
  const panelId = `colita-obra-${colita.obra_id}`
  const tono = tonoColita(colita.dias_sin_abono, umbral)

  return (
    <li>
      <button type="button" onClick={() => setAbierta((v) => !v)} aria-expanded={abierta} aria-controls={panelId}
        className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-fast hover:bg-surface-2">
        <span className="grid size-9 shrink-0 place-items-center rounded-md bg-surface-2 text-muted-foreground">
          <Building2 className="size-[18px]" aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="truncate text-[14px] font-medium text-foreground">
              {colita.obra_nombre || `Obra #${colita.obra_id}`}
            </span>
            <Semaforo tono={tono}>Colita · {num(colita.dias_sin_abono)} d</Semaforo>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-muted-foreground">
            <span className="truncate">{colita.cliente_nombre || `Cliente #${colita.cliente_id}`}</span>
            {colita.ultimo_abono_en
              ? <span>· último abono {fechaCorta(colita.ultimo_abono_en)}</span>
              : <span>· sin abonos</span>}
          </div>
        </div>
        <span className="tabular text-[13px] font-semibold text-foreground">{cop(colita.saldo)}</span>
        {abierta ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          : <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
      </button>
      {abierta && <ObraCargos id={panelId} obraId={colita.obra_id} />}
    </li>
  )
}

// Detalle de cargos de una obra (perezoso al expandir): horas de máquina asentadas + abonos + saldo.
function ObraCargos({ id, obraId }) {
  const q = useFetch(`/cartera-alquiler/obras/${obraId}`)
  const d = q.data || {}
  const cargos = arr(d.cargos)
  const abonos = arr(d.abonos)

  return (
    <div id={id} className="border-t border-border-subtle bg-surface-2/40 px-4 py-3.5">
      {q.loading ? (
        <p className="py-4 text-center text-[12px] text-muted-foreground">Cargando cargos…</p>
      ) : cargos.length === 0 ? (
        <p className="py-4 text-center text-[12px] text-muted-foreground">Esta obra aún no tiene cargos de alquiler.</p>
      ) : (
        <div className="rounded-md border border-border-subtle bg-surface">
          <div className="grid grid-cols-[1fr_auto_auto] gap-x-3 border-b border-border-subtle px-3 py-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
            <span>Máquina / fecha</span><span className="text-right">Horas</span><span className="text-right">Cargo</span>
          </div>
          <ul className="divide-y divide-border-subtle">
            {cargos.map((c) => (
              <li key={c.id ?? c.registro_horas_id} className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 px-3 py-2">
                <div className="min-w-0">
                  <div className="truncate text-[12px] font-medium text-foreground">{c.maquina_nombre || `Máquina #${c.maquina_id}`}</div>
                  <div className="text-[11px] text-muted-foreground">{c.fecha || fechaCorta(c.creado_en)}</div>
                </div>
                <span className="tabular text-right text-[12px] text-secondary-foreground">{num(c.horas_facturables)} h</span>
                <span className="tabular text-right text-[12px] font-semibold text-foreground">{cop(c.monto)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 text-[12px]">
        <span className="text-muted-foreground">
          {abonos.length > 0 ? `${abonos.length} abono${abonos.length === 1 ? '' : 's'} registrado${abonos.length === 1 ? '' : 's'}` : 'Sin abonos'}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="text-muted-foreground">Saldo de la obra</span>
          <b className="tabular text-[13px] font-semibold text-foreground">{cop(d.saldo)}</b>
        </span>
      </div>
      <p className="mt-2 inline-flex items-start gap-1 text-[11px] text-muted-foreground">
        <TriangleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
        Los abonos se registran en Clientes/Fiados (mueven caja); aquí solo se consulta.
      </p>
    </div>
  )
}

function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })
}

// ── Alta / edición de cupo ────────────────────────────────────────────────────────────────────────
// Alta: elige cliente (POST desactiva el cupo activo previo de ese cliente — único parcial en el
// backend). Edición: cliente fijo, con toggle `activo` para retirarlo.
function CupoForm({ cupo, onClose, onGuardado }) {
  const edicion = !!cupo
  const clientesQ = useFetch(edicion ? null : '/clientes')
  const clientes = arr(clientesQ.data)

  const [f, setF] = useState({
    cliente_id: cupo?.cliente_id ? String(cupo.cliente_id) : '',
    cupo: cupo?.cupo != null ? String(cupo.cupo) : '',
    vigente_desde: cupo?.vigente_desde || hoyCO(),
    vigente_hasta: cupo?.vigente_hasta || '',
    notas: cupo?.notas || '',
    activo: cupo ? !!cupo.activo : true,
  })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function guardar() {
    if (!edicion && !f.cliente_id) { toast.error('Elige el cliente del cupo'); return }
    if (!(n(f.cupo) > 0)) { toast.error('Indica un cupo mayor que cero'); return }
    if (!f.vigente_desde) { toast.error('Indica desde cuándo rige el cupo'); return }
    if (f.vigente_hasta && f.vigente_hasta < f.vigente_desde) {
      toast.error('La vigencia no puede terminar antes de empezar'); return
    }

    const payload = edicion
      ? {
          cupo: Number(f.cupo),
          vigente_desde: f.vigente_desde,
          vigente_hasta: f.vigente_hasta || null,
          notas: f.notas.trim() || null,
          activo: !!f.activo,
        }
      : {
          cliente_id: Number(f.cliente_id),
          cupo: Number(f.cupo),
          vigente_desde: f.vigente_desde,
          vigente_hasta: f.vigente_hasta || null,
          notas: f.notas.trim() || null,
        }

    setEnviando(true)
    try {
      const res = edicion
        ? await api(`/cartera-alquiler/cupos/${cupo.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
        : await api('/cartera-alquiler/cupos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      if (res.status === 403) { toast.error('Necesitas permisos de administrador'); return }
      if (!res.ok) { toast.error(edicion ? 'No se pudo guardar el cupo' : 'No se pudo crear el cupo'); return }
      toast.success(edicion ? 'Cupo actualizado' : 'Cupo creado')
      onGuardado()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-4">
      <h3 className="mb-3 inline-flex items-center gap-1.5 text-sm font-semibold">
        <Wallet className="size-4" aria-hidden="true" /> {edicion ? 'Editar cupo' : 'Nuevo cupo de alquiler'}
      </h3>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {edicion ? (
          <Campo label="Cliente">
            <Input value={cupo.cliente_nombre || `Cliente #${cupo.cliente_id}`} disabled className="h-9" />
          </Campo>
        ) : (
          <Campo label="Cliente" requerido>
            <select value={f.cliente_id} onChange={set('cliente_id')} className={SELECT_CLS}>
              <option value="">{clientesQ.loading ? 'Cargando clientes…' : 'Elige un cliente…'}</option>
              {clientes.map((c) => <option key={c.id} value={c.id}>{c.nombre}</option>)}
            </select>
          </Campo>
        )}
        <Campo label="Cupo de crédito" requerido hint="Tope que puede alcanzar su saldo de alquiler.">
          <Input type="number" inputMode="numeric" value={f.cupo} onChange={set('cupo')} placeholder="0" className="h-9 tabular" />
        </Campo>
        <Campo label="Vigente desde" requerido>
          <Input type="date" value={f.vigente_desde} onChange={set('vigente_desde')} className="h-9" />
        </Campo>
        <Campo label="Vigente hasta" hint="Opcional: en blanco, sin vencimiento.">
          <Input type="date" value={f.vigente_hasta} onChange={set('vigente_hasta')} className="h-9" />
        </Campo>
        <Campo label="Notas" className="sm:col-span-2">
          <Input value={f.notas} onChange={set('notas')} placeholder="Condiciones del crédito de alquiler" className="h-9" />
        </Campo>
        {edicion && (
          <label className="inline-flex items-center gap-2 text-sm self-end pb-2 sm:col-span-2">
            <input type="checkbox" checked={!!f.activo} aria-label="Cupo activo"
              onChange={(e) => setF((p) => ({ ...p, activo: e.target.checked }))} />
            Cupo activo (desmarcar lo retira sin borrar su histórico)
          </label>
        )}
      </div>
      <div className="mt-4 flex items-center justify-end gap-2">
        <button onClick={onClose} className={`${BTN_OUTLINE} h-10`}>Cancelar</button>
        <button onClick={guardar} disabled={enviando} className={`${BTN_PRIMARY} h-10`}>
          {enviando ? 'Guardando…' : edicion ? 'Guardar cambios' : 'Crear cupo'}
        </button>
      </div>
    </Card>
  )
}
