/*
 * TabLibroIVA — Libro IVA del rango, SOLO admin (Fase 12, Slice 5).
 * GET /reportes/libro-iva?desde&hasta (default mes en curso). Cruza el IVA generado (ventas) con el
 * descontable (compras fiscales) y muestra el saldo (a pagar / a favor). Es soporte tributario de solo
 * lectura: NO emite ni consulta a la DIAN. Datos por api.js. Live: re-fetch ante 'reconnected'.
 */
import { useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { ArrowDownCircle, ArrowUpCircle, Calculator, Scale } from 'lucide-react'
import { useFetch, cop, mesActualCO } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

export default function TabLibroIVA() {
  const { isAdmin } = useAuth()
  // El Libro IVA es del negocio completo: oculto para el vendedor (no se pide el endpoint).
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        El Libro IVA es solo para administradores.
      </Card>
    )
  }
  return <LibroIVAContenido />
}

function LibroIVAContenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const [rango, setRango] = useState(mesActualCO())
  const setCampo = (k) => (e) => setRango(prev => ({ ...prev, [k]: e.target.value }))

  const path = `/reportes/libro-iva?desde=${rango.desde}&hasta=${rango.hasta}`
  const q = useFetch(path, [refreshKey, rango.desde, rango.hasta])
  useRealtimeEvent(['reconnected'], q.refetch)

  const d = q.data || {}
  const ivaGenerado = Number(d.iva_generado ?? 0)
  const baseVentas = Number(d.base_ventas ?? 0)
  const ivaDescontable = Number(d.iva_descontable ?? 0)
  const baseCompras = Number(d.base_compras ?? 0)
  const saldo = Number(d.saldo ?? 0)
  const aPagar = saldo >= 0

  return (
    <div className="space-y-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-end gap-3">
          <h1 className="text-lg font-semibold tracking-tight mr-auto inline-flex items-center gap-2">
            <Calculator className="size-5 text-muted-foreground" /> Libro IVA
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
      </Card>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Metric label="IVA generado" value={cop(ivaGenerado)} sub={`Base ventas ${cop(baseVentas)}`}
          icon={ArrowUpCircle} tone="text-info" />
        <Metric label="IVA descontable" value={cop(ivaDescontable)} sub={`Base compras ${cop(baseCompras)}`}
          icon={ArrowDownCircle} tone="text-warning" />
        <Metric className="col-span-2" label={aPagar ? 'Saldo a pagar' : 'Saldo a favor'} value={cop(Math.abs(saldo))}
          sub={aPagar ? 'IVA generado − descontable (a cargo)' : 'IVA descontable − generado (a favor)'}
          icon={Scale} tone={aPagar ? 'text-destructive' : 'text-success'} hero />
      </div>

      <p className="text-[11px] text-muted-foreground px-1">
        IVA generado = IVA de ventas (no anuladas) del periodo; IVA descontable = IVA de las compras
        fiscales del periodo. El saldo es soporte tributario: este reporte no emite ni consulta a la DIAN.
      </p>
    </div>
  )
}

function Metric({ label, value, sub, icon: Icon, tone, hero, className = '' }) {
  return (
    <Card className={`p-3.5 ${className}`}>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
        <Icon className="size-3.5" /> {label}
      </div>
      <div className={`tabular font-semibold ${hero ? 'text-2xl' : 'text-[15px]'} ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground mt-0.5 tabular">{sub}</div>}
    </Card>
  )
}
