import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
vi.mock('@/lib/features.jsx', () => ({ useFeatures: () => [] }))

import TabVentasRapidas from './TabVentasRapidas.jsx'
import { PreferenciasProvider } from '@/lib/preferencias.jsx'

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }
function errResp(status, detail) { return { ok: false, status, json: async () => ({ detail }) } }

const MARTILLO = { id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad' }
const TALADRO_ESP = { id: 2, nombre: 'Taladro', precio_venta: '100000', precio_especial: '90000', unidad_medida: 'unidad' }

// PrecioLeer del motor: total ≠ precio_venta*cantidad para probar que manda el servidor.
function precioResp(id, cantidad) {
  return jsonResp({ producto_id: id, cantidad, precio_unitario: '10000', total: '10000', regla: 'escalonado' })
}

function instalarFetch(busqueda = [MARTILLO]) {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/productos/frecuentes')) return Promise.resolve(jsonResp([]))
    if (/\/productos\/\d+\/precio/.test(u)) {
      const id = Number(u.match(/\/productos\/(\d+)\/precio/)[1])
      return Promise.resolve(precioResp(id, 1))
    }
    if (u.includes('/productos')) return Promise.resolve(jsonResp(busqueda))
    if (u.includes('/clientes')) return Promise.resolve(jsonResp([]))
    if (u.includes('/ventas')) return Promise.resolve(jsonResp({ id: 9, consecutivo: 1 }))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function ventaPost(fetchMock) {
  const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/ventas') && c[1]?.method === 'POST')
  expect(call).toBeTruthy()
  return { headers: call[1].headers, body: JSON.parse(call[1].body) }
}

async function agregarMartillo() {
  fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
  fireEvent.click(await screen.findByText('Martillo'))
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabVentasRapidas', () => {
  it('la búsqueda (con debounce) llama GET /productos?q', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)
    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    await screen.findByText('Martillo')
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos?q=mar'))).toBe(true)
  })

  it('el total y el c/u vienen del servidor (GET /precio), no del precio_venta', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)
    await agregarMartillo()
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(c => /\/productos\/1\/precio/.test(String(c[0])))).toBe(true))
    // total del servidor ($10.000), no el precio_venta*cantidad ($11.900). Aparece en el total y el c/u.
    expect((await screen.findAllByText('$10.000')).length).toBeGreaterThan(0)
  })

  it('registrar hace POST /ventas SIN precio_unitario (server-authoritative) + Idempotency-Key', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)
    await agregarMartillo()
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText(/Busca o escanea/)   // carrito limpio tras el éxito

    const { headers, body } = ventaPost(fetchMock)
    expect(headers.get('Idempotency-Key')).toBeTruthy()
    expect(body.lineas[0]).toEqual({ producto_id: 1, cantidad: 1 })   // sin precio_unitario
    expect(body.origen).toBe('web')
  })

  it('elegir "especial" envía precio_unitario como override explícito', async () => {
    const fetchMock = instalarFetch([TALADRO_ESP])
    render(<TabVentasRapidas />)
    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'tal' } })
    fireEvent.click(await screen.findByText('Taladro'))
    fireEvent.click(await screen.findByText(/Especial/))
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText(/Busca o escanea/)

    const { body } = ventaPost(fetchMock)
    expect(body.lineas[0].precio_unitario).toBe(90000)
  })
})

// --- Pago mixto (F5): cobro dividido con validación suma=total ----------------

