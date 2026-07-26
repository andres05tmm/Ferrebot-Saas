/*
 * FichaProveedor — el estado con UN proveedor: cuánto se le debe, qué tan viejo es lo que se debe,
 * y todo lo que ha pasado con él.
 *
 * Patrón de las plataformas contables (QuickBooks Vendor Center, Odoo Supplier Statement): cabecera
 * con saldo + antigüedad en tramos, y pestañas de transacciones. La primera es el ESTADO DE CUENTA
 * con saldo corrido —cada factura y cada pago en orden, con el saldo acumulado al lado—, que es lo
 * que un contador pide primero.
 *
 * Ventana por defecto: 6 meses (decisión del dueño). Para llevarse el histórico completo está el
 * botón de PDF, que arma el estado de cuenta desde el primer movimiento.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Download, FileText, ImagePlus, Package, Receipt, Truck, Wallet } from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { cop, SkeletonFilas } from '@/components/shared.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs.jsx'
import { descargarEstadoCuenta } from './pdf.js'

const arr = (d) => (Array.isArray(d) ? d : [])
const fecha = (iso) => (iso
  ? new Date(`${String(iso).slice(0, 10)}T12:00:00-05:00`).toLocaleDateString('es-CO', {
    day: '2-digit', month: 'short', year: '2-digit',
  })
  : '—')

const MEDIO = {
  caja: 'efectivo de caja',
  efectivo_externo: 'efectivo guardado',
  banco: 'transferencia',
  mixto: 'pago mixto',
}

function Metrica({ label, valor, tono = '' }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${tono}`}>{valor}</div>
    </div>
  )
}

/** Antigüedad de la deuda en tramos, como el aging de los contables. */
function Aging({ aging }) {
  const tramos = ['0-30', '31-60', '61-90', '90+']
  const total = tramos.reduce((a, t) => a + Number(aging?.[t] || 0), 0)
  if (total <= 0) return null
  return (
    <div className="flex gap-2 flex-wrap">
      {tramos.map(t => {
        const v = Number(aging?.[t] || 0)
        if (v <= 0) return null
        const viejo = t === '61-90' || t === '90+'
        return (
          <Badge key={t} variant="outline"
            className={viejo ? 'bg-danger/10 text-danger border-danger/20' : ''}>
            {t} días · {cop(v)}
          </Badge>
        )
      })}
    </div>
  )
}

