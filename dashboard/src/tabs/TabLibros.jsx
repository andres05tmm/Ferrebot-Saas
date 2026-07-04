/*
 * TabLibros — libros contables auxiliar y mayor (ADR 0027). Gateada por 'libros_contables', SOLO admin.
 * GET /reportes/libro-mayor (total por cuenta/concepto) y /reportes/libro-auxiliar (detalle documento a
 * documento, filtrable por concepto), ambos por rango (default mes en curso). Es soporte contable de
 * SOLO LECTURA (no hay PUC formal aún; la naturaleza agrupa el concepto). Live: refetch ante 'reconnected'.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Library, BookOpen, ListTree } from 'lucide-react'
import { cop, mesActualCO } from '@/components/shared.jsx'
import { useLibroMayor, useLibroAuxiliar, keyPrefix } from '@/lib/queries'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

const NATURALEZA_TONO = {
  ingreso: 'text-success', egreso: 'text-destructive',
  impuesto: 'text-warning', retencion: 'text-info',
}

function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', { day: '2-digit', month: 'short', timeZone: 'America/Bogota' })
}

export default function TabLibros() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        Los libros contables son solo para administradores.
      </Card>
    )
  }
  return <LibrosContenido />
}

function LibrosContenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const [vista, setVista] = useState('mayor')   // 'mayor' | 'auxiliar'
  const [rango, setRango] = useState(mesActualCO())
  const setCampo = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  const qc = useQueryClient()
  const mayorQ = useLibroMayor(rango.desde, rango.hasta, vista === 'mayor', refreshKey)
  const auxQ = useLibroAuxiliar(rango.desde, rango.hasta, vista === 'auxiliar', refreshKey)
  const q = vista === 'mayor' ? mayorQ : auxQ
  useRealtimeEvent(['reconnected'], () => qc.invalidateQueries({ queryKey: keyPrefix.libros }))

  const filas = arr(q.data)

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <h1 className="text-lg font-semibold tracking-tight mr-auto inline-flex items-center gap-2">
            <Library className="size-5 text-muted-foreground" /> Libros contables
          </h1>
          <label className="text-[11px] text-muted-foreground">
            Desde
            <Input type="date" value={rango.desde} onChange={setCampo('desde')} aria-label="Desde" className="h-9 mt-1" />
          </label>
          <label className="text-[11px] text-muted-foreground">
            Hasta
            <Input type="date" value={rango.hasta} onChange={setCampo('hasta')} aria-label="Hasta" className="h-9 mt-1" />
          </label>
        </div>
        <div className="flex gap-1.5 mt-3">
          <button onClick={() => setVista('mayor')}
            className={`text-[12px] px-3 h-8 rounded-md border inline-flex items-center gap-1.5 ${
              vista === 'mayor' ? 'bg-primary text-primary-foreground border-primary' : 'bg-surface border-border hover:bg-surface-2'
            }`}>
            <BookOpen className="size-3.5" /> Mayor
          </button>
          <button onClick={() => setVista('auxiliar')}
            className={`text-[12px] px-3 h-8 rounded-md border inline-flex items-center gap-1.5 ${
              vista === 'auxiliar' ? 'bg-primary text-primary-foreground border-primary' : 'bg-surface border-border hover:bg-surface-2'
            }`}>
            <ListTree className="size-3.5" /> Auxiliar
          </button>
        </div>
      </Card>

      <Card className="p-0 overflow-hidden">
        {q.isLoading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : q.isError ? (
          <p className="py-10 text-center text-sm text-destructive">No se pudo cargar el libro.</p>
        ) : filas.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin movimientos en el periodo.</p>
        ) : vista === 'mayor' ? (
          <ul className="divide-y divide-border-subtle">
            {filas.map((f, i) => (
              <li key={i} className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate capitalize">{f.concepto}</div>
                  <div className={`text-[11px] capitalize ${NATURALEZA_TONO[f.naturaleza] || 'text-muted-foreground'}`}>{f.naturaleza}</div>
                </div>
                <span className="tabular-nums font-semibold shrink-0">{cop(f.total)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {filas.map((f, i) => (
              <li key={i} className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
                <span className="text-[11px] text-muted-foreground tabular-nums shrink-0 w-16">{fechaCorta(f.fecha)}</span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate capitalize">{f.concepto}</div>
                  <div className="text-[11px] text-muted-foreground truncate">{f.referencia}</div>
                </div>
                <span className={`tabular-nums font-semibold shrink-0 ${NATURALEZA_TONO[f.naturaleza] || ''}`}>{cop(f.valor)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <p className="text-[11px] text-muted-foreground px-1">
        Soporte contable de solo lectura, derivado de los documentos del periodo. Mientras no exista un
        PUC formal, la naturaleza (ingreso/egreso/impuesto/retención) agrupa cada concepto.
      </p>
    </div>
  )
}
