import { describe, expect, it } from 'vitest'
import { PERIODOS, PERIODOS_RESULTADOS, periodo } from './gastos.js'

describe('periodo()', () => {
  it('trimestre: arranca en el primer mes del trimestre en curso y llega hasta hoy', () => {
    // Los cuatro trimestres, con un mes de cada uno en los bordes.
    expect(periodo('trimestre', '2026-01-15').desde).toBe('2026-01-01')
    expect(periodo('trimestre', '2026-03-31').desde).toBe('2026-01-01')
    expect(periodo('trimestre', '2026-04-01').desde).toBe('2026-04-01')
    expect(periodo('trimestre', '2026-07-30').desde).toBe('2026-07-01')
    expect(periodo('trimestre', '2026-11-05').desde).toBe('2026-10-01')
    expect(periodo('trimestre', '2026-12-31').desde).toBe('2026-10-01')
    expect(periodo('trimestre', '2026-07-30').hasta).toBe('2026-07-30')
  })

  it('año corrido: del 1 de enero a hoy, no el año calendario completo', () => {
    expect(periodo('anio', '2026-07-30')).toEqual({ desde: '2026-01-01', hasta: '2026-07-30' })
  })

  it('los presets viejos no cambiaron (el tab Gastos depende de ellos)', () => {
    expect(periodo('hoy', '2026-07-30')).toEqual({ desde: '2026-07-30', hasta: '2026-07-30' })
    expect(periodo('mes', '2026-07-30')).toEqual({ desde: '2026-07-01', hasta: '2026-07-30' })
    expect(periodo('pasado', '2026-07-30')).toEqual({ desde: '2026-06-01', hasta: '2026-06-30' })
    expect(PERIODOS.map(([id]) => id)).toEqual(['hoy', 'semana', 'mes', 'pasado'])
  })

  it('Resultados suma las ventanas largas a los presets de Gastos', () => {
    expect(PERIODOS_RESULTADOS.map(([id]) => id))
      .toEqual([...PERIODOS.map(([id]) => id), 'trimestre', 'anio'])
  })
})
