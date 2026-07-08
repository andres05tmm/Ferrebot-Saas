import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

// Rol controlable por test: admin ve los controles CRUD; vendedor sigue en solo-lectura.
const authState = vi.hoisted(() => ({ admin: false }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabInventario from './TabInventario.jsx'
import { FeaturesProvider } from '@/lib/features.jsx'

// Render con features (gating por familia). Sin provider, useFeatures() → [] (retail).
function renderInv(features = []) {
  return render(
    <MemoryRouter><FeaturesProvider features={features}><TabInventario /></FeaturesProvider></MemoryRouter>,
  )
}
const CONSTRUCCION = ['construccion', 'obras', 'pos', 'inventario']

const PRODUCTOS = [
  { id: 1, nombre: 'Martillo', precio_venta: '11900', unidad_medida: 'unidad', activo: true, codigo: null, categoria: null, iva: 19 },
  { id: 2, nombre: 'Clavo', precio_venta: '100', unidad_medida: 'unidad', activo: true, codigo: null, categoria: null, iva: 19 },
]
const STOCK = [{ producto_id: 1, nombre: 'Martillo', stock_actual: '50', stock_minimo: '10', bajo: false }]
const CATEGORIAS = ['Herramientas', 'Pinturas']
const PROVEEDORES = [{ id: 7, nombre: 'Andina', nit: '900.1' }]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch(stock = STOCK, { categorias = CATEGORIAS, proveedores = PROVEEDORES } = {}) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (u.includes('/inventario/conteo') && opts?.method === 'POST')
      return Promise.resolve(jsonResp({ producto_id: 1, movimiento_id: 5, delta: '40', stock_actual: '40', replay: false }, 201))
    if (u.includes('/inventario/stock')) return Promise.resolve(jsonResp(stock))
    if (u.includes('/productos/categorias')) return Promise.resolve(jsonResp(categorias))
    if (u.includes('/proveedores')) return Promise.resolve(jsonResp(proveedores))
    if (u.includes('/productos') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 99 }, 201))
    if (u.includes('/productos') && opts?.method === 'PUT') return Promise.resolve(jsonResp({ id: 1 }, 200))
    if (u.includes('/productos') && opts?.method === 'DELETE') return Promise.resolve(jsonResp({ producto_id: 1, activo: false }, 200))
    if (u.includes('/productos')) return Promise.resolve(jsonResp(PRODUCTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { localStorage.clear(); authState.admin = false })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabInventario — solo lectura (vendedor)', () => {
  it('lista productos (activo=true) y filtra con ?q', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)

    expect(await screen.findByText('Martillo')).toBeInTheDocument()
    // El listado por defecto pide solo activos.
    expect(fetchMock.mock.calls.some(c => String(c[0]).includes('activo=true'))).toBe(true)

    fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'mar' } })
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('q=mar'))).toBe(true)
    })
  })

  it('un vendedor NO ve controles de crear/editar/eliminar/ajustar', async () => {
    instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    expect(screen.queryByText('Nuevo producto')).toBeNull()
    expect(screen.queryByTitle('Ajustar stock')).toBeNull()
    expect(screen.queryByTitle('Editar producto')).toBeNull()
    expect(screen.queryByTitle('Eliminar producto')).toBeNull()
  })
})

