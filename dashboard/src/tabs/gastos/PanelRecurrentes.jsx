/*
 * PanelRecurrentes — el checklist de lo que se paga todos los meses (0071).
 *
 * Responde la pregunta que el dueño se hace de verdad ("¿ya pagué el arriendo?") sin adivinar por
 * nombre ni por monto: el gasto que salda un recurrente guarda su `recurrente_id`, y el backend
 * devuelve el pago del mes junto a cada fila. Pagar desde aquí abre el modal de gasto ya prellenado.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { CalendarDays, Check, Pencil, Plus } from '@/lib/icons.jsx'
import { api } from '@/lib/api'
import { useFetch, cop, EstadoVacio, SkeletonFilas } from '@/components/shared.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import ModalGastoRapido from '@/components/ModalGastoRapido.jsx'
import { CATEGORIAS_GASTO, rangoISO } from '@/lib/gastos.js'

const SELECT = 'h-10 w-full rounded-md border border-input bg-surface px-2 text-sm text-foreground sm:h-9'

export default function PanelRecurrentes({ mes, refreshKey, onPagado }) {
  const { isAdmin } = useAuth()
  const { desde, hasta } = rangoISO(mes)
  const q = useFetch(
    `/gastos/recurrentes?desde=${encodeURIComponent(desde)}&hasta=${encodeURIComponent(hasta)}`,
    [refreshKey, desde, hasta],
  )
  const [pagando, setPagando] = useState(null)     // recurrente cuyo pago se está registrando
  const [editando, setEditando] = useState(null)   // recurrente en edición, o 'nuevo'

  const filas = Array.isArray(q.data) ? q.data : []
  const pendientes = filas.filter((r) => !r.pagado_en)
  const faltante = pendientes.reduce((a, r) => a + Number(r.monto_estimado || 0), 0)

  return (
    <Card className="p-3.5">
      <div className="flex items-center justify-between mb-2.5 gap-2">
        <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground inline-flex items-center gap-1.5">
          <CalendarDays className="size-3.5" /> Lo de todos los meses
        </h2>
        {isAdmin() && (
          <Button size="sm" variant="ghost" className="h-7 gap-1 text-caption"
            onClick={() => setEditando('nuevo')}>
            <Plus className="size-3.5" /> Agregar
          </Button>
        )}
      </div>

      {q.loading ? (
        <SkeletonFilas filas={3} />
      ) : filas.length === 0 ? (
        <EstadoVacio
          icon={CalendarDays}
          titulo="Sin gastos fijos anotados"
          detalle="Arriendo, luz, agua, internet, nómina. Anotarlos una vez te dice cada mes qué falta por pagar y cuánto tienes que vender para no perder."
          accion={isAdmin() && (
            <Button size="sm" onClick={() => setEditando('nuevo')}>Anotar el primero</Button>
          )}
        />
      ) : (
        <>
          <ul className="divide-y divide-border-subtle">
            {filas.map((r) => (
              <li key={r.id} className="py-2 flex items-center gap-2 text-body-sm">
                <span className={`size-4 shrink-0 grid place-items-center rounded-full ${
                  r.pagado_en ? 'bg-success/15 text-success' : 'border border-border-subtle'}`}>
                  {r.pagado_en && <Check className="size-3" />}
                </span>
                <span className="min-w-0 flex-1 truncate">
                  {r.nombre}
                  {r.dia_mes && !r.pagado_en && (
                    <span className="text-caption text-muted-foreground"> · día {r.dia_mes}</span>
                  )}
                </span>
                {r.pagado_en ? (
                  <span className="tabular text-meta text-muted-foreground shrink-0">
                    {cop(Number(r.monto_pagado))}
                  </span>
                ) : (
                  <Button size="sm" variant="outline" className="h-7 text-caption shrink-0"
                    onClick={() => setPagando(r)}>
                    Pagar
                  </Button>
                )}
                {isAdmin() && (
                  <button type="button" onClick={() => setEditando(r)}
                    aria-label={`Editar ${r.nombre}`}
                    className="shrink-0 text-muted-foreground hover:text-foreground">
                    <Pencil className="size-3.5" />
                  </button>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-2.5 text-caption text-muted-foreground">
            {pendientes.length === 0
              ? 'Todo pago este mes.'
              : `Faltan ${pendientes.length} por pagar${faltante > 0 ? ` · ${cop(faltante)} aprox.` : ''}`}
          </p>
        </>
      )}

      <ModalGastoRapido
        abierto={pagando !== null} recurrente={pagando}
        onCerrar={() => setPagando(null)}
        onRegistrado={() => { q.refetch(); onPagado?.() }}
      />
      {editando && (
        <ModalRecurrente
          recurrente={editando === 'nuevo' ? null : editando}
          onCerrar={() => setEditando(null)}
          onGuardado={() => { setEditando(null); q.refetch() }}
        />
      )}
    </Card>
  )
}

function ModalRecurrente({ recurrente, onCerrar, onGuardado }) {
  const [nombre, setNombre] = useState(recurrente?.nombre ?? '')
  const [categoria, setCategoria] = useState(recurrente?.categoria ?? 'servicios')
  const [monto, setMonto] = useState(
    recurrente?.monto_estimado ? String(Math.round(recurrente.monto_estimado)) : ''
  )
  const [dia, setDia] = useState(recurrente?.dia_mes ? String(recurrente.dia_mes) : '')
  const [enviando, setEnviando] = useState(false)

  async function guardar(e) {
    e.preventDefault()
    if (!nombre.trim() || enviando) return
    setEnviando(true)
    try {
      const res = await api(
        recurrente ? `/gastos/recurrentes/${recurrente.id}` : '/gastos/recurrentes',
        {
          method: recurrente ? 'PUT' : 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            nombre: nombre.trim(), categoria,
            monto_estimado: monto ? Number(monto) : null,
            dia_mes: dia ? Number(dia) : null,
            activo: recurrente ? recurrente.activo : true,
          }),
        },
      )
      if (res.ok) { toast.success(recurrente ? 'Actualizado' : 'Agregado'); onGuardado() }
      else toast.error('No se pudo guardar')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  async function darDeBaja() {
    setEnviando(true)
    try {
      const res = await api(`/gastos/recurrentes/${recurrente.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          nombre: recurrente.nombre, categoria: recurrente.categoria,
          monto_estimado: recurrente.monto_estimado, dia_mes: recurrente.dia_mes, activo: false,
        }),
      })
      if (res.ok) { toast.success('Ya no aparece en el checklist'); onGuardado() }
      else toast.error('No se pudo quitar')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o && !enviando) onCerrar() }}>
      <DialogContent aria-describedby="rec-desc">
        <DialogHeader>
          <DialogTitle>{recurrente ? 'Editar gasto fijo' : 'Nuevo gasto fijo'}</DialogTitle>
          <DialogDescription id="rec-desc">
            Lo que se paga todos los meses. El monto es una referencia: al pagar puedes cambiarlo.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={guardar} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="rec-nombre">Nombre</Label>
            <Input id="rec-nombre" autoFocus value={nombre} onChange={(e) => setNombre(e.target.value)}
              placeholder="Arriendo del local, luz, internet…" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="rec-cat">Categoría</Label>
            <select id="rec-cat" value={categoria} onChange={(e) => setCategoria(e.target.value)} className={SELECT}>
              {CATEGORIAS_GASTO.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="rec-monto">Suele costar</Label>
              <Input id="rec-monto" type="number" inputMode="numeric" min="0" value={monto}
                onChange={(e) => setMonto(e.target.value)} placeholder="0" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="rec-dia">Día del mes</Label>
              <Input id="rec-dia" type="number" inputMode="numeric" min="1" max="31" value={dia}
                onChange={(e) => setDia(e.target.value)} placeholder="5" />
            </div>
          </div>
          <div className="flex gap-2">
            <Button type="submit" disabled={!nombre.trim() || enviando} className="flex-1">
              {enviando ? 'Guardando…' : 'Guardar'}
            </Button>
            {recurrente && (
              <Button type="button" variant="outline" disabled={enviando} onClick={darDeBaja}>
                Quitar
              </Button>
            )}
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
