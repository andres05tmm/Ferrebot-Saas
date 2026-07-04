/*
 * TabVentasRapidas — registro de venta (E6, recableado a endpoints SaaS).
 * Búsqueda: GET /productos?q. Cliente (opcional): GET /clientes?q. Registrar: POST /ventas
 * (VentaCrear) con header Idempotency-Key (UUID por envío). Soporta venta varia (línea sin
 * producto_id → exige descripcion + precio_unitario). Éxito → limpia el carrito + toast; la SSE
 * 'venta_registrada' refresca Hoy/Historial. Diferido: productos frecuentes / top (sin endpoint).
 *
 * Atajos de teclado POS (ADR 0029): F2 o «/» enfocan el buscador; Enter en el buscador agrega el
 * primer resultado; F9 o Ctrl+Enter cobran; Alt+1..4 elige método de pago; y un lector de código de
 * barras (ráfaga de teclas terminada en Enter) busca y agrega directo. Se eligieron teclas que no
 * chocan con atajos del navegador.
 */
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Plus, Search, Trash2, X } from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { cop } from '@/components/shared.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

const METODOS = ['efectivo', 'transferencia', 'datafono', 'fiado']

function nuevaKey() {
  return (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`)
}

// Precio efectivo de una línea del carrito: el de la varia, o normal/especial según el toggle.
function precioEfectivo(it) {
  if (it.varia) return Number(it.precio_unitario) || 0
  if (it.usarEspecial && it.precio_especial != null) return Number(it.precio_especial)
  return Number(it.precio_normal) || 0
}

export default function TabVentasRapidas() {
  const features = useFeatures()
  // Documento fiscal por venta (ADR 0014): la intención la rutea el cierre fiscal en el backend.
  // Selector solo si el tenant tiene capacidad fiscal; con ambas elige, con una sola es estado fijo.
  const puedePos = features.includes('pos_electronico')
  const puedeFe = features.includes('facturacion_electronica')
  const mostrarDocumento = puedePos || puedeFe
  const documentoDefault = puedePos ? 'pos' : 'fe'   // default por capacidad: POS si hay POS, si no FE

  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])
  const [carrito, setCarrito] = useState([])
  const [metodoPago, setMetodoPago] = useState('efectivo')
  const [cliente, setCliente] = useState(null)
  const [documento, setDocumento] = useState(documentoDefault)
  const [enviando, setEnviando] = useState(false)

  // Refs para los atajos de teclado: el listener global se ata UNA vez, así que lee el estado vivo
  // por ref (resultados y la función registrar, que capturaría valores viejos si se cerrara en ella).
  const searchRef = useRef(null)
  const resultadosRef = useRef(resultados)
  const registrarRef = useRef(null)
  const bufferRef = useRef('')            // acumulador del lector de código de barras
  const bufferTimerRef = useRef(null)
  resultadosRef.current = resultados
  registrarRef.current = registrar

  // Búsqueda de producto (GET /productos?q). Sin q → sin resultados.
  useEffect(() => {
    if (!q.trim()) { setResultados([]); return undefined }
    let cancelado = false
    apiJson(`/productos?q=${encodeURIComponent(q.trim())}&limite=20`)
      .then(d => { if (!cancelado) setResultados(Array.isArray(d) ? d : []) })
      .catch(() => { if (!cancelado) setResultados([]) })
    return () => { cancelado = true }
  }, [q])

  // Lector de código de barras: busca el código y agrega el producto (match exacto por `codigo`, o el
  // primero). No pasa por el buscador visible: agrega directo, como espera un cajero escaneando.
  async function agregarPorCodigo(codigo) {
    try {
      const d = await apiJson(`/productos?q=${encodeURIComponent(codigo)}&limite=5`)
      const lista = Array.isArray(d) ? d : []
      if (lista.length === 0) { toast.error(`Sin producto para «${codigo}»`); return }
      const exacto = lista.find(p => String(p.codigo ?? '') === codigo) || lista[0]
      agregarProducto(exacto)
    } catch {
      toast.error('Error al buscar el código')
    }
  }

  // Atajos de teclado del POS (ADR 0029). Listener global atado una sola vez; el estado vivo entra
  // por refs. Teclas elegidas para no chocar con el navegador (F2/F9 = funciones; Alt+dígito; «/»
  // solo fuera de un campo). El lector de código de barras se detecta por ráfaga: teclas imprimibles
  // que llegan más rápido que IDLE_MS se acumulan; Enter con el buffer largo → es un escaneo.
  useEffect(() => {
    const BARCODE_MIN = 4     // caracteres mínimos del buffer para tratarlo como código escaneado
    const IDLE_MS = 30        // pausa que descarta el buffer (tecleo humano lo resetea; el lector no)

    const esEditable = (el) => {
      if (!el || !el.tagName) return false
      const tag = el.tagName
      return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
    }
    const limpiarBuffer = () => {
      bufferRef.current = ''
      if (bufferTimerRef.current) { clearTimeout(bufferTimerRef.current); bufferTimerRef.current = null }
    }

    function onKeyDown(e) {
      // Acumular ráfaga del lector: solo teclas imprimibles sin modificadores.
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        bufferRef.current += e.key
        if (bufferTimerRef.current) clearTimeout(bufferTimerRef.current)
        bufferTimerRef.current = setTimeout(() => { bufferRef.current = '' }, IDLE_MS)
      }

      // F2 o «/» → foco al buscador («/» solo si no estás escribiendo en un campo).
      if (e.key === 'F2' || (e.key === '/' && !esEditable(e.target))) {
        e.preventDefault()
        searchRef.current?.focus()
        searchRef.current?.select?.()
        return
      }
      // F9 o Ctrl/Cmd+Enter → cobrar.
      if (e.key === 'F9' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) {
        e.preventDefault()
        registrarRef.current?.()
        return
      }
      // Alt+1..N → método de pago.
      if (e.altKey && /^[1-9]$/.test(e.key)) {
        const idx = Number(e.key) - 1
        if (idx < METODOS.length) { e.preventDefault(); setMetodoPago(METODOS[idx]) }
        return
      }
      // Enter: ráfaga larga = escaneo (busca y agrega); si no, agrega el primer resultado del buscador.
      if (e.key === 'Enter') {
        const scan = bufferRef.current
        limpiarBuffer()
        if (scan.length >= BARCODE_MIN) {
          e.preventDefault()
          agregarPorCodigo(scan)
          return
        }
        if (document.activeElement === searchRef.current && resultadosRef.current.length > 0) {
          e.preventDefault()
          agregarProducto(resultadosRef.current[0])
        }
      }
    }

    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      if (bufferTimerRef.current) clearTimeout(bufferTimerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function agregarProducto(p) {
    setCarrito(prev => {
      const i = prev.findIndex(it => it.producto_id === p.id)
      if (i >= 0) {
        const copia = [...prev]
        copia[i] = { ...copia[i], cantidad: copia[i].cantidad + 1 }
        return copia
      }
      return [...prev, {
        key: nuevaKey(), producto_id: p.id, nombre: p.nombre, cantidad: 1, varia: false,
        precio_normal: Number(p.precio_venta),
        // Precio especial opcional del producto: habilita el selector por línea (default: normal).
        precio_especial: p.precio_especial != null ? Number(p.precio_especial) : null,
        usarEspecial: false,
      }]
    })
    setQ(''); setResultados([])
  }

  function agregarVaria({ descripcion, cantidad, precio_unitario }) {
    setCarrito(prev => [...prev, {
      key: nuevaKey(), producto_id: null, nombre: descripcion,
      cantidad, precio_unitario, varia: true,
    }])
  }

  function setCantidad(key, cantidad) {
    setCarrito(prev => prev.map(it => it.key === key ? { ...it, cantidad } : it))
  }
  function setUsarEspecial(key, usarEspecial) {
    setCarrito(prev => prev.map(it => it.key === key ? { ...it, usarEspecial } : it))
  }
  function quitar(key) {
    setCarrito(prev => prev.filter(it => it.key !== key))
  }

  const total = carrito.reduce((a, it) => a + precioEfectivo(it) * (Number(it.cantidad) || 0), 0)

  async function registrar() {
    if (carrito.length === 0) return
    const lineas = carrito.map(it => {
      if (it.varia) {
        return { descripcion: it.nombre, cantidad: Number(it.cantidad), precio_unitario: Number(it.precio_unitario) }
      }
      const linea = { producto_id: it.producto_id, cantidad: Number(it.cantidad) }
      // Precio especial elegido → override explícito por línea (gana sobre el motor de precios).
      if (it.usarEspecial && it.precio_especial != null) {
        linea.precio_unitario = Number(it.precio_especial)
      }
      return linea
    })
    const payload = { metodo_pago: metodoPago, origen: 'web', lineas }
    if (cliente?.id) payload.cliente_id = cliente.id
    // Solo si el selector está visible (hay capacidad fiscal); sin ella, el backend decide por defecto.
    if (mostrarDocumento) payload.documento = documento

    setEnviando(true)
    try {
      const res = await api('/ventas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': nuevaKey() },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        setCarrito([]); setCliente(null); setMetodoPago('efectivo'); setDocumento(documentoDefault)
        toast.success('Venta registrada')
      } else {
        toast.error('No se pudo registrar la venta')
      }
    } catch {
      toast.error('Error de conexión')
    } finally {
      setEnviando(false)
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      {/* Búsqueda + venta varia */}
      <div className="lg:col-span-2 space-y-3">
        <Card className="p-3">
          <div className="relative">
            <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar producto…" aria-label="Buscar producto" className="pl-9" />
          </div>
          {resultados.length > 0 && (
            <ul className="mt-2 divide-y divide-border-subtle max-h-72 overflow-y-auto scrollbar-aurora">
              {resultados.map(p => (
                <li key={p.id}>
                  <button onClick={() => agregarProducto(p)}
                    className="w-full flex items-center gap-2 py-2 px-1 text-left hover:bg-surface-2 rounded-md">
                    <Plus className="size-4 text-primary shrink-0" />
                    <span className="flex-1 text-[13px] truncate">{p.nombre}</span>
                    <span className="text-[12px] tabular text-muted-foreground shrink-0">{cop(Number(p.precio_venta))}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <AtajosHint />
        <VariaForm onAdd={agregarVaria} />
      </div>

      {/* Carrito */}
      <Card className="p-3.5 flex flex-col">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2.5">Carrito</h2>
        {carrito.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Agrega productos para vender.</p>
        ) : (
          <ul className="divide-y divide-border-subtle mb-3">
            {carrito.map(it => (
              <li key={it.key} className="py-2">
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] truncate">{it.nombre}</div>
                    <div className="text-[11px] text-muted-foreground tabular">{cop(precioEfectivo(it))} c/u</div>
                  </div>
                  <Input type="number" min="0" step="any" value={it.cantidad}
                    onChange={(e) => setCantidad(it.key, e.target.value)}
                    aria-label={`Cantidad de ${it.nombre}`} className="w-16 h-8 text-center" />
                  <button onClick={() => quitar(it.key)} aria-label={`Quitar ${it.nombre}`}
                    className="size-8 grid place-items-center rounded-md text-muted-foreground hover:text-destructive">
                    <Trash2 className="size-4" />
                  </button>
                </div>
                {!it.varia && it.precio_especial != null && (
                  <div className="mt-1.5 flex items-center gap-1" role="group" aria-label={`Precio de ${it.nombre}`}>
                    <PrecioOpcion activo={!it.usarEspecial} onClick={() => setUsarEspecial(it.key, false)}
                      aria-label={`Precio normal de ${it.nombre}`}>Normal {cop(it.precio_normal)}</PrecioOpcion>
                    <PrecioOpcion activo={it.usarEspecial} onClick={() => setUsarEspecial(it.key, true)}
                      aria-label={`Precio especial de ${it.nombre}`}>Especial {cop(it.precio_especial)}</PrecioOpcion>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        <ClientePicker cliente={cliente} onSelect={setCliente} />
        {mostrarDocumento && documento === 'fe' && (
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Con cliente → factura a su nombre; sin cliente → consumidor final.
          </p>
        )}

        <label className="text-[10px] uppercase tracking-wider text-muted-foreground mt-3 mb-1">Método de pago</label>
        <select value={metodoPago} onChange={(e) => setMetodoPago(e.target.value)}
          aria-label="Método de pago"
          className="text-sm h-9 px-2 rounded-md border border-border bg-surface text-foreground capitalize">
          {METODOS.map(m => <option key={m} value={m}>{m}</option>)}
        </select>

        {mostrarDocumento && (
          <>
            <label className="text-[10px] uppercase tracking-wider text-muted-foreground mt-3 mb-1">Documento</label>
            {puedePos && puedeFe ? (
              <div className="flex items-center gap-1" role="group" aria-label="Documento fiscal">
                <PrecioOpcion activo={documento === 'pos'} onClick={() => setDocumento('pos')}
                  aria-label="Documento POS">POS</PrecioOpcion>
                <PrecioOpcion activo={documento === 'fe'} onClick={() => setDocumento('fe')}
                  aria-label="Documento factura electrónica">Factura</PrecioOpcion>
              </div>
            ) : (
              <div className="text-[12px] text-muted-foreground" aria-label="Documento fiscal">
                {puedePos ? 'POS' : 'Factura electrónica'}
              </div>
            )}
          </>
        )}

        <div className="flex items-center justify-between mt-3 mb-2">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Total</span>
          <span className="text-lg font-semibold tabular">{cop(total)}</span>
        </div>
        <button onClick={registrar} disabled={enviando || carrito.length === 0}
          className="w-full h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Registrando…' : 'Registrar venta'}
        </button>
      </Card>
    </div>
  )
}

// Tecla con estilo de <kbd> para el hint de atajos.
function Kbd({ children }) {
  return (
    <kbd className="inline-flex items-center rounded border border-border bg-surface-2 px-1 text-[10px] font-medium text-muted-foreground">
      {children}
    </kbd>
  )
}

// Hint discreto de los atajos de teclado del POS (ADR 0029).
function AtajosHint() {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11px] text-muted-foreground">
      <span><Kbd>F2</Kbd> o <Kbd>/</Kbd> buscar</span>
      <span><Kbd>Enter</Kbd> agrega</span>
      <span><Kbd>F9</Kbd> cobrar</span>
      <span><Kbd>Alt</Kbd>+<Kbd>1</Kbd>–<Kbd>4</Kbd> método de pago</span>
      <span>o escanea un código de barras</span>
    </p>
  )
}

// Botón de un selector segmentado (Normal / Especial) por línea del carrito.
function PrecioOpcion({ activo, onClick, children, ...props }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={activo} {...props}
      className={`flex-1 h-7 px-2 rounded-md border text-[11px] tabular transition-colors ${
        activo
          ? 'border-primary bg-primary/10 text-primary font-medium'
          : 'border-border bg-surface text-muted-foreground hover:bg-surface-2'
      }`}>
      {children}
    </button>
  )
}

function VariaForm({ onAdd }) {
  const [descripcion, setDescripcion] = useState('')
  const [cantidad, setCantidad] = useState('1')
  const [precio, setPrecio] = useState('')

  function agregar() {
    const c = Number(cantidad), p = Number(precio)
    if (!descripcion.trim() || !c || !p) return
    onAdd({ descripcion: descripcion.trim(), cantidad: c, precio_unitario: p })
    setDescripcion(''); setCantidad('1'); setPrecio('')
  }

  return (
    <Card className="p-3">
      <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">Venta varia (sin catálogo)</h2>
      <div className="flex flex-wrap items-center gap-2">
        <Input value={descripcion} onChange={(e) => setDescripcion(e.target.value)}
          placeholder="Descripción" aria-label="Descripción varia" className="flex-1 min-w-[140px] h-9" />
        <Input type="number" min="0" step="any" value={cantidad} onChange={(e) => setCantidad(e.target.value)}
          aria-label="Cantidad varia" className="w-20 h-9 text-center" />
        <Input type="number" min="0" step="any" value={precio} onChange={(e) => setPrecio(e.target.value)}
          placeholder="Precio" aria-label="Precio varia" className="w-28 h-9" />
        <button onClick={agregar}
          className="h-9 px-3 rounded-md border border-border bg-surface text-sm hover:bg-surface-2">Agregar</button>
      </div>
    </Card>
  )
}

function ClientePicker({ cliente, onSelect }) {
  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])

  useEffect(() => {
    if (!q.trim()) { setResultados([]); return undefined }
    let cancelado = false
    apiJson(`/clientes?q=${encodeURIComponent(q.trim())}`)
      .then(d => { if (!cancelado) setResultados(Array.isArray(d) ? d : []) })
      .catch(() => { if (!cancelado) setResultados([]) })
    return () => { cancelado = true }
  }, [q])

  if (cliente) {
    return (
      <div className="flex items-center gap-2 mt-1 text-[12px]">
        <span className="text-muted-foreground">Cliente:</span>
        <span className="font-medium truncate flex-1">{cliente.nombre}</span>
        <button onClick={() => onSelect(null)} aria-label="Quitar cliente"
          className="size-6 grid place-items-center rounded-md text-muted-foreground hover:text-foreground">
          <X className="size-3.5" />
        </button>
      </div>
    )
  }

  return (
    <div className="mt-1">
      <Input value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Cliente (opcional)…" aria-label="Buscar cliente" className="h-8 text-sm" />
      {resultados.length > 0 && (
        <ul className="mt-1 divide-y divide-border-subtle max-h-40 overflow-y-auto scrollbar-aurora">
          {resultados.map(c => (
            <li key={c.id}>
              <button onClick={() => { onSelect(c); setQ(''); setResultados([]) }}
                className="w-full text-left py-1.5 px-1 text-[12px] hover:bg-surface-2 rounded-md truncate">
                {c.nombre}{c.documento ? ` · ${c.documento}` : ''}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