describe('TabInventario — CRUD (admin)', () => {
  beforeEach(() => { authState.admin = true })

  it('admin ve los controles de crear/editar/eliminar', async () => {
    instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    expect(screen.getByText('Nuevo producto')).toBeInTheDocument()
    expect(screen.getAllByTitle('Editar producto').length).toBeGreaterThan(0)
    expect(screen.getAllByTitle('Eliminar producto').length).toBeGreaterThan(0)
  })

  function postBody(fetchMock) {
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/productos') && c[1]?.method === 'POST')
    return JSON.parse(call[1].body)
  }

  it('crear postea el shape nuevo: proveedor_id + precio_especial, sin marca/mayorista/stock', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getByText('Nuevo producto'))
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Cemento' } })
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: '50000' } })
    // El proveedor sale del endpoint /proveedores (no es texto libre).
    await screen.findByRole('option', { name: 'Andina' })
    fireEvent.change(screen.getByLabelText('Proveedor'), { target: { value: '7' } })
    fireEvent.change(screen.getByLabelText('Precio especial'), { target: { value: '45000' } })
    fireEvent.click(screen.getByText('Crear producto'))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos') && c[1]?.method === 'POST')).toBe(true)
    })
    const body = postBody(fetchMock)
    expect(body).toMatchObject({
      nombre: 'Cemento', precio_venta: 50000, proveedor_id: 7, precio_especial: 45000,
      permite_fraccion: false, activo: true, fracciones: [],
    })
    // Campos retirados del contrato y bloque escalonado colapsado → no se envían.
    expect(body).not.toHaveProperty('marca')
    expect(body).not.toHaveProperty('precio_mayorista')
    expect(body).not.toHaveProperty('stock_inicial')
    expect(body).not.toHaveProperty('stock_minimo')
    expect(body).not.toHaveProperty('precio_umbral')
  })

  it('el precio escalonado SOLO se envía si se despliega', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getByText('Nuevo producto'))
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Cemento' } })
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: '50000' } })
    fireEvent.click(screen.getByRole('button', { name: /añadir precio escalonado/i }))
    fireEvent.change(screen.getByLabelText('Umbral de cantidad'), { target: { value: '10' } })
    fireEvent.change(screen.getByLabelText('Precio bajo umbral'), { target: { value: '9000' } })
    fireEvent.change(screen.getByLabelText('Precio sobre umbral'), { target: { value: '8000' } })
    fireEvent.click(screen.getByText('Crear producto'))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos') && c[1]?.method === 'POST')).toBe(true)
    })
    expect(postBody(fetchMock)).toMatchObject({
      precio_umbral: 10, precio_bajo_umbral: 9000, precio_sobre_umbral: 8000,
    })
  })

  it('la categoría sale del endpoint y permite crear una nueva', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getByText('Nuevo producto'))
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'X' } })
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: '1' } })
    // Opción existente venida de /productos/categorias.
    expect(await screen.findByRole('option', { name: 'Herramientas' })).toBeInTheDocument()
    // Elegir "nueva categoría" revela el campo de texto.
    fireEvent.change(screen.getByLabelText('Categoría'), { target: { value: '__nueva__' } })
    fireEvent.change(screen.getByLabelText('Nueva categoría'), { target: { value: 'Tornillería' } })
    fireEvent.click(screen.getByText('Crear producto'))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos') && c[1]?.method === 'POST')).toBe(true)
    })
    expect(postBody(fetchMock).categoria).toBe('Tornillería')
  })

  it('si no hay proveedores, avisa que se registren en el tab Proveedores', async () => {
    instalarFetch(STOCK, { proveedores: [] })
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getByText('Nuevo producto'))
    expect(await screen.findByText(/registra proveedores en el tab proveedores/i)).toBeInTheDocument()
  })

  it('editar hace PUT /productos/{id}', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getAllByTitle('Editar producto')[0])
    expect(await screen.findByText('Editar producto')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Precio de venta'), { target: { value: '12500' } })
    fireEvent.click(screen.getByText('Guardar cambios'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/productos/1') && c[1]?.method === 'PUT')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({ precio_venta: 12500 })
    })
  })

  it('eliminar hace DELETE (soft) tras confirmar', async () => {
    const fetchMock = instalarFetch()
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getAllByTitle('Eliminar producto')[0])
    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos/1') && c[1]?.method === 'DELETE')).toBe(true)
    })
  })

  it('NO elimina si el usuario cancela la confirmación', async () => {
    const fetchMock = instalarFetch()
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getAllByTitle('Eliminar producto')[0])
    expect(fetchMock.mock.calls.some(c => c[1]?.method === 'DELETE')).toBe(false)
  })

  it('conteo físico postea /inventario/conteo con cantidad_contada (no el delta)', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getAllByTitle('Ajustar stock')[0])           // abre el panel del producto 1
    fireEvent.change(await screen.findByLabelText('Cantidad real contada'), { target: { value: '40' } })
    fireEvent.click(screen.getByText('Ajustar a real'))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/inventario/conteo') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      expect(JSON.parse(call[1].body)).toMatchObject({ producto_id: 1, cantidad_contada: 40 })
    })
  })
})

