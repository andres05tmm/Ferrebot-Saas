/*
 * TabVentasRapidas — POS server-authoritative (E6, F5 pago mixto, reforma grilla híbrida).
 *
 * Orquestador del POS: es dueño del carrito, los precios del servidor, los atajos de teclado, el guard
 * de caja y el POST /ventas. La presentación vive en tabs/pos/ (LineaCarrito, Checkout, ClientePicker,
 * VariaForm, piezas).
 *
 * Precio server-authoritative: cada línea de catálogo consulta GET /productos/{id}/precio?cantidad=
 * (debounce 250ms + AbortController) y el total es la SUMA de los totales del servidor — el motor de
 * precios (escalonado, fracción, granel) manda y lo que ve el cajero == lo que cobra el backend. Al
 * registrar NO se manda precio_unitario (el backend recalcula), salvo el "especial" (override explícito).
 *
 * Atajos POS (ADR 0029): F2 o «/» enfocan el buscador; ↑/↓ mueven la selección y Enter la agrega;
 * F9 o Ctrl+Enter cobran; Alt+1..5 método de pago; lector de código de barras (ráfaga + Enter).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Plus, Search, X, Zap } from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { cop, ProductThumb } from '@/components/shared.jsx'
import ModalAbrirCaja from '@/components/ModalAbrirCaja.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { usePreferencias } from '@/lib/preferencias.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { AtajosHint, guardarLS, leerLS, METODOS, nuevaKey } from './pos/piezas.jsx'
import LineaCarrito from './pos/LineaCarrito.jsx'
import Checkout from './pos/Checkout.jsx'
import ClientePicker from './pos/ClientePicker.jsx'
import VariaForm from './pos/VariaForm.jsx'

// Carrito persistente + ventas en espera (F5, patrón CART_KEY del FerreBot viejo): el carrito vivo
// sobrevive un refresh y los carritos aparcados esperan su turno, todo client-side (localStorage).
const CART_KEY = 'pos_carrito_v1'
const ESPERA_KEY = 'pos_espera_v1'

export default function TabVentasRapidas() {
  const features = useFeatures()
  const { facturarEnVenta, cajaObligatoria } = usePreferencias()
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
  const [carrito, setCarrito] = useState(() => leerLS(CART_KEY, []))
  const [enEspera, setEnEspera] = useState(() => leerLS(ESPERA_KEY, []))
  const [precios, setPrecios] = useState({})  // key → {total, precio_unitario, regla, loading}
  const [metodoPago, setMetodoPago] = useState('efectivo')
  const [recibido, setRecibido] = useState('')
  // Cobro dividido (mixto): cuánto entra en efectivo; el resto va al segundo método.
  const [efectivoMixto, setEfectivoMixto] = useState('')
  const [metodoResto, setMetodoResto] = useState('transferencia')
  const [cliente, setCliente] = useState(null)
  const [documento, setDocumento] = useState(documentoDefault)
  const [enviando, setEnviando] = useState(false)
  // Guard de caja (`caja_obligatoria`): la venta que quedó esperando a que se abra la caja.
  // Guarda el payload Y su Idempotency-Key ya generada: al abrir caja se reintenta EXACTAMENTE
  // el mismo cobro (sin repetir la venta ni arriesgar un duplicado).
  const [ventaPendiente, setVentaPendiente] = useState(null)   // {payload, key} | null

  const searchRef = useRef(null)
  const resultadosRef = useRef(resultados)
  const selRef = useRef(sel)
  const registrarRef = useRef(null)
  const bufferRef = useRef('')
  const bufferTimerRef = useRef(null)
  resultadosRef.current = resultados
  selRef.current = sel

  // Persistencia del carrito vivo y de los aparcados (sobreviven refresh/corte de luz).
  useEffect(() => { guardarLS(CART_KEY, carrito) }, [carrito])
  useEffect(() => { guardarLS(ESPERA_KEY, enEspera) }, [enEspera])

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
  // Cobro dividido: el resto sale solo (total − efectivo), redondeado a centavos para que la suma
  // cuadre EXACTA con el total (el backend rechaza con 422 si no).
  const restanteMixto = metodoPago === 'mixto'
    ? Math.max(0, Math.round((total - (Number(efectivoMixto) || 0)) * 100) / 100) : null
  const mixtoValido = metodoPago === 'mixto' &&
    Number(efectivoMixto) > 0 && Number(efectivoMixto) < total

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
  // Ventas en espera: aparca el carrito actual (con su cliente) y deja el mostrador libre para
  // atender al siguiente; "Retomar" lo trae de vuelta (si había uno vivo, se aparca primero).
  function ponerEnEspera() {
    if (carrito.length === 0) return
    setEnEspera(prev => [...prev, { id: nuevaKey(), ts: Date.now(), carrito, cliente }])
    setCarrito([]); setPrecios({}); setCliente(null); setRecibido(''); setEfectivoMixto('')
  }

  function retomarEspera(id) {
    const aparcado = enEspera.find(e => e.id === id)
    if (!aparcado) return
    setEnEspera(prev => {
      const sinEste = prev.filter(e => e.id !== id)
      return carrito.length > 0
        ? [...sinEste, { id: nuevaKey(), ts: Date.now(), carrito, cliente }]
        : sinEste
    })
    setCarrito(aparcado.carrito); setCliente(aparcado.cliente || null)
    setPrecios({}); setRecibido(''); setEfectivoMixto('')
  }

  const quitarEspera = (id) => setEnEspera(prev => prev.filter(e => e.id !== id))

  const setCantidad = (key, cantidad) =>
    setCarrito(prev => prev.map(it => it.key === key ? { ...it, cantidad } : it))
  const setUsarEspecial = (key, usarEspecial) =>
    setCarrito(prev => prev.map(it => it.key === key ? { ...it, usarEspecial } : it))
  const quitar = (key) => setCarrito(prev => prev.filter(it => it.key !== key))

  // POST /ventas con una key concreta. Si el backend responde 409 `caja_no_abierta` (guard de caja),
  // deja la venta pendiente y abre el modal de apertura: el MISMO payload + key se reintenta al abrir.
  const enviarVenta = useCallback(async (payload, key) => {
    setEnviando(true)
    try {
      const res = await api('/ventas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': key },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        setVentaPendiente(null)
        setCarrito([]); setPrecios({}); setCliente(null); setMetodoPago('efectivo')
        setRecibido(''); setEfectivoMixto(''); setMetodoResto('transferencia')
        setDocumento(documentoDefault)
        toast.success('Venta registrada')
        return true
      }
      const err = await res.json().catch(() => ({}))
      if (res.status === 409 && err?.detail?.code === 'caja_no_abierta') {
        setVentaPendiente({ payload, key })   // el modal toma el relevo; carrito intacto
        return false
      }
      const detalle = err?.detail
      toast.error(
        (typeof detalle === 'string' && detalle) || detalle?.mensaje || 'No se pudo registrar la venta',
      )
      return false
    } catch {
      toast.error('Error de conexión')
      return false
    } finally { setEnviando(false) }
  }, [documentoDefault])

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
    if (metodoPago === 'mixto') {
      if (!mixtoValido) {
        toast.error('El efectivo del cobro mixto debe ser mayor que 0 y menor que el total')
        return
      }
      payload.pagos = [
        { metodo: 'efectivo', monto: Number(efectivoMixto) },
        { metodo: metodoResto, monto: restanteMixto },
      ]
    }
    if (cliente?.id) payload.cliente_id = cliente.id
    // "ninguno" → no se manda `documento`: el backend cae a su default (con facturar_en_venta=false, sin
    // emisión). POS/FE explícito sí viaja como intención (el backend lo respeta aunque el toggle esté off).
    if (mostrarDocumento && documento !== 'ninguno') payload.documento = documento

    const key = nuevaKey()
    // Guard de caja proactivo (`caja_obligatoria`): antes del primer cobro del día, si no hay caja
    // abierta se abre el modal SIN intentar el POST. Un fallo del check no bloquea (el 409 del
    // backend es la fuente de verdad y también dispara el modal).
    if (cajaObligatoria) {
      try {
        const estado = await apiJson('/caja/estado')
        if (!estado?.abierta) { setVentaPendiente({ payload, key }); return }
      } catch { /* el backend decide */ }
    }
    await enviarVenta(payload, key)
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
        <div className="flex items-center justify-between mb-2.5">
          <h2 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground">Carrito</h2>
          <Button variant="outline" size="sm" onClick={ponerEnEspera} disabled={carrito.length === 0}
            className="h-7 text-caption">En espera</Button>
        </div>
        {enEspera.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-2.5" role="group" aria-label="Ventas en espera">
            {enEspera.map((e, i) => (
              <span key={e.id} className="inline-flex items-center gap-1 rounded-md border border-border bg-surface-2 pl-2 pr-1 h-7 text-caption">
                <button onClick={() => retomarEspera(e.id)} className="hover:text-primary"
                  aria-label={`Retomar venta en espera ${i + 1}`}>
                  #{i + 1} · {e.carrito.length} ítem{e.carrito.length === 1 ? '' : 's'}
                  {e.cliente?.nombre ? ` · ${e.cliente.nombre}` : ''}
                </button>
                <button onClick={() => quitarEspera(e.id)} aria-label={`Descartar venta en espera ${i + 1}`}
                  className="size-5 grid place-items-center rounded text-muted-foreground hover:text-destructive">
                  <X className="size-3" />
                </button>
              </span>
            ))}
          </div>
        )}
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

        <Checkout
          metodoPago={metodoPago} setMetodoPago={setMetodoPago}
          recibido={recibido} setRecibido={setRecibido} cambio={cambio}
          efectivoMixto={efectivoMixto} setEfectivoMixto={setEfectivoMixto}
          metodoResto={metodoResto} setMetodoResto={setMetodoResto}
          restanteMixto={restanteMixto} mixtoValido={mixtoValido}
          mostrarDocumento={mostrarDocumento} opcionesDocumento={opcionesDocumento}
          documento={documento} setDocumento={setDocumento}
          total={total} enviando={enviando} carritoVacio={carrito.length === 0}
          onRegistrar={registrar}
        />
      </Card>

      {/* Guard de caja: abre la caja y registra la venta pendiente con su MISMA key (nada se repite). */}
      <ModalAbrirCaja
        abierto={ventaPendiente != null}
        onCancelar={() => setVentaPendiente(null)}
        onCajaAbierta={async () => {
          if (ventaPendiente) await enviarVenta(ventaPendiente.payload, ventaPendiente.key)
        }}
      />
    </div>
  )
}