describe('TabVentasRapidas — pago mixto', () => {
  it('manda pagos [efectivo + resto] que suman EXACTO el total', async () => {
    const fetchMock = instalarFetch()
    render(<TabVentasRapidas />)
    await agregarMartillo()
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(c => /\/productos\/1\/precio/.test(String(c[0])))).toBe(true))
    fireEvent.keyDown(document, { key: '5', altKey: true })   // Alt+5 → mixto
    fireEvent.change(await screen.findByLabelText('Parte en efectivo'), { target: { value: '4000' } })
    // El resto sale solo: total $10.000 − $4.000 = $6.000 por transferencia (default).
    await screen.findByText('$6.000')
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText(/Busca o escanea/)

    const { body } = ventaPost(fetchMock)
    expect(body.metodo_pago).toBe('mixto')
    expect(body.pagos).toEqual([
      { metodo: 'efectivo', monto: 4000 },
      { metodo: 'transferencia', monto: 6000 },
    ])
  })

  it('sin efectivo válido el botón queda deshabilitado (no hay POST posible)', async () => {
    instalarFetch()
    render(<TabVentasRapidas />)
    await agregarMartillo()
    fireEvent.keyDown(document, { key: '5', altKey: true })
    await screen.findByLabelText('Parte en efectivo')   // sin monto: inválido
    expect(screen.getByText(/Registrar venta/).closest('button')).toBeDisabled()
  })
})

// --- Carrito persistente + ventas en espera (F5) ------------------------------

describe('TabVentasRapidas — carrito persistente y en espera', () => {
  it('el carrito sobrevive un remount (localStorage)', async () => {
    instalarFetch()
    const { unmount } = render(<TabVentasRapidas />)
    await agregarMartillo()
    await screen.findAllByText('Martillo')
    unmount()

    instalarFetch()
    render(<TabVentasRapidas />)
    expect((await screen.findAllByText('Martillo')).length).toBeGreaterThan(0)
  })

  it('"En espera" aparca el carrito y "Retomar" lo trae de vuelta', async () => {
    instalarFetch()
    render(<TabVentasRapidas />)
    await agregarMartillo()
    fireEvent.click(screen.getByText('En espera'))
    await screen.findByText(/Busca o escanea/)          // mostrador libre
    const chip = screen.getByLabelText('Retomar venta en espera 1')
    expect(chip.textContent).toContain('1 ítem')

    fireEvent.click(chip)
    expect((await screen.findAllByText('Martillo')).length).toBeGreaterThan(0)
    expect(screen.queryByLabelText('Retomar venta en espera 1')).toBeNull()
  })
})

// --- Guard de apertura de caja (`caja_obligatoria`) --------------------------

function renderConGuard() {
  return render(
    <PreferenciasProvider cajaObligatoria>
      <TabVentasRapidas />
    </PreferenciasProvider>,
  )
}

function ventaPosts(fetchMock) {
  return fetchMock.mock.calls.filter(
    c => String(c[0]).includes('/ventas') && c[1]?.method === 'POST',
  )
}