describe('TabInventario — indicador de stock negativo (suave)', () => {
  it('una fila con stock < 0 muestra "por cuadrar" con tooltip, sin estilo de error', async () => {
    instalarFetch([{ producto_id: 2, nombre: 'Clavo', stock_actual: '-5', stock_minimo: '0', bajo: false }])
    render(<MemoryRouter><TabInventario /></MemoryRouter>)
    await screen.findByText('Clavo')

    const etiqueta = await screen.findByText('por cuadrar')
    expect(etiqueta).toBeInTheDocument()
    expect(etiqueta.className).not.toMatch(/destructive/)            // ámbar atenuado, NO rojo
    // Tooltip amable explicativo (sin ⚠️).
    expect(screen.getByTitle(/conteo físico para cuadrar/i)).toBeInTheDocument()
    expect(screen.queryByText('⚠️')).toBeNull()
  })
})

// ── Familia construcción: catálogo de consumibles/repuestos, no de venta por mostrador ──────────────
describe('TabInventario — construcción (consumibles/repuestos)', () => {
  beforeEach(() => { authState.admin = true })

  it('el formulario oculta escalonado, fracciones y precio especial; el precio se relabela a "Precio"', async () => {
    instalarFetch()
    renderInv(CONSTRUCCION)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getByText('Nuevo producto'))
    // El precio de venta se conserva (obligatorio en el backend) pero relabelado a "Precio".
    expect(screen.getByLabelText('Precio')).toBeInTheDocument()
    expect(screen.queryByLabelText('Precio de venta')).toBeNull()
    // Se retiran las mecánicas de venta: escalonado, fracciones y precio especial.
    expect(screen.queryByRole('button', { name: /añadir precio escalonado/i })).toBeNull()
    expect(screen.queryByLabelText('Permite fracción')).toBeNull()
    expect(screen.queryByLabelText('Precio especial')).toBeNull()
    // El costo de compra sí queda (importa para el consumible).
    expect(screen.getByLabelText('Precio de compra')).toBeInTheDocument()
  })

  it('crear postea precio_venta (obligatorio) y NADA de venta (sin umbral ni fracción)', async () => {
    const fetchMock = instalarFetch()
    renderInv(CONSTRUCCION)
    await screen.findByText('Martillo')

    fireEvent.click(screen.getByText('Nuevo producto'))
    fireEvent.change(screen.getByLabelText('Nombre'), { target: { value: 'Broca 1/2' } })
    fireEvent.change(screen.getByLabelText('Precio'), { target: { value: '8000' } })
    fireEvent.click(screen.getByText('Crear producto'))

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(c => String(c[0]).includes('/productos') && c[1]?.method === 'POST')).toBe(true)
    })
    const call = fetchMock.mock.calls.find(c => String(c[0]).includes('/productos') && c[1]?.method === 'POST')
    const body = JSON.parse(call[1].body)
    expect(body).toMatchObject({ nombre: 'Broca 1/2', precio_venta: 8000, permite_fraccion: false, fracciones: [] })
    expect(body).not.toHaveProperty('precio_umbral')
  })

  it('el aviso de stock negativo usa el copy de CONSUMO, no de venta', async () => {
    instalarFetch([{ producto_id: 2, nombre: 'Clavo', stock_actual: '-5', stock_minimo: '0', bajo: false }])
    renderInv(CONSTRUCCION)
    await screen.findByText('Clavo')

    expect(await screen.findByText('por cuadrar')).toBeInTheDocument()
    expect(screen.getByTitle(/se consumió más de lo registrado/i)).toBeInTheDocument()
    expect(screen.queryByTitle(/vendiste más/i)).toBeNull()
  })
})
