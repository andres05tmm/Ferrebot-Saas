/*
 * TabRetenciones — catálogo tributario editable de retenciones/INC (ADR 0027). Gateada por
 * 'retenciones', SOLO admin. El catálogo es OPT-IN: vacío = nada se retiene. Cada regla es (tipo,
 * concepto) con su base mínima en UVT y su tarifa; el motor la aplica por documento SIN tocar el total.
 * GET/PUT /retenciones/config. Las tarifas las define la empresa (no hay tarifas hardcodeadas).
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { Percent, Plus } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])
const TIPOS = ['retefuente', 'ica', 'reteiva', 'inc', 'uvt']

const TIPO_TONO = {
  retefuente: 'bg-info/10 text-info border-info/20',
  ica: 'bg-warning/10 text-warning border-warning/20',
  reteiva: 'bg-primary/10 text-primary border-primary/20',
  inc: 'bg-success/10 text-success border-success/20',
  uvt: 'bg-muted text-muted-foreground border-border',
}

function FormRegla({ onGuardada }) {
  const vacio = { tipo: 'retefuente', concepto: '', base_minima_uvt: '0', tarifa: '0', activo: true }
  const [f, setF] = useState(vacio)
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    if (!f.concepto.trim()) { toast.error('Indica el concepto'); return }
    const body = {
      tipo: f.tipo, concepto: f.concepto.trim(),
      base_minima_uvt: Number(f.base_minima_uvt) || 0,
      tarifa: Number(f.tarifa) || 0, activo: !!f.activo,
    }
    setEnviando(true)
    try {
      const res = await api('/retenciones/config', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      if (res.ok) { toast.success('Regla guardada'); setF(vacio); onGuardada() }
      else if (res.status === 422) toast.error('Tipo inválido')
      else if (res.status === 403) toast.error('Necesitas permisos de administrador')
      else toast.error('No se pudo guardar')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h3 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
        <Plus className="size-4" /> Nueva regla / editar
      </h3>
      <div className="space-y-2.5">
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Tipo</span>
          <select value={f.tipo} onChange={set('tipo')} aria-label="Tipo"
            className="h-9 rounded-md border border-border bg-surface px-2 text-sm">
            {TIPOS.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Concepto</span>
          <Input value={f.concepto} onChange={set('concepto')} placeholder="p. ej. Compras generales"
            aria-label="Concepto" className="h-9" />
        </label>
        <div className="grid grid-cols-2 gap-2.5">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Base mínima (UVT)</span>
            <Input type="number" value={f.base_minima_uvt} onChange={set('base_minima_uvt')}
              aria-label="Base mínima (UVT)" className="h-9" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Tarifa (%)</span>
            <Input type="number" step="0.01" value={f.tarifa} onChange={set('tarifa')}
              aria-label="Tarifa (%)" className="h-9" />
          </label>
        </div>
        <label className="inline-flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.activo} aria-label="Activa"
            onChange={e => setF(p => ({ ...p, activo: e.target.checked }))} />
          Activa
        </label>
      </div>
      <div className="mt-3 flex justify-end">
        <Button disabled={enviando} onClick={guardar}>{enviando ? 'Guardando…' : 'Guardar regla'}</Button>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        Se guarda por (tipo, concepto): repetir el par edita la regla existente. Aplicar el motor a un
        documento NO cambia su total; solo calcula el neto a recibir.
      </p>
    </Card>
  )
}

export default function TabRetenciones() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        El catálogo de retenciones es solo para administradores.
      </Card>
    )
  }
  return <RetencionesContenido />
}

function RetencionesContenido() {
  const configQ = useFetch('/retenciones/config')
  const reglas = arr(configQ.data)

  return (
    <div className="space-y-3">
      <h1 className="text-base font-semibold inline-flex items-center gap-2">
        <Percent className="size-4.5 text-primary" /> Retenciones e INC
      </h1>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Card className="lg:col-span-2 p-0 overflow-hidden">
          <div className="px-3.5 py-2.5 border-b border-border-subtle">
            <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Catálogo tributario</h2>
          </div>
          {configQ.loading ? (
            <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
          ) : reglas.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              Catálogo vacío: nada se retiene todavía. Agrega una regla para empezar (opt-in).
            </p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {reglas.map(r => (
                <li key={r.id} className={`px-3.5 py-2.5 flex items-center gap-3 text-[13px] ${r.activo ? '' : 'opacity-60'}`}>
                  <Badge variant="outline" className={`h-5 text-[10px] shrink-0 ${TIPO_TONO[r.tipo] || ''}`}>{r.tipo}</Badge>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium truncate">{r.concepto}</div>
                    <div className="text-[11px] text-muted-foreground tabular-nums">
                      base ≥ {Number(r.base_minima_uvt)} UVT{!r.activo && ' · inactiva'}
                    </div>
                  </div>
                  <span className="tabular-nums font-semibold shrink-0">{Number(r.tarifa)}%</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <FormRegla onGuardada={configQ.refetch} />
      </div>
    </div>
  )
}
