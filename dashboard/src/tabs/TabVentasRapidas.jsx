/*
 * TabVentasRapidas — POS server-authoritative (E6, recableado + rediseño F5).
 *
 * BUG que resuelve: el total ya NO se calcula en el cliente con precio_normal. Cada línea de catálogo
 * consulta GET /productos/{id}/precio?cantidad= (debounce 250ms + AbortController) y el total es la
 * SUMA de los totales del servidor — así el motor de precios (escalonado por umbral, fracción, granel)
 * manda, y lo que ve el cajero == lo que cobra el backend. Al registrar NO se manda precio_unitario
 * (el backend recalcula), salvo que el cajero elija el precio "especial" (override explícito).
 *
 * Extras: grilla de productos frecuentes (GET /productos/frecuentes) cuando el buscador está vacío;
 * búsqueda con debounce + navegación ↑/↓ + Enter; código/categoría en los resultados; hint de umbral
 * mayorista; botones de fracción (¼ ½ ¾) para productos fraccionables; recibido/cambio en efectivo.
 *
 * Atajos POS (ADR 0029): F2 o «/» enfocan el buscador; ↑/↓ mueven la selección y Enter la agrega;
 * F9 o Ctrl+Enter cobran; Alt+1..4 método de pago; lector de código de barras (ráfaga + Enter).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Plus, Search, Trash2, X, Zap } from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { cop, ProductThumb } from '@/components/shared.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { usePreferencias } from '@/lib/preferencias.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select.jsx'

const METODOS = ['efectivo', 'transferencia', 'datafono', 'fiado']
const FRACCIONES = [['¼', 0.25], ['½', 0.5], ['¾', 0.75], ['1', 1]]
const GRANEL = { grm: 'g', gramos: 'g', cms: 'cm' }   // sub-unidades de venta a granel

function nuevaKey() {
  return (crypto?.randomUUID?.() || `k-${Date.now()}-${Math.random()}`)
}

export default function TabVentasRapidas() {
  const features = useFeatures()
  const { facturarEnVenta } = usePreferencias()
  const puedePos = features.includes('pos_electronico')
  const puedeFe = features.includes('facturacion_electronica')
  const mostrarDocumento = puedePos || puedeFe
  // Opciones del selector de documento. Con la auto-facturación apagada (`facturarEnVenta=false`) se
  // ofrece "Sin factura" (venta interna): es el default y NO manda `documento` → el backend no emite.
  // POS/Factura quedan como opt-in por venta (intención explícita, que el backend respeta igual).
  const opcionesDocumento = [
    ...(facturarEnVenta ? [] : [{ v: 'ninguno', label: 'Sin factura' }]),
    ...(puedePos ? [{ v: 'pos', label: 'POS' }] : []),
    ...(puedeFe ? [{ v: 'fe', label: 'Factura' }] : []),
  ]
  const documentoDefault = facturarEnVenta ? (puedePos ? 'pos' : 'fe') : 'ninguno'

  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])
  const [sel, setSel] = useState(0)          // índice seleccionado en los resultados (teclado)
  const [frecuentes, setFrecuentes] = useState([])
  const [carrito, setCarrito] = useState([])
  const [precios, setPrecios] = useState({})  // key → {total, precio_unitario, regla, loading}
  const [metodoPago, setMetodoPago] = useState('efectivo')
  const [recibido, setRecibido] = useState('')
  const [cliente, setCliente] = useState(null)
  const [documento, setDocumento] = useState(documentoDefault)
  const [enviando, setEnviando] = useState(false)

  const searchRef = useRef(null)
  const resultadosRef = useRef(resultados)
  const selRef = useRef(sel)
  const registrarRef = useRef(null)
  const bufferRef = useRef('')
  const bufferTimerRef = useRef(null)
  resultadosRef.current = resultados
  selRef.current = sel

  // Grilla de frecuentes (una vez): acceso rápido para el mostrador.
  useEffect(() => {
    apiJson('/productos/frecuentes?dias=30&limite=12')
      .then(d => setFrecuentes(Array.isArray(d) ? d : []))
      .catch(() => setFrecuentes([]))
  }, [])

  // Búsqueda con debounce (200ms) + AbortController (descarta respuestas viejas de verdad).
  useEffect(() => {
    const term = q.trim()
    if (!term) { setResultados([]); setSel(0); return undefined }
    const ctrl = new AbortController()
    const t = setTimeout(() => {
      apiJson(`/productos?q=${encodeURIComponent(term)}&limite=20`, { signal: ctrl.signal })
        .then(d => { setResultados(Array.isArray(d) ? d : []); setSel(0) })
        .catch(err => { if (err?.name !== 'AbortError') { setResultados([]); setSel(0) } })
    }, 200)
    return () => { clearTimeout(t); ctrl.abort() }
  }, [q])

  // Precio server-authoritative por línea de catálogo (debounce 250ms + abort). Se dispara cuando
  // cambian las cantidades. Las líneas "especial" y "varia" NO consultan (su precio es explícito).
  const firmaPrecios = carrito
    .filter(it => !it.varia && !it.usarEspecial)
    .map(it => `${it.key}:${it.producto_id}:${it.cantidad}`).join('|')
  useEffect(() => {
    const lineas = carrito.filter(it => !it.varia && !it.usarEspecial && Number(it.cantidad) > 0)
    if (lineas.length === 0) return undefined
    const ctrl = new AbortController()
    const t = setTimeout(async () => {
      for (const it of lineas) {
        setPrecios(p => ({ ...p, [it.key]: { ...(p[it.key] || {}), loading: true } }))
        try {
          const d = await apiJson(
            `/productos/${it.producto_id}/precio?cantidad=${encodeURIComponent(it.cantidad)}`,
            { signal: ctrl.signal })
          setPrecios(p => ({ ...p, [it.key]: {
            total: Number(d.total), precio_unitario: Number(d.precio_unitario),
            regla: d.regla, loading: false } }))
        } catch (err) {
          if (err?.name === 'AbortError') return
          setPrecios(p => ({ ...p, [it.key]: { ...(p[it.key] || {}), loading: false, error: true } }))
        }
      }
    }, 250)
    return () => { clearTimeout(t); ctrl.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [firmaPrecios])

  // Total de una línea (fuente de verdad por tipo): varia/especial = cliente; catálogo = servidor.
  const totalLinea = useCallback((it) => {
    const cant = Number(it.cantidad) || 0
    if (it.varia) return (Number(it.precio_unitario) || 0) * cant
    if (it.usarEspecial && it.precio_especial != null) return Number(it.precio_especial) * cant
    const srv = precios[it.key]
    if (srv && srv.total != null && !srv.error) return srv.total
    return Number(it.precio_normal || 0) * cant   // provisional mientras carga el precio del servidor
  }, [precios])

  const total = useMemo(
    () => carrito.reduce((a, it) => a + totalLinea(it), 0), [carrito, totalLinea])
  const cambio = metodoPago === 'efectivo' && recibido !== ''
    ? Math.max(0, Number(recibido) - total) : null

  async function agregarPorCodigo(codigo) {
    try {
      const d = await apiJson(`/productos?q=${encodeURIComponent(codigo)}&limite=5`)
      const lista = Array.isArray(d) ? d : []
      if (lista.length === 0) { toast.error(`Sin producto para «${codigo}»`); return }
      agregarProducto(lista.find(p => String(p.codigo ?? '') === codigo) || lista[0])
    } catch { toast.error('Error al buscar el código') }
  }

  useEffect(() => {
    const BARCODE_MIN = 4, IDLE_MS = 30
    const esEditable = (el) => el?.tagName &&
      (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable)
    const limpiar = () => {
      bufferRef.current = ''
      if (bufferTimerRef.current) { clearTimeout(bufferTimerRef.current); bufferTimerRef.current = null }
    }
    function onKeyDown(e) {
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        bufferRef.current += e.key
        if (bufferTimerRef.current) clearTimeout(bufferTimerRef.current)
        bufferTimerRef.current = setTimeout(() => { bufferRef.current = '' }, IDLE_MS)
      }
      if (e.key === 'F2' || (e.key === '/' && !esEditable(e.target))) {
        e.preventDefault(); searchRef.current?.focus(); searchRef.current?.select?.(); return
      }
      if (e.key === 'F9' || (e.key === 'Enter' && (e.ctrlKey || e.metaKey))) {
        e.preventDefault(); registrarRef.current?.(); return
      }
      if (e.altKey && /^[1-9]$/.test(e.key)) {
        const idx = Number(e.key) - 1
        if (idx < METODOS.length) { e.preventDefault(); setMetodoPago(METODOS[idx]) }
        return
      }
      // Navegación de resultados con ↑/↓ cuando el foco está en el buscador.
      if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') &&
          document.activeElement === searchRef.current && resultadosRef.current.length > 0) {
        e.preventDefault()
        const n = resultadosRef.current.length
        setSel(s => e.key === 'ArrowDown' ? (s + 1) % n : (s - 1 + n) % n)
        return
      }
      if (e.key === 'Enter') {
        const scan = bufferRef.current; limpiar()
        if (scan.length >= BARCODE_MIN) { e.preventDefault(); agregarPorCodigo(scan); return }
        if (document.activeElement === searchRef.current && resultadosRef.current.length > 0) {
          e.preventDefault()
          agregarProducto(resultadosRef.current[selRef.current] || resultadosRef.current[0])
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
        copia[i] = { ...copia[i], cantidad: Number(copia[i].cantidad) + 1 }
        return copia
      }
      return [...prev, {
        key: nuevaKey(), producto_id: p.id, nombre: p.nombre, cantidad: 1, varia: false,
        precio_normal: Number(p.precio_venta),
        precio_especial: p.precio_especial != null ? Number(p.precio_especial) : null,
        usarEspecial: false,
        codigo: p.codigo, categoria: p.categoria,
        unidad_medida: p.unidad_medida, permite_fraccion: p.permite_fraccion,
        precio_umbral: p.precio_umbral != null ? Number(p.precio_umbral) : null,
        precio_sobre_umbral: p.precio_sobre_umbral != null ? Number(p.precio_sobre_umbral) : null,
      }]
    })
    setQ(''); setResultados([]); setSel(0)
  }

  function agregarVaria({ descripcion, cantidad, precio_unitario }) {
    setCarrito(prev => [...prev, {
      key: nuevaKey(), producto_id: null, nombre: descripcion, cantidad, precio_unitario, varia: true,
    }])
  }
  const setCantidad = (key, cantidad) =>
    setCarrito(prev => prev.map(it => it.key === key ? { ...it, cantidad } : it))
  const setUsarEspecial = (key, usarEspecial) =>
    setCarrito(prev => prev.map(it => it.key === key ? { ...it, usarEspecial } : it))
  const quitar = (key) => setCarrito(prev => prev.filter(it => it.key !== key))

  async function registrar() {
    if (carrito.length === 0) return
    const lineas = carrito.map(it => {
      if (it.varia) return {
        descripcion: it.nombre, cantidad: Number(it.cantidad), precio_unitario: Number(it.precio_unitario),
      }
      const linea = { producto_id: it.producto_id, cantidad: Number(it.cantidad) }
      // Override explícito solo si eligió "especial"; si no, el backend recalcula con el motor.
      if (it.usarEspecial && it.precio_especial != null) linea.precio_unitario = Number(it.precio_especial)
      return linea
    })
    const payload = { metodo_pago: metodoPago, origen: 'web', lineas }
    if (cliente?.id) payload.cliente_id = cliente.id
    // "ninguno" → no se manda `documento`: el backend cae a su default (con facturar_en_venta=false, sin
    // emisión). POS/FE explícito sí viaja como intención (el backend lo respeta aunque el toggle esté off).
    if (mostrarDocumento && documento !== 'ninguno') payload.documento = documento

    setEnviando(true)
    try {
      const res = await api('/ventas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': nuevaKey() },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        setCarrito([]); setPrecios({}); setCliente(null); setMetodoPago('efectivo')
        setRecibido(''); setDocumento(documentoDefault)
        toast.success('Venta registrada')
      } else {
        const err = await res.json().catch(() => ({}))
        toast.error(err?.detail || 'No se pudo registrar la venta')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }
  registrarRef.current = registrar

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      <div className="lg:col-span-2 space-y-3">
        <Card className="p-3">
          <div className="relative">
            <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Buscar producto…" aria-label="Buscar producto" className="pl-9" />
          </div>
          {resultados.length > 0 && (
            <ul className="mt-2 divide-y divide-border-subtle max-h-80 overflow-y-auto scrollbar-aurora"
              role="listbox" aria-label="Resultados">
              {resultados.map((p, i) => (
                <li key={p.id} role="option" aria-selected={i === sel}>
                  <button onClick={() => agregarProducto(p)} onMouseEnter={() => setSel(i)}
                    className={`w-full flex items-center gap-2.5 py-2 px-1.5 text-left rounded-md ${
                      i === sel ? 'bg-primary/10' : 'hover:bg-surface-2'}`}>
                    <ProductThumb nombre={p.nombre} className="size-9 shrink-0 rounded-md" />
                    <span className="min-w-0 flex-1">
                      <span className="block text-body-sm truncate">{p.nombre}</span>
                      <span className="block text-caption text-muted-foreground truncate">
                        {[p.codigo, p.categoria].filter(Boolean).join(' · ') || 'Sin código'}
                      </span>
                    </span>
                    {p.precio_umbral != null && p.precio_sobre_umbral != null && (
                      <span className="text-caption text-info shrink-0 hidden sm:block">
                        ≥{cop(p.precio_umbral)} u: {cop(p.precio_sobre_umbral)}
                      </span>
                    )}
                    <span className="text-body-sm tabular text-muted-foreground shrink-0">{cop(Number(p.precio_venta))}</span>
                    <Plus className="size-4 text-primary shrink-0" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {!q.trim() && frecuentes.length > 0 && (
            <div className="mt-3">
              <div className="flex items-center gap-1.5 text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                <Zap className="size-3.5" /> Frecuentes
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                {frecuentes.map(p => (
                  <button key={p.id} onClick={() => agregarProducto(p)}
                    className="flex flex-col items-start gap-0.5 p-2 rounded-md border border-border bg-surface hover:bg-surface-2 text-left">
                    <span className="text-caption font-medium leading-tight line-clamp-2">{p.nombre}</span>
                    <span className="text-caption tabular text-muted-foreground">{cop(Number(p.precio_venta))}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </Card>

        <AtajosHint />
        <VariaForm onAdd={agregarVaria} />
      </div>

      <Card className="p-3.5 flex flex-col">
        <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-2.5">Carrito</h2>
        {carrito.length === 0 ? (
          <div className="py-10 text-center">
            <Search className="size-6 mx-auto text-muted-foreground/40 mb-2" />
            <p className="text-body-sm text-muted-foreground">Busca o escanea un producto para empezar.</p>
          </div>
        ) : (
          <ul className="divide-y divide-border-subtle mb-3">
            {carrito.map(it => (
              <LineaCarrito key={it.key} it={it} precio={precios[it.key]}
                onCantidad={(v) => setCantidad(it.key, v)} onQuitar={() => quitar(it.key)}
                onEspecial={(v) => setUsarEspecial(it.key, v)} />
            ))}
          </ul>
        )}

        <ClientePicker cliente={cliente} onSelect={setCliente} />
        {mostrarDocumento && documento === 'fe' && (
          <p className="mt-1.5 text-caption text-muted-foreground">
            Con cliente → factura a su nombre; sin cliente → consumidor final.
          </p>
        )}

        <label className="text-caption uppercase tracking-wider text-muted-foreground mt-3 mb-1">Método de pago</label>
        <Select value={metodoPago} onValueChange={setMetodoPago}>
          <SelectTrigger aria-label="Método de pago" className="capitalize"><SelectValue /></SelectTrigger>
          <SelectContent>
            {METODOS.map(m => <SelectItem key={m} value={m} className="capitalize">{m}</SelectItem>)}
          </SelectContent>
        </Select>

        {metodoPago === 'efectivo' && (
          <div className="mt-2 flex items-center gap-2">
            <Input type="number" min="0" step="any" value={recibido} onChange={(e) => setRecibido(e.target.value)}
              placeholder="Recibido" aria-label="Efectivo recibido" className="h-9 flex-1" />
            {cambio != null && (
              <span className="text-body-sm tabular shrink-0">
                Cambio <span className="font-semibold text-success">{cop(cambio)}</span>
              </span>
            )}
          </div>
        )}

        {mostrarDocumento && (
          <>
            <label className="text-caption uppercase tracking-wider text-muted-foreground mt-3 mb-1">Documento</label>
            {opcionesDocumento.length > 1 ? (
              <div className="flex items-center gap-1 flex-wrap" role="group" aria-label="Documento fiscal">
                {opcionesDocumento.map(({ v, label }) => (
                  <Seg key={v} activo={documento === v} onClick={() => setDocumento(v)}
                    aria-label={`Documento ${label}`}>{label}</Seg>
                ))}
              </div>
            ) : (
              <div className="text-body-sm text-muted-foreground">{opcionesDocumento[0]?.label}</div>
            )}
          </>
        )}

        <div className="flex items-center justify-between mt-3 mb-2">
          <span className="text-caption uppercase tracking-wider text-muted-foreground">Total</span>
          <span className="text-xl font-semibold tabular">{cop(total)}</span>
        </div>
        <Button onClick={registrar} disabled={enviando || carrito.length === 0} className="w-full h-10">
          {enviando ? 'Registrando…' : 'Registrar venta'} <span className="ml-1.5 opacity-70 text-caption">F9</span>
        </Button>
      </Card>
    </div>
  )
}

// --- Línea del carrito ------------------------------------------------------
function LineaCarrito({ it, precio, onCantidad, onQuitar, onEspecial }) {
  const granel = !it.varia && GRANEL[(it.unidad_medida || '').toLowerCase()]
  const usaServidor = !it.varia && !it.usarEspecial
  const cargando = usaServidor && precio?.loading
  const unit = it.varia ? Number(it.precio_unitario)
    : it.usarEspecial ? Number(it.precio_especial)
    : precio?.precio_unitario
  const faltanMayorista = it.precio_umbral != null && !it.usarEspecial &&
    Number(it.cantidad) > 0 && Number(it.cantidad) < it.precio_umbral

  return (
    <li className="py-2">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-body-sm truncate">{it.nombre}</div>
          <div className="text-caption text-muted-foreground tabular flex items-center gap-1.5">
            {cargando ? 'calculando…' : unit != null ? `${cop(unit)} c/u` : '—'}
            {precio?.regla && precio.regla !== 'simple' && !it.usarEspecial && (
              <span className="rounded bg-info/15 text-info px-1 text-[10px] uppercase">{precio.regla}</span>
            )}
            {granel && <span className="text-[10px] uppercase">/{granel}</span>}
          </div>
        </div>
        <Input type="number" min="0" step="any" value={it.cantidad}
          onChange={(e) => onCantidad(e.target.value)}
          aria-label={`Cantidad de ${it.nombre}`} className="w-16 h-8 text-center" />
        <span className="w-20 text-right text-body-sm tabular shrink-0">
          {cop(it.varia ? Number(it.precio_unitario) * Number(it.cantidad || 0)
            : it.usarEspecial ? Number(it.precio_especial) * Number(it.cantidad || 0)
            : (precio?.total ?? Number(it.precio_normal || 0) * Number(it.cantidad || 0)))}
        </span>
        <button onClick={onQuitar} aria-label={`Quitar ${it.nombre}`}
          className="size-8 grid place-items-center rounded-md text-muted-foreground hover:text-destructive">
          <Trash2 className="size-4" />
        </button>
      </div>

      {it.permite_fraccion && !it.usarEspecial && (
        <div className="mt-1.5 flex items-center gap-1" role="group" aria-label={`Fracción de ${it.nombre}`}>
          {FRACCIONES.map(([et, val]) => (
            <Seg key={et} activo={Number(it.cantidad) === val} onClick={() => onCantidad(String(val))}
              aria-label={`${et} de ${it.nombre}`}>{et}</Seg>
          ))}
        </div>
      )}
      {faltanMayorista && (
        <p className="mt-1 text-caption text-info">
          ≥ {it.precio_umbral} u: {cop(it.precio_sobre_umbral)} c/u — te faltan {it.precio_umbral - Number(it.cantidad)} para mayorista
        </p>
      )}
      {!it.varia && it.precio_especial != null && (
        <div className="mt-1.5 flex items-center gap-1" role="group" aria-label={`Precio de ${it.nombre}`}>
          <Seg activo={!it.usarEspecial} onClick={() => onEspecial(false)}>Normal</Seg>
          <Seg activo={it.usarEspecial} onClick={() => onEspecial(true)}>Especial {cop(it.precio_especial)}</Seg>
        </div>
      )}
    </li>
  )
}

function Seg({ activo, onClick, children, ...props }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={activo} {...props}
      className={`flex-1 h-7 px-2 rounded-md border text-caption tabular transition-colors ${
        activo ? 'border-primary bg-primary/10 text-primary font-medium'
          : 'border-border bg-surface text-muted-foreground hover:bg-surface-2'}`}>
      {children}
    </button>
  )
}

function Kbd({ children }) {
  return (
    <kbd className="inline-flex items-center rounded border border-border bg-surface-2 px-1 text-[10px] font-medium text-muted-foreground">
      {children}
    </kbd>
  )
}

function AtajosHint() {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-caption text-muted-foreground">
      <span><Kbd>F2</Kbd> o <Kbd>/</Kbd> buscar</span>
      <span><Kbd>↑</Kbd><Kbd>↓</Kbd> elegir</span>
      <span><Kbd>Enter</Kbd> agrega</span>
      <span><Kbd>F9</Kbd> cobrar</span>
      <span><Kbd>Alt</Kbd>+<Kbd>1</Kbd>–<Kbd>4</Kbd> pago</span>
      <span>o escanea un código</span>
    </p>
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
      <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground mb-2">Venta varia (sin catálogo)</h2>
      <div className="flex flex-wrap items-center gap-2">
        <Input value={descripcion} onChange={(e) => setDescripcion(e.target.value)}
          placeholder="Descripción" aria-label="Descripción varia" className="flex-1 min-w-[140px] h-9" />
        <Input type="number" min="0" step="any" value={cantidad} onChange={(e) => setCantidad(e.target.value)}
          aria-label="Cantidad varia" className="w-20 h-9 text-center" />
        <Input type="number" min="0" step="any" value={precio} onChange={(e) => setPrecio(e.target.value)}
          placeholder="Precio" aria-label="Precio varia" className="w-28 h-9" />
        <Button variant="outline" onClick={agregar} className="h-9">Agregar</Button>
      </div>
    </Card>
  )
}

function ClientePicker({ cliente, onSelect }) {
  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])
  useEffect(() => {
    const term = q.trim()
    if (!term) { setResultados([]); return undefined }
    const ctrl = new AbortController()
    const t = setTimeout(() => {
      apiJson(`/clientes?q=${encodeURIComponent(term)}`, { signal: ctrl.signal })
        .then(d => setResultados(Array.isArray(d) ? d : []))
        .catch(err => { if (err?.name !== 'AbortError') setResultados([]) })
    }, 200)
    return () => { clearTimeout(t); ctrl.abort() }
  }, [q])

  if (cliente) {
    return (
      <div className="flex items-center gap-2 mt-1 text-body-sm">
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
        placeholder="Cliente (opcional)…" aria-label="Buscar cliente" className="h-8 text-body-sm" />
      {resultados.length > 0 && (
        <ul className="mt-1 divide-y divide-border-subtle max-h-40 overflow-y-auto scrollbar-aurora">
          {resultados.map(c => (
            <li key={c.id}>
              <button onClick={() => { onSelect(c); setQ(''); setResultados([]) }}
                className="w-full text-left py-1.5 px-1 text-body-sm hover:bg-surface-2 rounded-md truncate">
                {c.nombre}{c.documento ? ` · ${c.documento}` : ''}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
