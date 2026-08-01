import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))

import TabKardex from './TabKardex.jsx'
import { LIMITE_KARDEX } from '@/lib/queries'
import { conQuery } from '@/test/query.jsx'

const PRODUCTOS = [{ id: 5, nombre: 'Taladro Bosch', codigo: 'TAL-01', precio_venta: '120000' }]

// Los CUATRO tipos del enum `mov_inventario_tipo`, y nada más: la base rechaza cualquier otro.
// El fixture viejo usaba 'VENTA' y 'COMPRA', valores imposibles, así que el test pasaba en verde
// mientras el tab pintaba de gris las ventas y las compras reales.
const KARDEX = [
  { id: 100, tipo: 'SALIDA', cantidad: '2', costo_unitario: '80000', referencia: 'venta #12',
    usuario_id: 1, creado_en: '2026-06-10T14:00:00+00:00' },
  { id: 101, tipo: 'ENTRADA', cantidad: '10', costo_unitario: '75000', referencia: 'compra #3',
    usuario_id: 1, creado_en: '2026-06-08T14:00:00+00:00' },
  { id: 102, tipo: 'DEVOLUCION', cantidad: '1', costo_unitario: '80000', referencia: 'devolución #4',
    usuario_id: 1, creado_en: '2026-06-07T14:00:00+00:00' },
  // El AJUSTE guarda el delta CON signo: −3 es una merma encontrada al contar.
  { id: 103, tipo: 'AJUSTE', cantidad: '-3', costo_unitario: null, referencia: 'conteo',
    usuario_id: 1, creado_en: '2026-06-06T14:00:00+00:00' },
]

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

function instalarFetch(kardex = KARDEX) {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/inventario/kardex/5')) return Promise.resolve(jsonResp(kardex))
    if (u.includes('/productos')) return Promise.resolve(jsonResp(PRODUCTOS))
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function abrirProducto() {
  render(conQuery(<MemoryRouter><TabKardex /></MemoryRouter>))
  fireEvent.change(screen.getByLabelText('Buscar producto'), { target: { value: 'taladro' } })
  fireEvent.click(await screen.findByRole('button', { name: /Taladro Bosch/ }))
}

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabKardex', () => {
  it('parte con un estado vacío que invita a buscar', () => {
    instalarFetch()
    render(conQuery(<MemoryRouter><TabKardex /></MemoryRouter>))
    expect(screen.getByText(/Busca un producto para ver su historial/i)).toBeInTheDocument()
  })

  it('busca, selecciona un producto y muestra sus movimientos', async () => {
    instalarFetch()
    await abrirProducto()

    expect(await screen.findByText('SALIDA')).toBeInTheDocument()
    expect(screen.getByText('ENTRADA')).toBeInTheDocument()
    expect(screen.getByText(/venta #12/)).toBeInTheDocument()
  })

  it('el signo sale del tipo, y del delta cuando el AJUSTE viene negativo', async () => {
    instalarFetch()
    await abrirProducto()

    expect(await screen.findByText('−2')).toBeInTheDocument()   // SALIDA resta
    expect(screen.getByText('+10')).toBeInTheDocument()         // ENTRADA suma
    expect(screen.getByText('+1')).toBeInTheDocument()          // DEVOLUCION reingresa mercancía
    expect(screen.getByText('−3')).toBeInTheDocument()          // AJUSTE con delta negativo
  })

  it('los CUATRO tipos reales llevan su color (ninguno cae al gris del fallback)', async () => {
    // La regresión que esto ataja: el mapa de tonos estaba escrito contra VENTA/COMPRA/CONTEO,
    // tipos que no existen, así que lo más frecuente del kárdex se pintaba sin color.
    instalarFetch()
    await abrirProducto()

    for (const tipo of ['SALIDA', 'ENTRADA', 'DEVOLUCION', 'AJUSTE']) {
      const badge = await screen.findByText(tipo)
      expect(badge.className).not.toMatch(/text-muted-foreground/)
    }
  })

  it('avisa cuando la lista viene cortada por el límite', async () => {
    const muchos = Array.from({ length: LIMITE_KARDEX }, (_, i) => ({
      id: 1000 + i, tipo: 'SALIDA', cantidad: '1', costo_unitario: '80000',
      referencia: `venta #${i}`, usuario_id: 1, creado_en: '2026-06-10T14:00:00+00:00',
    }))
    instalarFetch(muchos)
    await abrirProducto()

    expect(await screen.findByText(/puede tener\s+más atrás/i)).toBeInTheDocument()
  })

  it('con pocos movimientos NO avisa de corte', async () => {
    instalarFetch()
    await abrirProducto()

    expect(await screen.findByText('SALIDA')).toBeInTheDocument()
    expect(screen.queryByText(/puede tener\s+más atrás/i)).not.toBeInTheDocument()
  })
})
