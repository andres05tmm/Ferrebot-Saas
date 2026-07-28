/*
 * El parser de la lista que el dueño pega. El material real es sucio: sale de copiar la tabla del
 * dashboard viejo o de pasar un cuaderno a máquina. Si el parser fuera estricto, nadie cargaría nada.
 */
import { describe, expect, it } from 'vitest'
import { parsearDiasPegados, parsearFecha, parsearMonto } from './pegarDias.js'

describe('parsearMonto', () => {
  it('el punto colombiano es separador de miles, no decimal', () => {
    // Lo más caro de equivocar: "1.030.500" son un millón de pesos, no uno con treinta.
    expect(parsearMonto('1.030.500')).toBe(1030500)
    expect(parsearMonto('$1.030.500')).toBe(1030500)
    expect(parsearMonto('898500')).toBe(898500)
  })

  it('una coma con dos dígitos al final sí son centavos', () => {
    expect(parsearMonto('1.030,50')).toBe(1030.5)
  })

  it('lo que no es un monto devuelve null en vez de un cero silencioso', () => {
    expect(parsearMonto('—')).toBeNull()
    expect(parsearMonto('abc')).toBeNull()
    expect(parsearMonto('')).toBeNull()
  })
})

describe('parsearFecha', () => {
  it('acepta los tres formatos que aparecen de verdad', () => {
    expect(parsearFecha('2026-07-27', 2026)).toBe('2026-07-27')
    expect(parsearFecha('27/07/2026', 2026)).toBe('2026-07-27')
    expect(parsearFecha('07/27', 2026)).toBe('2026-07-27')   // como lo imprime el dashboard viejo
  })

  it('rellena a dos dígitos', () => {
    expect(parsearFecha('7/5', 2026)).toBe('2026-07-05')
  })
})

describe('parsearDiasPegados', () => {
  it('lee la tabla del dashboard viejo pegada tal cual', () => {
    const pegado = `07/27\t$602.500
07/24\t$283.000
07/23\t$282.000`
    const { dias, errores } = parsearDiasPegados(pegado, 2026)
    expect(errores).toEqual([])
    expect(dias).toEqual([
      { fecha: '2026-07-27', total: 602500 },
      { fecha: '2026-07-24', total: 283000 },
      { fecha: '2026-07-23', total: 282000 },
    ])
  })

  it('aguanta espacios múltiples y líneas en blanco', () => {
    const { dias } = parsearDiasPegados('  2026-07-01     1.030.500  \n\n2026-07-02   898.500\n', 2026)
    expect(dias).toHaveLength(2)
    expect(dias[1]).toEqual({ fecha: '2026-07-02', total: 898500 })
  })

  it('lo que no se entiende vuelve como error, no se descarta callado', () => {
    // Descartar en silencio sería perder plata del reporte sin que nadie se entere.
    const { dias, errores } = parsearDiasPegados('2026-07-01\t1.000\nbasura\n2026-07-02\t—', 2026)
    expect(dias).toHaveLength(1)
    expect(errores).toHaveLength(2)
    expect(errores[0].linea).toBe(2)
    expect(errores[1].motivo).toMatch(/total/)
  })

  it('una fecha repetida se avisa en vez de dejar que la última gane en silencio', () => {
    const { dias, errores } = parsearDiasPegados('2026-07-01\t1.000\n2026-07-01\t2.000', 2026)
    expect(dias).toHaveLength(1)
    expect(errores[0].motivo).toBe('fecha repetida')
  })
})
