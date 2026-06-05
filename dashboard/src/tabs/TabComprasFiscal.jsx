/*
 * TabComprasFiscal — compras fiscales con desglose de IVA (Fase 12, Slice 6a). SOLO admin.
 * Gateado por la feature `compras_fiscal` (la ruta solo aparece en la navegación con la capacidad).
 * Registrar: nit + base + iva + total (+ soporte) → POST /compras-fiscal. Marcar fiscal: convierte una
 * compra normal en fiscal vía POST /compras/{id}/to-fiscal (idempotente). Lista del rango (default mes).
 * Solo DATOS: NO toca RADIAN/DIAN ni MATIAS. Datos por api.js. Live: re-fetch ante 'reconnected'.
 */
import { useMemo, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { FileCog, Tag } from 'lucide-react'
import { api } from '@/lib/api.js'
import { useFetch, cop, mesActualCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const FECHA_CO = { day: '2-digit', month: 'short', timeZone: 'America/Bogota' }

export default function TabComprasFiscal() {
  const { isAdmin } = useAuth()
  // Registrar/listar compras fiscales es admin-only en el backend: el tab se gatea igual para el vendedor.
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        Las compras fiscales son solo para administradores.
      </Card>
    )
  }
  return <ComprasFiscalContenido />
}

function ComprasFiscalContenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const [rango, setRango] = useState(mesActualCO())
  const setCampoRango = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  const deps = [refreshKey, rango.desde, rango.hasta]
  const fiscalesQ = useFetch(`/compras-fiscal?desde=${rango.desde}&hasta=${rango.hasta}`, deps)
  const comprasQ = useFetch(`/compras?desde=${rango.desde}&hasta=${rango.hasta}`, deps)
  function recargar() { fiscalesQ.refetch(); comprasQ.refetch() }
  useRealtimeEvent(['reconnected', 'compra_registrada'], recargar)

  const fiscales = Array.isArray(fiscalesQ.data) ? fiscalesQ.data : []
  const compras = Array.isArray(comprasQ.data) ? comprasQ.data : []
  // Compras que ya tienen una fiscal ligada → no se pueden volver a marcar (la idempotencia lo respeta).
  const yaFiscal = useMemo(
    () => new Set(fiscales.map(f => f.compra_id).filter(id => id != null)),
    [fiscales],
  )

  async function marcarFiscal(compra) {
    try {
      const res = await api(`/compras/${compra.id}/to-fiscal`, { method: 'POST' })
      if (res.ok) { toast.success('Compra marcada como fiscal'); recargar() }
      else toast.error('No se pudo marcar la compra como fiscal')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <div className="space-y-3">
        <RegistrarCompraFiscal onRegistrada={recargar} />
        <ComprasNormales compras={compras} loading={comprasQ.loading} yaFiscal={yaFiscal} onMarcar={marcarFiscal} />
      </div>

      <Card className="p-0 overflow-hidden">
        <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
          <FileCog className="size-4 text-muted-foreground" />
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mr-auto">Compras fiscales</h2>
          <Input type="date" value={rango.desde} onChange={setCampoRango('desde')} aria-label="Desde" className="h-7 w-[8.5rem] text-[11px]" />
          <Input type="date" value={rango.hasta} onChange={setCampoRango('hasta')} aria-label="Hasta" className="h-7 w-[8.5rem] text-[11px]" />
        </div>
        {fiscalesQ.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : fiscales.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Sin compras fiscales en el periodo.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {fiscales.map(f => (
              <li key={f.id} className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{f.proveedor_nit ? `NIT ${f.proveedor_nit}` : 'Sin NIT'}</div>
                  <div className="text-[11px] text-muted-foreground tabular">
                    base {cop(Number(f.base))} · IVA {cop(Number(f.iva))}
                    {f.creado_en ? ` · ${new Date(f.creado_en).toLocaleDateString('es-CO', FECHA_CO)}` : ''}
                  </div>
                </div>
                <span className="tabular font-semibold shrink-0">{cop(Number(f.total))}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}

function RegistrarCompraFiscal({ onRegistrada }) {
  const [f, setF] = useState({ proveedor_nit: '', base: '', iva: '', total: '', soporte_url: '' })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))
  // Sugerencia de total = base + iva (informativa; el backend exige coherencia ±1 centavo).
  const sugerido = (Number(f.base) || 0) + (Number(f.iva) || 0)

  async function registrar() {
    if (!f.proveedor_nit.trim()) { toast.error('Indica el NIT del proveedor'); return }
    if (!(Number(f.total) >= 0) || !(Number(f.base) >= 0) || !(Number(f.iva) >= 0)) {
      toast.error('Base, IVA y total deben ser válidos'); return
    }
    const payload = {
      proveedor_nit: f.proveedor_nit.trim(),
      base: Number(f.base), iva: Number(f.iva), total: Number(f.total),
      soporte_url: f.soporte_url.trim() || null,
    }
    setEnviando(true)
    try {
      const res = await api('/compras-fiscal', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      if (res.status === 422) { toast.error('Montos incoherentes: base + IVA debe igualar el total'); return }
      if (!res.ok) { toast.error('No se pudo registrar la compra fiscal'); return }
      toast.success('Compra fiscal registrada')
      setF({ proveedor_nit: '', base: '', iva: '', total: '', soporte_url: '' })
      onRegistrada()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
        <FileCog className="size-4" /> Nueva compra fiscal
      </h2>
      <div className="space-y-2">
        <Input value={f.proveedor_nit} onChange={set('proveedor_nit')} placeholder="NIT del proveedor *" aria-label="NIT del proveedor" className="h-9" />
        <div className="flex gap-2">
          <Input type="number" value={f.base} onChange={set('base')} placeholder="Base" aria-label="Base" className="h-9 flex-1" />
          <Input type="number" value={f.iva} onChange={set('iva')} placeholder="IVA" aria-label="IVA" className="h-9 flex-1" />
        </div>
        <div className="flex items-center gap-2">
          <Input type="number" value={f.total} onChange={set('total')} placeholder="Total *" aria-label="Total" className="h-9 flex-1" />
          {sugerido > 0 && (
            <button type="button" onClick={() => setF(prev => ({ ...prev, total: String(sugerido) }))}
              className="text-[11px] px-2 h-9 rounded-md border border-border bg-surface hover:bg-surface-2 shrink-0 text-muted-foreground">
              = {cop(sugerido)}
            </button>
          )}
        </div>
        <Input value={f.soporte_url} onChange={set('soporte_url')} placeholder="URL de soporte (opcional)" aria-label="URL de soporte" className="h-9" />
        <button onClick={registrar} disabled={enviando}
          className="w-full h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Registrando…' : 'Registrar compra fiscal'}
        </button>
      </div>
    </Card>
  )
}

function ComprasNormales({ compras, loading, yaFiscal, onMarcar }) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
        <Tag className="size-4 text-muted-foreground" />
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Compras del periodo</h2>
      </div>
      {loading ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : compras.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Sin compras en el periodo.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {compras.map(c => {
            const fiscalizada = yaFiscal.has(c.id)
            return (
              <li key={c.id} className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{c.proveedor_nombre || 'Proveedor'}</div>
                  <div className="text-[11px] text-muted-foreground tabular">{cop(Number(c.total))}</div>
                </div>
                {fiscalizada ? (
                  <Badge variant="outline" className="h-5 text-[10px] shrink-0 bg-success/10 text-success border-success/20">fiscal</Badge>
                ) : (
                  <button onClick={() => onMarcar(c)}
                    className="text-[11px] px-2.5 h-7 rounded-md border border-border bg-surface hover:bg-surface-2 shrink-0">
                    marcar fiscal
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
