/*
 * TabPedidosProveedor — pedidos a proveedor con cronómetro de lead time (reforma dashboard F2).
 * Gateada por la feature `pedidos_proveedor`.
 *
 * El flujo del negocio: se llama al proveedor → se registra el pedido AQUÍ (arranca el cronómetro;
 * si el proveedor cobra por adelantado, el anticipo egresa de la caja) → cuando llega la mercancía,
 * "Llegó" abre el modal de recepción: productos/cantidades/costos REALES + condición de pago
 * (contado/crédito/anticipado) + cuadre de inventario progresivo ("¿cuánto hay físicamente?").
 * El backend asienta todo en una transacción: compra (ENTRADA + costo), deuda o pago, y cuadre.
 *
 * Captura FLEXIBLE (decisión del dueño): descripción + monto estimado bastan al pedir; el detalle
 * preciso se fija al recibir. Semáforo del cronómetro: verde/ámbar/rojo vs el promedio histórico
 * del proveedor (o la fecha estimada).
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  PackageSearch, Plus, Truck, Timer, CheckCircle2, XCircle, Search, Trash2,
} from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { cop } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Badge } from '@/components/ui/badge.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'

const EVENTOS = [
  'pedido_proveedor_creado', 'pedido_proveedor_recibido', 'pedido_proveedor_cancelado',
  'pedido_demorado',   // el cron F6 acaba de avisar: refresca la lista (horas/semáforo al día)
]
const KEY = ['pedidos-proveedor']

const FILTROS = [
  { id: 'pedido', label: 'En camino' },
  { id: 'recibido', label: 'Recibidos' },
  { id: '', label: 'Todos' },
]

const arr = (d) => (Array.isArray(d) ? d : [])

function horasATexto(h) {
  if (h == null) return '—'
  if (h < 24) return `${Math.round(h)} h`
  const dias = h / 24
  return `${dias < 10 ? dias.toFixed(1) : Math.round(dias)} días`
}

function fechaCorta(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('es-CO', {
    day: '2-digit', month: 'short', timeZone: 'America/Bogota',
  })
}

// Semáforo del cronómetro: rojo si superó el promedio del proveedor (o la fecha estimada),
// ámbar desde el 75% del promedio, verde antes. Sin referencia → neutro.
function tonoCronometro(p) {
  if (p.estado !== 'pedido') return 'neutro'
  if (p.fecha_estimada) {
    const vence = new Date(`${p.fecha_estimada}T23:59:59-05:00`)
    return Date.now() > vence.getTime() ? 'rojo' : 'verde'
  }
  if (p.promedio_proveedor_horas == null || p.horas_transcurridas == null) return 'neutro'
  if (p.horas_transcurridas > p.promedio_proveedor_horas) return 'rojo'
  if (p.horas_transcurridas > p.promedio_proveedor_horas * 0.75) return 'ambar'
  return 'verde'
}

const TONO_CLS = {
  rojo: 'bg-danger/10 text-danger border-danger/20',
  ambar: 'bg-warning/10 text-warning border-warning/20',
  verde: 'bg-success/10 text-success border-success/20',
  neutro: 'bg-muted text-muted-foreground border-border',
}

// --- Buscador de producto (compartido por los dos modales) -------------------
function BuscadorProducto({ onPick, placeholder = 'Buscar producto para agregar…' }) {
  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])

  async function buscar(term) {
    setQ(term)
    if (!term.trim()) { setResultados([]); return }
    try {
      const d = await apiJson(`/productos?q=${encodeURIComponent(term)}&limite=8`)
      setResultados(arr(d))
    } catch { setResultados([]) }
  }

  return (
    <div className="relative">
      <Search className="size-4 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input value={q} onChange={(e) => buscar(e.target.value)} placeholder={placeholder}
        aria-label="Buscar producto" className="pl-8" />
      {resultados.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full bg-surface border border-border rounded-md shadow-md max-h-48 overflow-y-auto">
          {resultados.map(p => (
            <li key={p.id}>
              <button type="button"
                onClick={() => { onPick(p); setQ(''); setResultados([]) }}
                className="w-full text-left px-3 py-1.5 text-body-sm hover:bg-surface-2 flex justify-between gap-2">
                <span className="truncate">{p.nombre}</span>
                <span className="text-muted-foreground shrink-0">{cop(Number(p.precio_compra || 0))}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// --- Modal: registrar pedido (arranca el cronómetro) -------------------------
function ModalCrearPedido({ abierto, onCerrar, onCreado }) {
  const [proveedor, setProveedor] = useState('')
  const [descripcion, setDescripcion] = useState('')
  const [monto, setMonto] = useState('')
  const [fechaEstimada, setFechaEstimada] = useState('')
  const [anticipo, setAnticipo] = useState('')
  const [anticipoDeCaja, setAnticipoDeCaja] = useState(true)
  const [lineas, setLineas] = useState([])   // {producto_id, nombre, cantidad, costo_estimado}
  const [enviando, setEnviando] = useState(false)

  const valido = proveedor.trim() && (descripcion.trim() || lineas.length > 0)

  function limpiar() {
    setProveedor(''); setDescripcion(''); setMonto(''); setFechaEstimada('')
    setAnticipo(''); setAnticipoDeCaja(true); setLineas([])
  }

  async function crear(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    const payload = {
      proveedor: { nombre: proveedor.trim() },
      descripcion: descripcion.trim() || null,
      monto_estimado: monto ? Number(monto) : null,
      fecha_estimada: fechaEstimada || null,
      lineas: lineas.map(l => ({
        producto_id: l.producto_id, cantidad: Number(l.cantidad), costo_estimado: l.costo_estimado ? Number(l.costo_estimado) : null,
      })),
    }
    if (anticipo) {
      payload.anticipo = Number(anticipo)
      payload.anticipo_desde_caja = anticipoDeCaja
    }
    setEnviando(true)
    try {
      const res = await api('/pedidos-proveedor', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        toast.success('Pedido registrado — el cronómetro está corriendo')
        limpiar(); onCreado()
      } else {
        const err = await res.json().catch(() => ({}))
        toast.error(typeof err?.detail === 'string' ? err.detail : 'No se pudo registrar el pedido')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open={abierto} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="crear-pedido-desc">
        <DialogHeader>
          <DialogTitle>Registrar pedido a proveedor</DialogTitle>
          <DialogDescription id="crear-pedido-desc">
            Rápido: proveedor y qué se pidió. Los productos y costos exactos se registran cuando
            llegue la mercancía.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={crear} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="pp-proveedor">Proveedor</Label>
            <Input id="pp-proveedor" value={proveedor} onChange={(e) => setProveedor(e.target.value)}
              placeholder="Ferrisariato" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="pp-desc">¿Qué se pidió?</Label>
            <Input id="pp-desc" value={descripcion} onChange={(e) => setDescripcion(e.target.value)}
              placeholder="50 martillos, 2 cajas de puntilla…" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="pp-monto">Monto estimado</Label>
              <Input id="pp-monto" type="number" inputMode="numeric" min="0" value={monto}
                onChange={(e) => setMonto(e.target.value)} placeholder="0" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="pp-fecha">Llega aprox.</Label>
              <Input id="pp-fecha" type="date" value={fechaEstimada}
                onChange={(e) => setFechaEstimada(e.target.value)} />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Productos (opcional)</Label>
            <BuscadorProducto onPick={(p) => setLineas(prev => [...prev, {
              producto_id: p.id, nombre: p.nombre, cantidad: '1', costo_estimado: p.precio_compra || '',
            }])} />
            {lineas.map((l, i) => (
              <div key={`${l.producto_id}-${i}`} className="flex items-center gap-2 text-body-sm">
                <span className="flex-1 truncate">{l.nombre}</span>
                <Input type="number" inputMode="numeric" min="0" value={l.cantidad} aria-label={`Cantidad ${l.nombre}`}
                  onChange={(e) => setLineas(prev => prev.map((x, j) => j === i ? { ...x, cantidad: e.target.value } : x))}
                  className="w-20 h-8" />
                <Input type="number" inputMode="numeric" min="0" value={l.costo_estimado} aria-label={`Costo estimado ${l.nombre}`}
                  onChange={(e) => setLineas(prev => prev.map((x, j) => j === i ? { ...x, costo_estimado: e.target.value } : x))}
                  className="w-28 h-8" placeholder="costo est." />
                <button type="button" onClick={() => setLineas(prev => prev.filter((_, j) => j !== i))}
                  aria-label={`Quitar ${l.nombre}`} className="text-muted-foreground hover:text-danger">
                  <Trash2 className="size-4" />
                </button>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3 items-end">
            <div className="space-y-1.5">
              <Label htmlFor="pp-anticipo">Anticipo (si se pagó por adelantado)</Label>
              <Input id="pp-anticipo" type="number" inputMode="numeric" min="0" value={anticipo}
                onChange={(e) => setAnticipo(e.target.value)} placeholder="0" />
            </div>
            {anticipo && (
              <label className="flex items-center gap-2 text-body-sm pb-2">
                <input type="checkbox" checked={anticipoDeCaja}
                  onChange={(e) => setAnticipoDeCaja(e.target.checked)} />
                Salió de la caja
              </label>
            )}
          </div>

          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Registrando…' : 'Registrar pedido'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// --- Modal: llegó la mercancía (recepción) ------------------------------------
function ModalRecibir({ pedido, onCerrar, onRecibido }) {
  const [lineas, setLineas] = useState(() =>
    (pedido?.detalles || [])
      .filter(d => d.producto_id != null)
      .map(d => ({
        producto_id: d.producto_id, nombre: d.descripcion || `Producto #${d.producto_id}`,
        cantidad: String(d.cantidad), costo: d.costo_estimado != null ? String(d.costo_estimado) : '',
        cuadrar: false, cantidad_fisica: '',
      })),
  )
  const [condicion, setCondicion] = useState(pedido?.anticipo ? 'anticipado' : 'credito')
  const [pagoDesdeCaja, setPagoDesdeCaja] = useState(false)
  const [numeroFactura, setNumeroFactura] = useState('')
  const [vencimiento, setVencimiento] = useState('')
  const [enviando, setEnviando] = useState(false)

  const total = lineas.reduce((acc, l) => acc + Number(l.cantidad || 0) * Number(l.costo || 0), 0)
  const anticipo = Number(pedido?.anticipo || 0)
  const remanente = Math.max(0, total - anticipo)
  const lineasValidas = lineas.length > 0 && lineas.every(l => Number(l.cantidad) > 0 && l.costo !== '')
  const necesitaDestinoRemanente = condicion === 'anticipado' && remanente > 0
    && !pagoDesdeCaja && !numeroFactura.trim()
  const valido = lineasValidas && !necesitaDestinoRemanente
    && (condicion !== 'credito' || true)

  async function recibir(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    const payload = {
      lineas: lineas.map(l => ({
        producto_id: l.producto_id, cantidad: Number(l.cantidad), costo: Number(l.costo),
        cantidad_fisica: l.cuadrar && l.cantidad_fisica !== '' ? Number(l.cantidad_fisica) : null,
      })),
      condicion_pago: condicion,
      pago_desde_caja: pagoDesdeCaja,
      numero_factura: numeroFactura.trim() || null,
      fecha_vencimiento: vencimiento || null,
    }
    setEnviando(true)
    try {
      const res = await api(`/pedidos-proveedor/${pedido.id}/recibir`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        toast.success('Mercancía recibida: inventario y cuentas al día')
        onRecibido()
      } else {
        const err = await res.json().catch(() => ({}))
        toast.error(typeof err?.detail === 'string' ? err.detail : 'No se pudo registrar la recepción')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open={pedido != null} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="recibir-desc" className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Llegó la mercancía — pedido #{pedido?.id}</DialogTitle>
          <DialogDescription id="recibir-desc">
            Registra lo que llegó DE VERDAD (producto, cantidad y costo real): esto entra al
            inventario, fija el costo y asienta la deuda o el pago.
          </DialogDescription>
        </DialogHeader>
        {pedido?.descripcion && (
          <p className="text-body-sm text-muted-foreground -mt-2">Se pidió: “{pedido.descripcion}”</p>
        )}
        <form onSubmit={recibir} className="space-y-3">
          <BuscadorProducto onPick={(p) => setLineas(prev => [...prev, {
            producto_id: p.id, nombre: p.nombre, cantidad: '1',
            costo: p.precio_compra != null ? String(p.precio_compra) : '',
            cuadrar: false, cantidad_fisica: '',
          }])} placeholder="Agregar producto que llegó…" />

          {lineas.length === 0 && (
            <p className="text-body-sm text-muted-foreground">Agrega los productos que llegaron.</p>
          )}
          {lineas.map((l, i) => (
            <div key={`${l.producto_id}-${i}`} className="border border-border rounded-md p-2 space-y-2">
              <div className="flex items-center gap-2 text-body-sm">
                <span className="flex-1 truncate font-medium">{l.nombre}</span>
                <Input type="number" inputMode="numeric" min="0" value={l.cantidad} aria-label={`Cantidad recibida ${l.nombre}`}
                  onChange={(e) => setLineas(prev => prev.map((x, j) => j === i ? { ...x, cantidad: e.target.value } : x))}
                  className="w-20 h-8" />
                <Input type="number" inputMode="numeric" min="0" value={l.costo} aria-label={`Costo real ${l.nombre}`}
                  onChange={(e) => setLineas(prev => prev.map((x, j) => j === i ? { ...x, costo: e.target.value } : x))}
                  className="w-28 h-8" placeholder="costo real" />
                <button type="button" onClick={() => setLineas(prev => prev.filter((_, j) => j !== i))}
                  aria-label={`Quitar ${l.nombre}`} className="text-muted-foreground hover:text-danger">
                  <Trash2 className="size-4" />
                </button>
              </div>
              <label className="flex items-center gap-2 text-caption text-muted-foreground">
                <input type="checkbox" checked={l.cuadrar}
                  onChange={(e) => setLineas(prev => prev.map((x, j) => j === i
                    ? { ...x, cuadrar: e.target.checked, cantidad_fisica: e.target.checked ? x.cantidad : '' }
                    : x))} />
                Cuadrar inventario: ¿cuánto hay físicamente ahora?
                {l.cuadrar && (
                  <Input type="number" inputMode="numeric" min="0" value={l.cantidad_fisica}
                    aria-label={`Cantidad física ${l.nombre}`}
                    onChange={(e) => setLineas(prev => prev.map((x, j) => j === i ? { ...x, cantidad_fisica: e.target.value } : x))}
                    className="w-24 h-7" />
                )}
              </label>
            </div>
          ))}

          <div className="flex items-center justify-between text-body-sm">
            <span className="text-muted-foreground">Total real</span>
            <span className="font-semibold tabular-nums">{cop(total)}</span>
          </div>
          {anticipo > 0 && (
            <div className="flex items-center justify-between text-body-sm">
              <span className="text-muted-foreground">Anticipo ya entregado</span>
              <span className="tabular-nums">−{cop(anticipo)} → queda {cop(remanente)}</span>
            </div>
          )}

          <div className="space-y-1.5">
            <Label>¿Cómo se paga{anticipo > 0 ? ' el resto' : ''}?</Label>
            <div className="flex gap-2 flex-wrap">
              {[
                ...(anticipo > 0 ? [{ id: 'anticipado', label: 'Ya estaba pagado (anticipo)' }] : []),
                { id: 'contado', label: 'De contado' },
                { id: 'credito', label: 'A crédito (queda debiendo)' },
              ].map(o => (
                <button key={o.id} type="button" onClick={() => setCondicion(o.id)}
                  className={`px-2.5 py-1 rounded-md border text-body-sm ${
                    condicion === o.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
                  {o.label}
                </button>
              ))}
            </div>
          </div>

          {(condicion === 'contado' || (condicion === 'anticipado' && remanente > 0)) && (
            <label className="flex items-center gap-2 text-body-sm">
              <input type="checkbox" checked={pagoDesdeCaja}
                onChange={(e) => setPagoDesdeCaja(e.target.checked)} />
              {condicion === 'contado' ? 'El pago sale de la caja ahora' : `El remanente (${cop(remanente)}) sale de la caja`}
            </label>
          )}
          {(condicion === 'credito' || (condicion === 'anticipado' && remanente > 0 && !pagoDesdeCaja)) && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="pp-factura">Nº factura del proveedor</Label>
                <Input id="pp-factura" value={numeroFactura} onChange={(e) => setNumeroFactura(e.target.value)}
                  placeholder={condicion === 'credito' ? `PED-${pedido?.id}` : 'obligatorio'} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="pp-vence">Vence</Label>
                <Input id="pp-vence" type="date" value={vencimiento}
                  onChange={(e) => setVencimiento(e.target.value)} />
              </div>
            </div>
          )}
          {necesitaDestinoRemanente && (
            <p className="text-caption text-warning">
              La mercancía costó más que el anticipo: indica si el resto sale de caja o queda a crédito.
            </p>
          )}

          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Registrando…' : 'Registrar llegada'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// --- Tab ----------------------------------------------------------------------
export default function TabPedidosProveedor() {
  const [filtro, setFiltro] = useState('pedido')
  const [crearAbierto, setCrearAbierto] = useState(false)
  const [recibiendo, setRecibiendo] = useState(null)   // pedido | null
  const qc = useQueryClient()

  const pedidosQ = useQuery({
    queryKey: [...KEY, filtro],
    queryFn: () => apiJson(`/pedidos-proveedor${filtro ? `?estado=${filtro}` : ''}`),
  })
  const metricasQ = useQuery({
    queryKey: [...KEY, 'metricas'],
    queryFn: () => apiJson('/pedidos-proveedor/metricas'),
  })
  useRealtimeEvent(EVENTOS, () => qc.invalidateQueries({ queryKey: KEY }))

  const pedidos = arr(pedidosQ.data)
  const metricas = arr(metricasQ.data)
  const enCamino = useMemo(
    () => metricas.reduce((acc, m) => acc + m.pedidos_en_camino, 0), [metricas],
  )
  const masViejo = useMemo(
    () => metricas.reduce((acc, m) => Math.max(acc, m.mas_viejo_en_camino_horas || 0), 0), [metricas],
  )

  function refrescar() {
    setCrearAbierto(false); setRecibiendo(null)
    qc.invalidateQueries({ queryKey: KEY })
  }

  async function cancelar(p) {
    if (!window.confirm(`¿Cancelar el pedido #${p.id} a ${p.proveedor_nombre}?`)) return
    try {
      const res = await api(`/pedidos-proveedor/${p.id}/cancelar`, { method: 'POST' })
      if (res.ok) { toast.success('Pedido cancelado'); refrescar() }
      else toast.error('No se pudo cancelar')
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-base font-semibold inline-flex items-center gap-2">
          <PackageSearch className="size-4.5 text-primary" aria-hidden="true" /> Pedidos a proveedor
        </h1>
        <Button onClick={() => setCrearAbierto(true)}>
          <Plus className="size-4 mr-1" aria-hidden="true" /> Registrar pedido
        </Button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <Card className="p-3">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">En camino</div>
          <div className="text-lg font-semibold tabular-nums">{metricasQ.isLoading ? '…' : enCamino}</div>
        </Card>
        <Card className="p-3">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">Más viejo esperando</div>
          <div className="text-lg font-semibold tabular-nums">
            {metricasQ.isLoading ? '…' : (enCamino ? horasATexto(masViejo) : '—')}
          </div>
        </Card>
        <Card className="p-3 hidden lg:block">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">Proveedores con historial</div>
          <div className="text-lg font-semibold tabular-nums">
            {metricasQ.isLoading ? '…' : metricas.filter(m => m.pedidos_recibidos > 0).length}
          </div>
        </Card>
      </div>

      <div className="flex gap-2">
        {FILTROS.map(f => (
          <button key={f.id} onClick={() => setFiltro(f.id)}
            className={`px-2.5 py-1 rounded-md border text-body-sm ${
              filtro === f.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
            {f.label}
          </button>
        ))}
      </div>

      <Card className="p-0 divide-y divide-border-subtle">
        {pedidosQ.isLoading && <div className="p-4 text-body-sm text-muted-foreground">Cargando…</div>}
        {!pedidosQ.isLoading && pedidos.length === 0 && (
          <div className="p-6 text-center text-body-sm text-muted-foreground">
            {filtro === 'pedido'
              ? 'No hay pedidos en camino. Cuando llames al proveedor, regístralo aquí para medir cuánto tarda.'
              : 'Nada por aquí todavía.'}
          </div>
        )}
        {pedidos.map(p => {
          const tono = tonoCronometro(p)
          return (
            <div key={p.id} className="p-3 flex items-center gap-3 flex-wrap">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-body-sm truncate">{p.proveedor_nombre || `Proveedor #${p.proveedor_id}`}</span>
                  {p.anticipo && (
                    <Badge variant="outline" className="text-[10px]">anticipo {cop(Number(p.anticipo))}</Badge>
                  )}
                </div>
                <div className="text-caption text-muted-foreground truncate">
                  {p.descripcion || (p.detalles?.length ? `${p.detalles.length} producto(s)` : '')}
                  {p.monto_estimado ? ` · ~${cop(Number(p.monto_estimado))}` : ''}
                  {` · pedido ${fechaCorta(p.fecha_pedido)}`}
                </div>
              </div>

              {p.estado === 'pedido' && (
                <Badge variant="outline" className={`inline-flex items-center gap-1 ${TONO_CLS[tono]}`}>
                  <Timer className="size-3" aria-hidden="true" />
                  {horasATexto(p.horas_transcurridas)}
                  {p.promedio_proveedor_horas != null && (
                    <span className="opacity-70">/ suele tardar {horasATexto(p.promedio_proveedor_horas)}</span>
                  )}
                </Badge>
              )}
              {p.estado === 'recibido' && (
                <Badge variant="outline" className={TONO_CLS.verde}>
                  <CheckCircle2 className="size-3 mr-1" aria-hidden="true" />
                  llegó en {horasATexto(p.lead_time_horas)}
                </Badge>
              )}
              {p.estado === 'cancelado' && (
                <Badge variant="outline" className={TONO_CLS.neutro}>
                  <XCircle className="size-3 mr-1" aria-hidden="true" /> cancelado
                </Badge>
              )}

              {p.estado === 'pedido' && (
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setRecibiendo(p)}>
                    <Truck className="size-4 mr-1" aria-hidden="true" /> Llegó
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => cancelar(p)}>Cancelar</Button>
                </div>
              )}
            </div>
          )
        })}
      </Card>

      {metricas.length > 0 && (
        <Card className="p-3">
          <h2 className="text-body-sm font-semibold mb-2">¿Cuánto tarda cada proveedor?</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-body-sm">
              <thead>
                <tr className="text-left text-caption text-muted-foreground">
                  <th className="py-1 pr-2 font-normal">Proveedor</th>
                  <th className="py-1 pr-2 font-normal">Tarda en promedio</th>
                  <th className="py-1 pr-2 font-normal">Última entrega</th>
                  <th className="py-1 font-normal">En camino</th>
                </tr>
              </thead>
              <tbody>
                {metricas.map(m => (
                  <tr key={m.proveedor_id} className="border-t border-border-subtle">
                    <td className="py-1.5 pr-2">{m.proveedor_nombre}</td>
                    <td className="py-1.5 pr-2 tabular-nums">{horasATexto(m.lead_time_promedio_horas)}</td>
                    <td className="py-1.5 pr-2">{fechaCorta(m.ultima_entrega)}</td>
                    <td className="py-1.5 tabular-nums">{m.pedidos_en_camino || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <ModalCrearPedido abierto={crearAbierto} onCerrar={() => setCrearAbierto(false)} onCreado={refrescar} />
      {recibiendo && (
        <ModalRecibir pedido={recibiendo} onCerrar={() => setRecibiendo(null)} onRecibido={refrescar} />
      )}
    </div>
  )
}
