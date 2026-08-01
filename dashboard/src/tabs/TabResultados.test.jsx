import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/components/RealtimeProvider.jsx', () => ({
  RealtimeProvider: ({ children }) => children,
  useRealtimeEvent: () => {},
}))
const authState = vi.hoisted(() => ({ admin: true }))
vi.mock('@/hooks/useAuth.js', () => ({ useAuth: () => ({ isAdmin: () => authState.admin }) }))

import TabResultados from './TabResultados.jsx'

const RESULTADOS = {
  desde: '2026-06-01', hasta: '2026-06-05',
  ventas_brutas: '110000.00', devoluciones: '10000.00',
  ingresos: '100000.00', costo_ventas: '60000.00', utilidad_bruta: '40000.00',
  gastos: '15000.00', utilidad_neta: '25000.00',
  cobertura_pct: '80.00',
  anterior: {
    desde: '2026-05-27', hasta: '2026-05-31',
    ingresos: '90000.00', costo_ventas: '50000.00', utilidad_bruta: '40000.00',
    gastos: '10000.00', utilidad_neta: '20000.00',
  },
}
const RESUMEN = {
  fecha: '2026-06-05', num_ventas: 8, total_vendido: '119000.00',
  ticket_promedio: '14875.00', por_metodo_pago: { efectivo: '119000.00' },
}
const GASTOS = {
  desde: '2026-06-01', hasta: '2026-06-05',
  total_gasto: '15000.00', total_retiro: '0', total_inversion: '0', total_pago_deuda: '0',
  fijos: '9000.00', variables: '6000.00', por_categoria: [],
  gasto_periodo_anterior: '10000.00', ventas: '100000.00', pct_ventas: '15.00',
  margen_bruto_pct: '40.00', fijos_mes: '80000.00',
  punto_equilibrio_mes: '200000.00', punto_equilibrio_dia: '6666.67',
}
const FLUJO = {
  desde: '2026-06-01', hasta: '2026-06-05',
  ventas_por_metodo: { efectivo: '20000.00' }, ventas_fiado: '10000.00',
  abonos_fiados: '4000.00', ingresos_caja: '0', total_entradas: '24000.00',
  gastos_por_categoria: { otros: '5000.00' }, abonos_proveedores: '3000.00',
  egresos_caja: '0', total_salidas: '8000.00', neto: '16000.00',
}
const MARGEN_PRODUCTO = [{
  clave: 'Martillo', producto_id: 1, cantidad: '2.000', ingresos: '20000.00',
  cogs: '24000.00', margen: '-4000.00', margen_pct: '-20.00', cobertura_pct: '50.00',
}]
const MARGEN_CATEGORIA = [{
  clave: 'Pinturas', producto_id: null, cantidad: '5.000', ingresos: '50000.00',
  cogs: '35000.00', margen: '15000.00', margen_pct: '30.00', cobertura_pct: '100.00',
}]

function jsonResp(data) { return { ok: true, status: 200, json: async () => data } }

