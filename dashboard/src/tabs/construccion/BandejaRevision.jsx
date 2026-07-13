/*
 * BandejaRevision — cola de recibos que el bot importó con baja confianza (vertical construcción).
 *
 * Los trabajadores de campo mandan foto de recibo por Telegram; si la extracción queda con confianza
 * < 0.7 el gasto se guarda con `requiere_revision=true`. Esta sección vive ARRIBA de la pestaña Gastos
 * y le da al dueño una cola accionable: ver el recibo, corregir la imputación, aprobar o RECHAZAR.
 *
 *   GET   /gastos/revision            → lista de GastoLeer con requiere_revision=true.
 *   POST  /gastos/{id}/aprobar        → baja el flag (idempotente); la fila sale de la cola.
 *   POST  /gastos/{id}/rechazar       → F2.2: anula el gasto devolviendo la plata a caja con un
 *                                       movimiento INVERSO (el egreso original nunca se borra).
 *   PATCH /gastos/{id}/imputacion     → F2.2: re-imputa obra/máquina/categoría/concepto ANTES de
 *                                       aprobar (nunca el monto: cambiar plata = rechazar y recrear).
 *
 * Solo admin (cifras financieras). Con la cola vacía ya NO desaparece en silencio: muestra un "al día ✓"
 * de una línea — el dueño sabe que la bandeja existe y está despachada (hallazgo de la auditoría F1).
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { ReceiptText, ExternalLink, Check, HardHat, Truck, Bot, Pencil, X } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Textarea } from '@/components/ui/textarea.jsx'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog.jsx'
import { Semaforo, Campo, BTN_PRIMARY, BTN_OUTLINE, SELECT_CLS, CATEGORIAS_GASTO_VERTICAL } from './comunes.jsx'

const n = (v) => { const x = Number(v); return Number.isFinite(x) ? x : 0 }
const arr = (x) => (Array.isArray(x) ? x : [])

// Fecha corta en hora Colombia (regla #4): dd MMM, sin ambigüedad de zona.
function fechaCorta(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('es-CO', {
      timeZone: 'America/Bogota', day: '2-digit', month: 'short',
    })
  } catch { return '' }
}

// Lee el `detail` de un error del backend sin romper si el body no es JSON.
async function detalleError(res) {
  try { const b = await res.json(); return typeof b?.detail === 'string' ? b.detail : null } catch { return null }
}

export default function BandejaRevision({ refreshKey }) {
  const { isAdmin } = useAuth()
  const admin = isAdmin()

  // Sin admin no se pide nada (path falsy → useFetch queda en reposo, sin 403).
  const revisionQ = useFetch(admin ? '/gastos/revision' : null, [refreshKey])
  useRealtimeEvent(['gasto_registrado', 'gasto_rechazado', 'reconnected'], revisionQ.refetch)

  // Despachados de forma optimista (aprobado o rechazado): fuera de la lista al instante; revert si falla.
  const [despachados, setDespachados] = useState(() => new Set())
  const [enviando, setEnviando] = useState(() => new Set())
  const [rechazando, setRechazando] = useState(null)   // gasto en el diálogo de rechazo, o null
  const [editando, setEditando] = useState(null)       // id del gasto con el editor de imputación abierto

  const lista = arr(revisionQ.data).filter((g) => !despachados.has(g.id))

  // Silencioso mientras carga o si falla; sin admin no se monta.
  if (!admin || revisionQ.loading || revisionQ.error) return null

  // Cola vacía: "al día" visible (la bandeja existe y está despachada), nunca desaparición muda.
  if (lista.length === 0) {
    return (
      <Card className="flex items-center gap-2.5 p-3 text-body-sm text-success" aria-label="Bandeja de revisión al día">
        <ReceiptText className="size-4 shrink-0" aria-hidden="true" />
        <span className="font-medium">Bandeja del bot al día — sin recibos por revisar.</span>
      </Card>
    )
  }

  function marcar(id) { setDespachados((s) => new Set(s).add(id)) }
  function desmarcar(id) { setDespachados((s) => { const c = new Set(s); c.delete(id); return c }) }

  async function despachar(id, hacer, msgOk, msgFallo) {
    setEnviando((s) => new Set(s).add(id))
    marcar(id)
    try {
      const res = await hacer()
      if (res.ok) toast.success(msgOk)
      else { desmarcar(id); toast.error((await detalleError(res)) || msgFallo) }
    } catch { desmarcar(id); toast.error('Error de conexión') } finally {
      setEnviando((s) => { const c = new Set(s); c.delete(id); return c })
    }
  }

  const aprobar = (id) => despachar(
    id,
    () => api(`/gastos/${id}/aprobar`, { method: 'POST' }),
    'Recibo aprobado', 'No se pudo aprobar el recibo',
  )

  const rechazar = (id, motivo) => despachar(
    id,
    () => api(`/gastos/${id}/rechazar`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ motivo: motivo?.trim() || null }),
    }),
    'Recibo rechazado — la plata volvió a caja con un movimiento inverso',
    'No se pudo rechazar el recibo',
  )

  return (
    <Card className="p-3.5 border-l-[3px] border-l-warning" aria-label="Recibos del bot por revisar">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <ReceiptText className="size-4 text-muted-foreground" aria-hidden="true" />
        <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">
          Por revisar · {lista.length} {lista.length === 1 ? 'recibo del bot' : 'recibos del bot'}
        </h2>
      </div>

      <ul className="divide-y divide-border-subtle">
        {lista.map((g) => (
          <FilaRecibo
            key={g.id} gasto={g} ocupado={enviando.has(g.id)} editando={editando === g.id}
            onAprobar={() => aprobar(g.id)}
            onRechazar={() => setRechazando(g)}
            onEditar={() => setEditando(editando === g.id ? null : g.id)}
            onEditado={() => { setEditando(null); revisionQ.refetch() }}
          />
        ))}
      </ul>

      {/* Confirmación destructiva con motivo opcional (alert-dialog, F2.0). */}
      <AlertDialog open={rechazando != null} onOpenChange={(o) => { if (!o) setRechazando(null) }}>
        {rechazando != null && (
          <DialogoRechazo
            gasto={rechazando}
            onConfirmar={(motivo) => { rechazar(rechazando.id, motivo); setRechazando(null) }}
          />
        )}
      </AlertDialog>
    </Card>
  )
}