export default function FichaProveedor({ proveedor, onAbonar, onFactura, onComprar, onEditar }) {
  const [descargando, setDescargando] = useState(false)
  // Disponibilidad de fotos: optimista; si una subida responde 503, se apaga con aviso.
  const [fotos, setFotos] = useState(true)
  const id = proveedor.id

  const cuentaQ = useQuery({
    queryKey: ['proveedor', id, 'estado-cuenta'],
    queryFn: () => apiJson(`/proveedores/${id}/estado-cuenta`),
  })
  const pedidosQ = useQuery({
    queryKey: ['proveedor', id, 'pedidos'],
    queryFn: () => apiJson(`/pedidos-proveedor?proveedor_id=${id}`),
  })
  const comprasQ = useQuery({
    queryKey: ['proveedor', id, 'compras'],
    queryFn: () => apiJson(`/compras?proveedor_id=${id}`),
  })
  const facturasQ = useQuery({
    queryKey: ['proveedor', id, 'facturas'],
    queryFn: () => apiJson('/proveedores/facturas'),
  })

  const cuenta = cuentaQ.data
  const movimientos = arr(cuenta?.movimientos)
  const facturas = arr(facturasQ.data).filter(f => f.proveedor_id === id)
  const abonos = movimientos.filter(m => m.tipo === 'abono')

  async function subirFoto(factura, file) {
    const fd = new FormData()
    fd.append('file', file)
    try {
      const res = await api(`/proveedores/facturas/${encodeURIComponent(factura.id)}/foto`, {
        method: 'POST', body: fd,
      })
      if (res.status === 503) {
        setFotos(false)
        toast.error('Fotos no disponibles: Cloudinary no está configurado para esta empresa.')
        return
      }
      if (res.ok) { toast.success('Foto subida'); facturasQ.refetch() }
      else toast.error('No se pudo subir la foto')
    } catch { toast.error('Error de conexión') }
  }

  async function descargarPdf() {
    setDescargando(true)
    try {
      // El PDF se lleva TODO el histórico (no los 6 meses de la pantalla): eso es lo que se archiva.
      const completo = await apiJson(`/proveedores/${id}/estado-cuenta?desde=2000-01-01`)
      descargarEstadoCuenta(completo)
    } catch { /* el botón vuelve solo; el estado de cuenta en pantalla sigue disponible */ }
    finally { setDescargando(false) }
  }

  return (
    <div className="space-y-3">
      <Card className="p-3.5 space-y-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <h2 className="text-base font-semibold truncate">{proveedor.nombre}</h2>
            <p className="text-caption text-muted-foreground">
              {[proveedor.nit && `NIT ${proveedor.nit}`, proveedor.telefono,
                proveedor.contacto_nombre].filter(Boolean).join(' · ') || 'Sin datos de contacto'}
            </p>
          </div>
          <div className="flex gap-2 flex-wrap">
            <Button size="sm" onClick={() => onAbonar(proveedor)}>Abonar</Button>
            <Button size="sm" variant="outline" onClick={() => onFactura(proveedor)}>
              Registrar factura
            </Button>
            <Button size="sm" variant="outline" onClick={() => onComprar(proveedor)}>
              Nueva compra
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onEditar(proveedor)}>Editar</Button>
          </div>
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Metrica label="Se le debe" valor={cop(Number(cuenta?.saldo_pendiente || 0))} />
          <Metrica label="Vencido" valor={cop(Number(cuenta?.vencido || 0))}
            tono={Number(cuenta?.vencido || 0) > 0 ? 'text-danger' : ''} />
          <Metrica label="Pedidos en camino" valor={proveedor.pedidos_en_camino ?? 0} />
          <Metrica label="Suele tardar"
            valor={proveedor.lead_time_promedio_horas != null
              ? `${Math.round(proveedor.lead_time_promedio_horas / 24 * 10) / 10} días`
              : '—'} />
        </div>
        <Aging aging={cuenta?.aging} />
      </Card>

      <Tabs defaultValue="cuenta">
        <TabsList>
          <TabsTrigger value="cuenta">Estado de cuenta</TabsTrigger>
          <TabsTrigger value="pedidos">Pedidos</TabsTrigger>
          <TabsTrigger value="compras">Compras</TabsTrigger>
          <TabsTrigger value="facturas">Facturas</TabsTrigger>
          <TabsTrigger value="pagos">Pagos</TabsTrigger>
        </TabsList>

        {/* Estado de cuenta: el movimiento a movimiento con saldo corrido. */}
        <TabsContent value="cuenta" className="mt-3">
          <Card className="p-3.5">
            <div className="flex items-center justify-between gap-2 mb-2">
              <h3 className="text-body-sm font-semibold inline-flex items-center gap-1.5">
                <FileText className="size-4 text-primary" /> Movimientos
                <span className="text-caption font-normal text-muted-foreground">
                  {fecha(cuenta?.desde)} a {fecha(cuenta?.hasta)}
                </span>
              </h3>
              <Button size="sm" variant="outline" onClick={descargarPdf} disabled={descargando}
                className="gap-1.5">
                <Download className="size-3.5" />
                {descargando ? 'Armando…' : 'PDF histórico'}
              </Button>
            </div>
            {cuentaQ.isLoading ? (
              <SkeletonFilas filas={6} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-body-sm">
                  <thead>
                    <tr className="text-left text-caption text-muted-foreground">
                      <th className="py-1 pr-2 font-normal">Fecha</th>
                      <th className="py-1 pr-2 font-normal">Concepto</th>
                      <th className="py-1 pr-2 font-normal text-right">Debe</th>
                      <th className="py-1 pr-2 font-normal text-right">Pagado</th>
                      <th className="py-1 font-normal text-right">Saldo</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-t border-border-subtle text-muted-foreground">
                      <td className="py-1.5 pr-2">{fecha(cuenta?.desde)}</td>
                      <td className="py-1.5 pr-2 italic">Saldo anterior</td>
                      <td /><td />
                      <td className="py-1.5 text-right tabular-nums">
                        {cop(Number(cuenta?.saldo_anterior || 0))}
                      </td>
                    </tr>
                    {movimientos.map((m, i) => (
                      <tr key={`${m.tipo}-${m.referencia}-${i}`} className="border-t border-border-subtle">
                        <td className="py-1.5 pr-2">{fecha(m.fecha)}</td>
                        <td className="py-1.5 pr-2">
                          {m.tipo === 'factura' ? 'Factura' : 'Abono'} {m.referencia}
                          {m.medio && (
                            <span className="text-caption text-muted-foreground">
                              {' '}· {MEDIO[m.medio] || m.medio}
                            </span>
                          )}
                        </td>
                        <td className="py-1.5 pr-2 text-right tabular-nums">
                          {Number(m.cargo) > 0 ? cop(Number(m.cargo)) : ''}
                        </td>
                        <td className="py-1.5 pr-2 text-right tabular-nums text-success">
                          {Number(m.abono) > 0 ? cop(Number(m.abono)) : ''}
                        </td>
                        <td className="py-1.5 text-right tabular-nums font-medium">{cop(Number(m.saldo))}</td>
                      </tr>
                    ))}
                    {movimientos.length === 0 && (
                      <tr><td colSpan={5} className="py-6 text-center text-muted-foreground">
                        Sin movimientos en el periodo.
                      </td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </TabsContent>

        <TabsContent value="pedidos" className="mt-3">
          <Card className="p-3.5">
            <h3 className="text-body-sm font-semibold mb-2 inline-flex items-center gap-1.5">
              <Truck className="size-4 text-primary" /> Historial de pedidos
            </h3>
            <Listado
              cargando={pedidosQ.isLoading}
              filas={arr(pedidosQ.data)}
              vacio="Nunca se le ha hecho un pedido."
              render={(p) => ({
                clave: p.id,
                izquierda: `#${p.id} · ${p.detalles?.length || 0} producto(s)`,
                sub: `pedido ${fecha(p.fecha_pedido)}${
                  p.fecha_recepcion ? ` · llegó ${fecha(p.fecha_recepcion)}` : ''}`,
                derecha: cop(Number(p.monto_estimado || 0)),
                badge: p.estado === 'pedido' ? 'en camino'
                  : p.estado === 'recibido' ? 'recibido' : 'cancelado',
              })}
            />
          </Card>
        </TabsContent>

        <TabsContent value="compras" className="mt-3">
          <Card className="p-3.5">
            <h3 className="text-body-sm font-semibold mb-2 inline-flex items-center gap-1.5">
              <Package className="size-4 text-primary" /> Mercancía recibida
            </h3>
            <Listado
              cargando={comprasQ.isLoading}
              filas={arr(comprasQ.data)}
              vacio="Sin compras registradas en el rango."
              render={(c) => ({
                clave: c.id,
                izquierda: `Compra #${c.id}`,
                sub: fecha(c.fecha),
                derecha: cop(Number(c.total || 0)),
              })}
            />
          </Card>
        </TabsContent>

        <TabsContent value="facturas" className="mt-3">
          <Card className="p-3.5">
            <h3 className="text-body-sm font-semibold mb-2 inline-flex items-center gap-1.5">
              <Receipt className="size-4 text-primary" /> Facturas
            </h3>
            {!fotos && (
              <p className="mb-2 text-caption text-muted-foreground">
                Las fotos de soporte están deshabilitadas (Cloudinary no configurado).
              </p>
            )}
            <Listado
              cargando={facturasQ.isLoading}
              filas={facturas}
              vacio="Sin facturas registradas."
              render={(f) => ({
                clave: f.id,
                izquierda: (
                  <>
                    {f.id}
                    {f.foto_url && (
                      <a href={f.foto_url} target="_blank" rel="noreferrer"
                        className="ml-2 text-caption text-primary hover:underline">ver soporte</a>
                    )}
                  </>
                ),
                sub: `${fecha(f.fecha)}${f.fecha_vencimiento ? ` · vence ${fecha(f.fecha_vencimiento)}` : ''}`,
                derecha: `${cop(Number(f.pendiente))} de ${cop(Number(f.total))}`,
                badge: f.estado,
                accion: fotos && (
                  <label title="Subir foto"
                    className="size-8 grid place-items-center rounded-md border border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2 cursor-pointer shrink-0">
                    <ImagePlus className="size-4" />
                    <input type="file" className="hidden" aria-label={`Subir foto ${f.id}`}
                      onChange={(e) => { const x = e.target.files?.[0]; if (x) subirFoto(f, x) }} />
                  </label>
                ),
              })}
            />
          </Card>
        </TabsContent>

        <TabsContent value="pagos" className="mt-3">
          <Card className="p-3.5">
            <h3 className="text-body-sm font-semibold mb-2 inline-flex items-center gap-1.5">
              <Wallet className="size-4 text-primary" /> Pagos y abonos
            </h3>
            <Listado
              cargando={cuentaQ.isLoading}
              filas={abonos}
              vacio="Todavía no se le ha pagado nada en el periodo."
              render={(a, i) => ({
                clave: `${a.referencia}-${i}`,
                izquierda: `Abono a ${a.referencia}`,
                sub: `${fecha(a.fecha)}${a.medio ? ` · ${MEDIO[a.medio] || a.medio}` : ''}`,
                derecha: cop(Number(a.abono)),
              })}
            />
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

/** Lista simple de dos columnas: la misma forma para pedidos, compras, facturas y pagos. */
function Listado({ cargando, filas, vacio, render }) {
  if (cargando) return <SkeletonFilas filas={4} />
  if (filas.length === 0) return <p className="py-4 text-center text-body-sm text-muted-foreground">{vacio}</p>
  return (
    <ul className="divide-y divide-border-subtle">
      {filas.map((f, i) => {
        const r = render(f, i)
        return (
          <li key={r.clave} className="py-2 flex items-center gap-3 text-body-sm">
            <div className="min-w-0 flex-1">
              <div className="truncate">{r.izquierda}</div>
              <div className="text-caption text-muted-foreground truncate">{r.sub}</div>
            </div>
            {r.badge && <Badge variant="outline" className="text-[10px] shrink-0">{r.badge}</Badge>}
            <span className="tabular-nums shrink-0">{r.derecha}</span>
            {r.accion}
          </li>
        )
      })}
    </ul>
  )
}
