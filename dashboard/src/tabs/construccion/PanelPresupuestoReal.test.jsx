import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), message: vi.fn() } }))

import PanelPresupuestoReal from './PanelPresupuestoReal.jsx'
import { isRouteEnabled } from '@/lib/features.jsx'

function jsonResp(data, status = 200) { return { ok: status < 400, status, json: async () => data } }

// Desglove de gasto real "en amarillo": la obra se está comiendo la utilidad presupuestada.
const GASTO_REAL = {
  total_gastos: '1000000', total_compras: '2000000', total_prorrateo_nomina: '500000',
  total_horas_maquina: '1500000', total_consumos_inventario: '0',
  total: '5000000', semaforo: 'amarillo',
  ingreso_presupuestado: '10000000', utilidad_presupuestada: '400000', utilidad_real: '5000000',
}
const CEROS = {
  total_gastos: '0', total_compras: '0', total_prorrateo_nomina: '0', total_horas_maquina: '0',
  total_consumos_inventario: '0', total: '0', semaforo: 'verde',
  ingreso_presupuestado: '0', utilidad_presupuestada: '0', utilidad_real: '0',
}

const OBRA = { id: 7, nombre: 'Pavimentación vía La Estrella', estado: 'EN_EJECUCION' }

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

// Monta el panel con un gasto-real dado; `extra` permite interceptar POSTs u otros paths.
function montar(gastoReal = GASTO_REAL, obra = OBRA, extra) {
  const fetchMock = vi.fn((url, opts) => {
    const u = String(url)
    if (extra) { const r = extra(u, opts); if (r) return r }
    if (u.includes(`/obras/${obra.id}/gasto-real`)) return Promise.resolve(jsonResp(gastoReal))
    if (u.includes(`/obras/${obra.id}/liquidacion`)) return Promise.resolve(jsonResp({ fecha_liquidacion: '2026-07-01T00:00:00-05:00' }))
    return Promise.resolve(jsonResp({}))
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<PanelPresupuestoReal obra={obra} onCambio={vi.fn()} />)
  return fetchMock
}

describe('PanelPresupuestoReal', () => {
  it('renderiza el desglose por componente y el gasto real total', async () => {
    montar()
    expect(await screen.findByText('Gasto real total')).toBeInTheDocument()
    // Los 5 componentes del DesgloseGasto.
    expect(screen.getByText('Gastos')).toBeInTheDocument()
    expect(screen.getByText('Compras')).toBeInTheDocument()
    expect(screen.getByText('Nómina prorrateada')).toBeInTheDocument()
    expect(screen.getByText('Horas de máquina')).toBeInTheDocument()
    expect(screen.getByText('Consumos de inventario')).toBeInTheDocument()
    expect(screen.getByText('Utilidad real')).toBeInTheDocument()
  })

  it('mapea el semáforo del backend a su etiqueta según el umbral', async () => {
    // El panel confía en el semáforo autoritativo del backend (función pura) y solo lo traduce a etiqueta.
    montar({ ...GASTO_REAL, semaforo: 'verde' })
    expect(await screen.findByText('Rentable')).toBeInTheDocument()
    cleanup()
    montar({ ...GASTO_REAL, semaforo: 'amarillo' })
    expect(await screen.findByText('Comiéndose la utilidad')).toBeInTheDocument()
    cleanup()
    montar({ ...GASTO_REAL, semaforo: 'rojo' })
    expect(await screen.findByText('En pérdida')).toBeInTheDocument()
  })

  it('muestra un estado vacío con propósito cuando no se ha imputado nada', async () => {
    montar(CEROS)
    expect(await screen.findByText('Aún no se ha imputado nada a esta obra')).toBeInTheDocument()
    // Aun vacía, las acciones para empezar a imputar siguen disponibles.
    expect(screen.getByRole('button', { name: /Imputar gasto/ })).toBeInTheDocument()
  })

  it('imputar gasto postea el shape correcto (POST /gastos con obra_id + categoria POS)', async () => {
    // El gasto NO es sub-recurso de obra: va al router de caja (POST /gastos) con `obra_id` en el cuerpo
    // y la `categoria` POS NOT NULL derivada del vertical (OTRO → 'otros').
    const fetchMock = montar(GASTO_REAL, OBRA, (u, opts) => {
      if (u.endsWith('/gastos') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 99 }, 201))
      return null
    })

    fireEvent.click(await screen.findByRole('button', { name: /Imputar gasto/ }))
    fireEvent.change(await screen.findByLabelText('Concepto'), { target: { value: 'Combustible' } })
    fireEvent.change(screen.getByLabelText('Monto'), { target: { value: '150000' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar gasto' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/gastos') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      const body = JSON.parse(call[1].body)
      expect(body).toMatchObject({
        obra_id: 7, categoria: 'otros', concepto: 'Combustible', monto: '150000',
        categoria_gasto: 'OTRO', metodo_pago: 'EFECTIVO', numero_referencia: null,
      })
      expect(body.fecha).toBeUndefined()   // el backend GastoCrear no tiene `fecha`: no se envía
    })
  })

  it('registrar horas postea a /maquinas/{maquina_id}/horas con obra_id en el cuerpo', async () => {
    const fetchMock = montar(GASTO_REAL, OBRA, (u, opts) => {
      if (u.includes('/maquinas') && u.endsWith('/horas') && opts?.method === 'POST') {
        return Promise.resolve(jsonResp({ registro_id: 5, horas_facturables: '6', minimo_cubierto: true, ingreso: '900000', replay: false }, 201))
      }
      if (u.endsWith('/maquinas')) return Promise.resolve(jsonResp([{ id: 3, codigo: 'M-001', nombre: 'Retro', estado: 'DISPONIBLE' }]))
      return null
    })

    fireEvent.click(await screen.findByRole('button', { name: /Registrar horas/ }))
    fireEvent.change(await screen.findByLabelText('Máquina'), { target: { value: '3' } })
    fireEvent.change(screen.getByLabelText('Horas trabajadas'), { target: { value: '6' } })
    fireEvent.click(screen.getByRole('button', { name: 'Guardar horas' }))

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/maquinas/3/horas') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
      const body = JSON.parse(call[1].body)
      expect(body).toMatchObject({ obra_id: 7, horas_trabajadas: '6' })
      expect(body.fecha).toMatch(/^\d{4}-\d{2}-\d{2}$/)   // fecha por defecto hoy Colombia
    })
  })

  it('liquidar postea a /obras/{id}/liquidar cuando la obra está FINALIZADA', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true))   // window.confirm → aceptar
    const obra = { ...OBRA, estado: 'FINALIZADA' }
    const fetchMock = montar(GASTO_REAL, obra, (u, opts) => {
      if (u.includes('/obras/7/liquidar') && opts?.method === 'POST') return Promise.resolve(jsonResp({ id: 1, obra_id: 7, semaforo: 'amarillo' }, 201))
      return null
    })

    const botonLiquidar = await screen.findByRole('button', { name: /Liquidar obra/ })
    expect(botonLiquidar).not.toBeDisabled()
    fireEvent.click(botonLiquidar)

    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => String(c[0]).includes('/obras/7/liquidar') && c[1]?.method === 'POST')
      expect(call).toBeTruthy()
    })
  })

  it('el botón Liquidar está deshabilitado si la obra no está FINALIZADA', async () => {
    montar(GASTO_REAL, { ...OBRA, estado: 'EN_EJECUCION' })
    const boton = await screen.findByRole('button', { name: /Liquidar obra/ })
    expect(boton).toBeDisabled()
  })

  it('una obra LIQUIDADA muestra el snapshot congelado y oculta las acciones', async () => {
    montar(GASTO_REAL, { ...OBRA, estado: 'LIQUIDADA' })
    expect(await screen.findByText(/quedó congelado/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Imputar gasto/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Liquidar obra/ })).not.toBeInTheDocument()
  })

  it('la ruta /obras se gatea por la feature `obras` (y el meta-pack construccion)', () => {
    expect(isRouteEnabled('/obras', [])).toBe(false)
    expect(isRouteEnabled('/obras', ['obras'])).toBe(true)
    expect(isRouteEnabled('/obras', ['construccion'])).toBe(true)
  })
})
