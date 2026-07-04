/*
 * TabFacturasRecibidas — recepción de facturas de proveedor por QR (ADR 0020, F1). SOLO admin.
 * Gateado por `compras_fiscal` (la ruta solo aparece con la capacidad; espeja el back).
 *
 * Escanear/pegar el contenido del QR de una factura electrónica DIAN → el back extrae el CUFE, consulta
 * RADIAN (acuse 030) y registra la CUENTA POR PAGAR con su vencimiento real + el soporte fiscal (CUFE).
 * v1 SIN inventario. Idempotente por CUFE: re-pegar el mismo QR no duplica (el back responde 200).
 *
 * Datos con TanStack Query (ADR 0029): useFacturasRecibidas (listado) + useEscanearQR (mutación que
 * invalida el listado). Cámara no necesaria en v1: basta pegar el texto del QR (URL DIAN o CUFE).
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { AlertTriangle, FileCheck, ScanLine } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { useFacturasRecibidas, useEscanearQR } from '@/lib/queries'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Badge } from '@/components/ui/badge.jsx'

const FECHA_CO = { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'America/Bogota' }

const ESTADO_BADGE = {
  pendiente: 'bg-info/10 text-info border-info/20',
  aceptada: 'bg-success/10 text-success border-success/20',
  reclamada: 'bg-warning/10 text-warning border-warning/20',
}

export default function TabFacturasRecibidas() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        Las facturas recibidas son solo para administradores.
      </Card>
    )
  }
  return <FacturasRecibidasContenido />
}

function FacturasRecibidasContenido() {
  const recibidasQ = useFacturasRecibidas()
  const recibidas = Array.isArray(recibidasQ.data) ? recibidasQ.data : []

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <EscanearQR />
      <ListaRecibidas recibidas={recibidas} loading={recibidasQ.isLoading} error={recibidasQ.isError} />
    </div>
  )
}

const FORM_INICIAL = {
  qr: '', proveedor_nit: '', proveedor_nombre: '', numero_factura: '',
  total: '', base: '', iva: '', fecha: '', fecha_vencimiento: '',
}

function EscanearQR() {
  const [f, setF] = useState(FORM_INICIAL)
  const escanear = useEscanearQR()
  const set = (k) => (e) => setF((prev) => ({ ...prev, [k]: e.target.value }))

  async function enviar() {
    if (!f.qr.trim()) { toast.error('Pega el contenido del QR (URL DIAN o CUFE)'); return }
    if (!f.proveedor_nit.trim()) { toast.error('Indica el NIT del proveedor'); return }
    if (!(Number(f.total) > 0)) { toast.error('El total debe ser mayor a 0'); return }

    const payload = {
      qr: f.qr.trim(),
      proveedor_nit: f.proveedor_nit.trim(),
      proveedor_nombre: f.proveedor_nombre.trim() || null,
      numero_factura: f.numero_factura.trim() || null,
      total: Number(f.total),
      base: Number(f.base) || 0,
      iva: Number(f.iva) || 0,
      fecha: f.fecha || null,
      fecha_vencimiento: f.fecha_vencimiento || null,
    }
    try {
      await escanear.mutateAsync(payload)
      toast.success('Factura recibida registrada')
      setF(FORM_INICIAL)
    } catch (err) {
      if (err?.message === 'qr_invalido') toast.error('El QR no contiene un CUFE reconocible')
      else toast.error('No se pudo registrar la factura recibida')
    }
  }

  return (
    <Card className="p-3.5 space-y-2 self-start">
      <h2 className="text-sm font-semibold inline-flex items-center gap-1.5">
        <ScanLine className="size-4" /> Escanear factura recibida
      </h2>
      <p className="text-[11px] text-muted-foreground">
        Pega el contenido del QR de la factura del proveedor (la URL DIAN o el CUFE). Se registra la cuenta
        por pagar con su vencimiento y se acusa recibo ante la DIAN. No mueve inventario.
      </p>
      <textarea
        value={f.qr} onChange={set('qr')} rows={2} aria-label="Contenido del QR"
        placeholder="https://catalogo-vpfe.dian.gov.co/document/searchqr?documentkey=… o el CUFE"
        className="w-full rounded-md border border-border bg-surface px-2.5 py-1.5 text-[12px] resize-y" />
      <Input value={f.proveedor_nit} onChange={set('proveedor_nit')} placeholder="NIT del proveedor *" aria-label="NIT del proveedor" className="h-9" />
      <Input value={f.proveedor_nombre} onChange={set('proveedor_nombre')} placeholder="Nombre del proveedor (opcional)" aria-label="Nombre del proveedor" className="h-9" />
      <Input value={f.numero_factura} onChange={set('numero_factura')} placeholder="Nº de factura (opcional)" aria-label="Número de factura" className="h-9" />
      <div className="flex gap-2">
        <Input type="number" value={f.base} onChange={set('base')} placeholder="Base (opcional)" aria-label="Base" className="h-9 flex-1" />
        <Input type="number" value={f.iva} onChange={set('iva')} placeholder="IVA (opcional)" aria-label="IVA" className="h-9 flex-1" />
      </div>
      <Input type="number" value={f.total} onChange={set('total')} placeholder="Total *" aria-label="Total" className="h-9" />
      <div className="flex gap-2">
        <label className="flex-1 text-[11px] text-muted-foreground">
          Fecha
          <Input type="date" value={f.fecha} onChange={set('fecha')} aria-label="Fecha de la factura" className="h-9 mt-0.5" />
        </label>
        <label className="flex-1 text-[11px] text-muted-foreground">
          Vencimiento
          <Input type="date" value={f.fecha_vencimiento} onChange={set('fecha_vencimiento')} aria-label="Fecha de vencimiento" className="h-9 mt-0.5" />
        </label>
      </div>
      <button onClick={enviar} disabled={escanear.isPending}
        className="w-full h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
        {escanear.isPending ? 'Registrando…' : 'Registrar factura recibida'}
      </button>
    </Card>
  )
}

function ListaRecibidas({ recibidas, loading, error }) {
  return (
    <Card className="p-0 overflow-hidden self-start">
      <div className="px-3.5 py-2.5 border-b border-border-subtle flex items-center gap-2">
        <FileCheck className="size-4 text-muted-foreground" />
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Facturas recibidas</h2>
      </div>
      {loading ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
      ) : error ? (
        <p className="py-10 text-center text-sm text-destructive">No se pudieron cargar las facturas recibidas.</p>
      ) : recibidas.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">Aún no has recibido facturas por QR.</p>
      ) : (
        <ul className="divide-y divide-border-subtle">
          {recibidas.map((r) => <RecibidaRow key={r.cufe} r={r} />)}
        </ul>
      )}
    </Card>
  )
}

function RecibidaRow({ r }) {
  return (
    <li className="px-3.5 py-2.5 space-y-1 text-[13px]">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium truncate">
            {r.proveedor_nit ? `NIT ${r.proveedor_nit}` : 'Proveedor'}
            {r.numero_factura ? ` · ${r.numero_factura}` : ''}
          </div>
          <div className="text-[11px] text-muted-foreground truncate">
            CUFE {String(r.cufe).slice(0, 16)}…
            {r.evento_030_at ? ' · acuse ✓' : ''}
          </div>
        </div>
        <span className="tabular font-semibold shrink-0">{cop(Number(r.total))}</span>
        {r.evento_estado && (
          <Badge variant="outline" className={`h-5 text-[10px] capitalize shrink-0 ${ESTADO_BADGE[r.evento_estado] || ''}`}>
            {r.evento_estado}
          </Badge>
        )}
      </div>
      <div className="text-[11px] text-muted-foreground tabular">
        {r.pendiente != null ? `pendiente ${cop(Number(r.pendiente))}` : ''}
        {r.fecha_vencimiento ? ` · vence ${new Date(r.fecha_vencimiento).toLocaleDateString('es-CO', FECHA_CO)}` : ' · sin vencimiento'}
      </div>
      {r.evento_error && (
        <div className="flex items-start gap-1.5 text-[11px] text-destructive">
          <AlertTriangle className="size-3.5 mt-0.5 shrink-0" />
          <span>Acuse DIAN pendiente: {r.evento_error}</span>
        </div>
      )}
    </li>
  )
}