describe('TabVentasRapidas — guard de caja', () => {
  it('sin caja abierta, cobrar abre el modal de apertura y NO postea la venta', async () => {
    const fetchMock = instalarFetch()
    fetchMock.mockImplementation((url, opts) => {
      const u = String(url)
      if (u.includes('/caja/estado')) return Promise.resolve(jsonResp({ abierta: false }))
      if (u.includes('/productos/frecuentes')) return Promise.resolve(jsonResp([]))
      if (u.includes('/productos')) return Promise.resolve(jsonResp([MARTILLO]))
      if (u.includes('/ventas') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 9 }))
      return Promise.resolve(jsonResp([]))
    })
    renderConGuard()
    await agregarMartillo()
    fireEvent.click(screen.getByText(/Registrar venta/))

    await screen.findByText('¿Cuánto dinero hay en caja?')
    expect(ventaPosts(fetchMock)).toHaveLength(0)          // el cobro quedó en espera
    expect(screen.getByText('Martillo')).toBeInTheDocument()   // carrito intacto
  })

  it('abrir caja desde el modal registra la venta pendiente sin repetir nada', async () => {
    const fetchMock = instalarFetch()
    fetchMock.mockImplementation((url, opts) => {
      const u = String(url)
      if (u.includes('/caja/estado')) return Promise.resolve(jsonResp({ abierta: false }))
      if (u.includes('/caja/apertura')) return Promise.resolve({ ok: true, status: 201, json: async () => ({ id: 1 }) })
      if (u.includes('/productos/frecuentes')) return Promise.resolve(jsonResp([]))
      if (u.includes('/productos')) return Promise.resolve(jsonResp([MARTILLO]))
      if (u.includes('/ventas') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 9 }))
      return Promise.resolve(jsonResp([]))
    })
    renderConGuard()
    await agregarMartillo()
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText('¿Cuánto dinero hay en caja?')

    fireEvent.change(screen.getByLabelText('Dinero en caja'), { target: { value: '50000' } })
    fireEvent.click(screen.getByText('Abrir caja y registrar la venta'))
    await screen.findByText(/Busca o escanea/)   // carrito limpio: la venta se registró

    const apertura = fetchMock.mock.calls.find(c => String(c[0]).includes('/caja/apertura'))
    expect(JSON.parse(apertura[1].body)).toEqual({ saldo_inicial: 50000 })
    const posts = ventaPosts(fetchMock)
    expect(posts).toHaveLength(1)
    expect(posts[0][1].headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('el 409 caja_no_abierta del backend también abre el modal y el reintento usa la MISMA key', async () => {
    let ventasIntentos = 0
    const fetchMock = instalarFetch()
    fetchMock.mockImplementation((url, opts) => {
      const u = String(url)
      // El pre-check dice "abierta" (carrera: otro dispositivo la cerró) → el 409 es la defensa real.
      if (u.includes('/caja/estado')) return Promise.resolve(jsonResp({ abierta: true }))
      if (u.includes('/caja/apertura')) return Promise.resolve({ ok: true, status: 201, json: async () => ({ id: 1 }) })
      if (u.includes('/productos/frecuentes')) return Promise.resolve(jsonResp([]))
      if (u.includes('/productos')) return Promise.resolve(jsonResp([MARTILLO]))
      if (u.includes('/ventas') && opts?.method === 'POST') {
        ventasIntentos += 1
        if (ventasIntentos === 1) return Promise.resolve(errResp(409, { code: 'caja_no_abierta', mensaje: 'Abre la caja' }))
        return Promise.resolve(jsonResp({ id: 9 }))
      }
      return Promise.resolve(jsonResp([]))
    })
    renderConGuard()
    await agregarMartillo()
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText('¿Cuánto dinero hay en caja?')
    expect(screen.getByText('Martillo')).toBeInTheDocument()   // el 409 no vació el carrito

    fireEvent.change(screen.getByLabelText('Dinero en caja'), { target: { value: '20000' } })
    fireEvent.click(screen.getByText('Abrir caja y registrar la venta'))
    await screen.findByText(/Busca o escanea/)

    const posts = ventaPosts(fetchMock)
    expect(posts).toHaveLength(2)
    const k1 = posts[0][1].headers.get('Idempotency-Key')
    const k2 = posts[1][1].headers.get('Idempotency-Key')
    expect(k1).toBeTruthy()
    expect(k2).toBe(k1)   // mismo cobro: sin riesgo de venta duplicada
  })

  it('si la apertura falla, el modal sigue abierto y el carrito queda intacto', async () => {
    const fetchMock = instalarFetch()
    fetchMock.mockImplementation((url, opts) => {
      const u = String(url)
      if (u.includes('/caja/estado')) return Promise.resolve(jsonResp({ abierta: false }))
      if (u.includes('/caja/apertura')) return Promise.resolve(errResp(500, 'boom'))
      if (u.includes('/productos/frecuentes')) return Promise.resolve(jsonResp([]))
      if (u.includes('/productos')) return Promise.resolve(jsonResp([MARTILLO]))
      if (u.includes('/ventas') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 9 }))
      return Promise.resolve(jsonResp([]))
    })
    renderConGuard()
    await agregarMartillo()
    fireEvent.click(screen.getByText(/Registrar venta/))
    await screen.findByText('¿Cuánto dinero hay en caja?')

    fireEvent.change(screen.getByLabelText('Dinero en caja'), { target: { value: '1000' } })
    fireEvent.click(screen.getByText('Abrir caja y registrar la venta'))
    await waitFor(() => expect(ventaPosts(fetchMock)).toHaveLength(0))

    expect(screen.getByText('¿Cuánto dinero hay en caja?')).toBeInTheDocument()
    expect(screen.getByText('Martillo')).toBeInTheDocument()
  })
})