function DialogoRechazo({ gasto, onConfirmar }) {
  const [motivo, setMotivo] = useState('')
  return (
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>¿Rechazar el recibo de {cop(n(gasto.monto))}?</AlertDialogTitle>
        <AlertDialogDescription>
          El gasto se anula y la plata vuelve a caja con un movimiento inverso (el egreso original
          queda en el historial). Esta acción no se puede deshacer: si el recibo era real, habrá que
          registrarlo de nuevo con los datos correctos.
        </AlertDialogDescription>
      </AlertDialogHeader>
      <Campo label="Motivo" hint="Opcional. Queda en el registro del gasto rechazado.">
        <Textarea value={motivo} onChange={(e) => setMotivo(e.target.value)}
          placeholder="Ej. monto ilegible, recibo repetido…" />
      </Campo>
      <AlertDialogFooter>
        <AlertDialogCancel>Cancelar</AlertDialogCancel>
        <AlertDialogAction variant="destructive" onClick={() => onConfirmar(motivo)}>
          Rechazar y devolver a caja
        </AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  )
}

function FilaRecibo({ gasto: g, ocupado, editando, onAprobar, onRechazar, onEditar, onEditado }) {
  // Códigos del vertical (REPUESTOS, MANTENIMIENTO_MAQUINA…) → legibles; sin código → fallback.
  const cat = g.categoria_gasto ? g.categoria_gasto.toLowerCase().replace(/_/g, ' ') : 'Sin clasificar'
  const esBot = g.origen_registro === 'TELEGRAM_BOT'
  return (
    <li className="py-2.5">
      <div className="flex items-center gap-3">
        <Recibo url={g.comprobante_url} />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="tabular text-[15px] font-semibold text-foreground">{cop(n(g.monto))}</span>
            <span className="text-caption text-muted-foreground">{fechaCorta(g.creado_en)}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <span className="text-[12px] capitalize text-secondary-foreground">{cat}</span>
            {g.obra_id != null && (
              <span className="inline-flex items-center gap-1 text-caption text-muted-foreground">
                <HardHat className="size-3" aria-hidden="true" /> Obra #{g.obra_id}
              </span>
            )}
            {g.maquina_id != null && (
              <span className="inline-flex items-center gap-1 text-caption text-muted-foreground">
                <Truck className="size-3" aria-hidden="true" /> Máquina #{g.maquina_id}
              </span>
            )}
            {esBot && (
              <Semaforo tono="azul" className="gap-1">
                <Bot className="size-3" aria-hidden="true" /> Bot
              </Semaforo>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <button type="button" onClick={onEditar} aria-expanded={editando} title="Corregir imputación"
            className={`${BTN_OUTLINE} h-9 px-2.5`}>
            {editando ? <X className="size-4" aria-hidden="true" /> : <Pencil className="size-4" aria-hidden="true" />}
          </button>
          <button type="button" onClick={onRechazar} disabled={ocupado}
            className="inline-flex h-9 min-h-10 items-center rounded-md px-2.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive sm:min-h-0">
            Rechazar
          </button>
          <button type="button" onClick={onAprobar} disabled={ocupado} className={`${BTN_PRIMARY} h-9`}>
            <Check className="size-4" aria-hidden="true" />
            {ocupado ? 'Enviando…' : 'Aprobar'}
          </button>
        </div>
      </div>

      {editando && <EditorImputacion gasto={g} onEditado={onEditado} />}
    </li>
  )
}

// Corrección de la imputación ANTES de aprobar: obra, máquina, categoría vertical y concepto.
// El monto no se toca (su egreso ya está posteado): plata mala = rechazar y registrar de nuevo.
function EditorImputacion({ gasto, onEditado }) {
  const obrasQ = useFetch('/obras')
  const maquinasQ = useFetch('/maquinas')
  const [f, setF] = useState({
    obra_id: gasto.obra_id != null ? String(gasto.obra_id) : '',
    maquina_id: gasto.maquina_id != null ? String(gasto.maquina_id) : '',
    categoria_gasto: gasto.categoria_gasto || '',
    concepto: gasto.concepto || '',
  })
  const [guardando, setGuardando] = useState(false)
  const set = (k) => (e) => setF((p) => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    const payload = {}
    if (f.obra_id) payload.obra_id = Number(f.obra_id)
    if (f.maquina_id) payload.maquina_id = Number(f.maquina_id)
    if (f.categoria_gasto) payload.categoria_gasto = f.categoria_gasto
    if (f.concepto.trim()) payload.concepto = f.concepto.trim()
    setGuardando(true)
    try {
      const res = await api(`/gastos/${gasto.id}/imputacion`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (res.ok) { toast.success('Imputación corregida'); onEditado() }
      else toast.error((await detalleError(res)) || 'No se pudo corregir la imputación')
    } catch { toast.error('Error de conexión') } finally { setGuardando(false) }
  }

  return (
    <div className="mt-2 rounded-md border border-border bg-surface-2/60 p-3">
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        <Campo label="Obra">
          <select value={f.obra_id} onChange={set('obra_id')} className={SELECT_CLS}>
            <option value="">Sin obra</option>
            {arr(obrasQ.data).map((o) => <option key={o.id} value={o.id}>{o.nombre}</option>)}
          </select>
        </Campo>
        <Campo label="Máquina">
          <select value={f.maquina_id} onChange={set('maquina_id')} className={SELECT_CLS}>
            <option value="">Sin máquina</option>
            {arr(maquinasQ.data).map((m) => (
              <option key={m.id} value={m.id}>{m.codigo ? `${m.codigo} · ${m.nombre}` : m.nombre}</option>
            ))}
          </select>
        </Campo>
        <Campo label="Categoría">
          <select value={f.categoria_gasto} onChange={set('categoria_gasto')} className={SELECT_CLS}>
            <option value="">Sin clasificar</option>
            {CATEGORIAS_GASTO_VERTICAL.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Campo>
        <Campo label="Concepto">
          <input value={f.concepto} onChange={set('concepto')} placeholder="Qué se compró" className={SELECT_CLS} />
        </Campo>
      </div>
      <div className="mt-2.5 flex justify-end">
        <button type="button" onClick={guardar} disabled={guardando} className={`${BTN_PRIMARY} h-8`}>
          {guardando ? 'Guardando…' : 'Guardar imputación'}
        </button>
      </div>
    </div>
  )
}

/** Miniatura del recibo → abre el comprobante en pestaña nueva. Sin URL: placeholder con icono. */
function Recibo({ url }) {
  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="group relative size-11 shrink-0 overflow-hidden rounded-md border border-border bg-surface-2"
        aria-label="Ver recibo"
      >
        <img src={url} alt="Recibo" className="size-full object-cover" />
        <span className="absolute inset-0 grid place-items-center bg-black/0 text-transparent transition-colors group-hover:bg-black/45 group-hover:text-white">
          <ExternalLink className="size-4" aria-hidden="true" />
        </span>
      </a>
    )
  }
  return (
    <span className="grid size-11 shrink-0 place-items-center rounded-md border border-border bg-surface-2 text-muted-foreground" aria-hidden="true">
      <ReceiptText className="size-4" />
    </span>
  )
}
