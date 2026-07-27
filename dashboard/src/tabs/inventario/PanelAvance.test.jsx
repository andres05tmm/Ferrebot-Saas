/*
 * PanelAvance — las dos puertas para meter un producto al inventario (issue #180).
 *
 * Lo que importa: que "Se acabó" mande un conteo en CERO (el día que el estante queda vacío el
 * stock es cero exacto, sin contar nada) y que el panel desaparezca cuando ya no hay pendientes —
 * un tablero que dice "0 productos por cuadrar" es ruido permanente en la pantalla.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import PanelAvance from './PanelAvance.jsx'

const AVANCE = {
  activos: 604, cuadrados: 132,
  pendientes: [
    { producto_id: 7, nombre: 'Cemento Gris', unidad_medida: 'Kg', stock_actual: '-40', lineas_vendidas: 31 },
    { producto_id: 9, nombre: 'Yeso', unidad_medida: 'Kg', stock_actual: '0', lineas_vendidas: 4 },
  ],
}

function respuesta(body, ok = true) {
  return Promise.resolve({ ok, status: ok ? 200 : 500, json: () => Promise.resolve(body) })
}

beforeEach(() => {
  global.fetch = vi.fn((url) => {
    if (String(url).includes('/inventario/avance')) return respuesta(AVANCE)
    return respuesta({})
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('PanelAvance', () => {
  it('muestra el avance y los pendientes en el orden que los manda el servidor', async () => {
    render(<PanelAvance />)
    expect(await screen.findByText(/132 de 604 en control/)).toBeTruthy()
    const filas = screen.getAllByRole('listitem')
    expect(filas[0].textContent).toContain('Cemento Gris')     // el que más rota, primero
    expect(filas[0].textContent).toContain('sin contar')       // el negativo se muestra como backlog
  })

  it('"Se acabó" postea un conteo en CERO', async () => {
    const onCuadrado = vi.fn()
    render(<PanelAvance onCuadrado={onCuadrado} />)
    await screen.findByText('Cemento Gris')

    fireEvent.click(screen.getAllByRole('button', { name: 'Se acabó' })[0])

    await waitFor(() => {
      const llamada = global.fetch.mock.calls.find(
        ([u]) => String(u).includes('/inventario/conteo'))
      expect(llamada).toBeTruthy()
      expect(JSON.parse(llamada[1].body)).toMatchObject({
        producto_id: 7, cantidad_contada: 0, motivo: 'Se acabó',
      })
    })
    expect(onCuadrado).toHaveBeenCalled()
  })

  it('"Conté…" abre el input y manda la cantidad escrita', async () => {
    render(<PanelAvance />)
    await screen.findByText('Cemento Gris')

    fireEvent.click(screen.getAllByRole('button', { name: 'Conté…' })[0])
    fireEvent.change(screen.getByLabelText('Cantidad contada de Cemento Gris'), { target: { value: '18' } })
    fireEvent.click(screen.getByLabelText('Guardar conteo de Cemento Gris'))

    await waitFor(() => {
      const llamada = global.fetch.mock.calls.find(
        ([u]) => String(u).includes('/inventario/conteo'))
      expect(JSON.parse(llamada[1].body)).toMatchObject({ producto_id: 7, cantidad_contada: 18 })
    })
  })

  it('desaparece cuando ya no queda nada por cuadrar', async () => {
    global.fetch = vi.fn(() => respuesta({ activos: 604, cuadrados: 604, pendientes: [] }))
    const { container } = render(<PanelAvance />)
    await waitFor(() => expect(container.textContent).toBe(''))
  })
})