function instalarFetch() {
  const fetchMock = vi.fn((url) => {
    const u = String(url)
    if (u.includes('/reportes/resultados')) return Promise.resolve(jsonResp(RESULTADOS))
    if (u.includes('/reportes/resumen')) return Promise.resolve(jsonResp(RESUMEN))
    if (u.includes('/reportes/gastos')) return Promise.resolve(jsonResp(GASTOS))
    if (u.includes('/reportes/flujo-dinero')) return Promise.resolve(jsonResp(FLUJO))
    if (u.includes('/reportes/margen-productos')) {
      return Promise.resolve(jsonResp(u.includes('por=categoria') ? MARGEN_CATEGORIA : MARGEN_PRODUCTO))
    }
    return Promise.resolve(jsonResp([]))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const pedidos = (mock, fragmento) =>
  mock.mock.calls.filter(c => String(c[0]).includes(fragmento))

/** La tarjeta del héroe. La utilidad aparece a propósito en dos sitios (héroe y cascada), así que
 *  las aserciones sobre ella se acotan a su sección. */
const heroe = async () =>
  (await screen.findByText('Utilidad del periodo')).parentElement

beforeEach(() => { localStorage.clear(); authState.admin = true })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('TabResultados', () => {
  it('admin: pide /reportes/resultados con el rango y pinta la utilidad como métrica héroe', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    expect(within(await heroe()).getByText('$25.000')).toBeInTheDocument()  // utilidad neta
    const kpis = within(screen.getByRole('group', { name: 'Métricas del periodo' }))
    expect(kpis.getByText('$100.000')).toBeInTheDocument()                  // ventas netas
    expect(kpis.getByText('$60.000')).toBeInTheDocument()                   // costo de mercancía
    expect(kpis.getByText('$14.875')).toBeInTheDocument()                   // ticket promedio
    const [call] = pedidos(fetchMock, '/reportes/resultados')
    expect(String(call[0])).toContain('desde=')
    expect(String(call[0])).toContain('hasta=')
  })

  it('cada cifra trae su delta contra el periodo anterior NOMBRADO', async () => {
    instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    // Utilidad 25.000 vs 20.000 = +25,0%, y el label dice contra QUÉ compara (sin eso es ruido).
    const card = within(await heroe())
    expect(card.getByText(/\+25\.0%/)).toBeInTheDocument()
    expect(card.getByText(/vs\. 27–31 may/)).toBeInTheDocument()
    // Ventas netas 100.000 vs 90.000 = +11,1% — cada métrica lleva SU delta, no el del héroe.
    expect(screen.getByText(/\+11\.1%/)).toBeInTheDocument()
  })

  it('en el costo de mercancía, subir se pinta como malo (dirección semántica)', async () => {
    instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    // Costo 60.000 vs 50.000 = +20%: el número sube, pero es una mala noticia.
    const delta = await screen.findByText(/\+20\.0%/)
    expect(delta.className).toContain('text-danger')
  })

  it('avisa que el margen es estimado cuando la cobertura de costo no llega al 100%', async () => {
    instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)
    expect(await screen.findByText(/sobre el 80% de lo vendido/)).toBeInTheDocument()
  })

  it('la cascada muestra las devoluciones como renglón que resta', async () => {
    instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    const cascada = (await screen.findByRole('heading', { name: /De la venta a la utilidad/i }))
      .closest('div')
    expect(within(cascada).getByText(/Devoluciones/)).toBeInTheDocument()
    expect(within(cascada).getByText('$110.000')).toBeInTheDocument()   // ventas brutas
    expect(within(cascada).getByText('($10.000)')).toBeInTheDocument()  // lo devuelto, en negativo
  })

  it('el punto de equilibrio muestra el avance del mes', async () => {
    instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    expect(await screen.findByText(/de \$200\.000/)).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toBeInTheDocument()
  })

  it('el flujo de dinero es una sección colapsada: entradas, neto y el fiado explicado', async () => {
    instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)
    await heroe()

    expect(await screen.findByText(/Entró \$24\.000/)).toBeInTheDocument()
    expect(screen.getByText(/eso es cartera/)).toBeInTheDocument()      // el fiado no es plata en mano
    expect(screen.getByText('Abonos a proveedores')).toBeInTheDocument()
  })

  it('el detalle por producto solo se pide al abrir la sección', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)
    await heroe()

    expect(pedidos(fetchMock, 'por=producto')).toHaveLength(0)

    // jsdom no implementa el toggle nativo de <details>: se abre a mano y se emite el evento.
    const detalle = screen.getByText(/Margen producto por producto/).closest('details')
    detalle.open = true
    fireEvent(detalle, new Event('toggle'))

    expect(await screen.findByText('Martillo')).toBeInTheDocument()
    expect(screen.getByText(/costo incompleto/)).toBeInTheDocument()    // margen no confiable, avisado
    expect(pedidos(fetchMock, 'por=producto')).toHaveLength(1)
  })

  it('los presets de periodo re-piden con otro rango', async () => {
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)
    await heroe()
    const antes = pedidos(fetchMock, '/reportes/resultados').length

    fireEvent.click(screen.getByRole('button', { name: 'Año corrido' }))

    const llamadas = pedidos(fetchMock, '/reportes/resultados')
    expect(llamadas.length).toBeGreaterThan(antes)
    expect(String(llamadas.at(-1)[0])).toMatch(/desde=\d{4}-01-01/)
  })

  it('vendedor: NO ve el P&L ni pide el endpoint', async () => {
    authState.admin = false
    const fetchMock = instalarFetch()
    render(<MemoryRouter><TabResultados /></MemoryRouter>)

    expect(await screen.findByText(/solo para administradores/i)).toBeInTheDocument()
    expect(pedidos(fetchMock, '/reportes/resultados')).toHaveLength(0)
  })
})
