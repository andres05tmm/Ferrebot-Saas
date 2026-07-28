/*
 * Los valores esperados salen de la captura del dashboard viejo: es el formato que el dueño ya sabe
 * leer, así que el test lo fija contra el original y no contra mi criterio.
 */
import { describe, expect, it } from 'vitest'
import { montoCorto } from './montoCorto.js'

describe('montoCorto', () => {
  it('replica el formato del dashboard viejo', () => {
    expect(montoCorto(1030500)).toBe('1,03M')
    expect(montoCorto(1471500)).toBe('1,47M')
    expect(montoCorto(899000)).toBe('899k')
    expect(montoCorto(733025)).toBe('733k')
    expect(montoCorto(80000)).toBe('80,0k')
    expect(montoCorto(13000)).toBe('13,0k')
  })

  it('un día sin ventas muestra una raya, no "$0"', () => {
    // Veinte celdas con "$0" son ruido; la raya se lee como "acá no pasó nada".
    expect(montoCorto(0)).toBe('—')
    expect(montoCorto(null)).toBe('—')
    expect(montoCorto(undefined)).toBe('—')
  })

  it('los montos chicos se muestran completos', () => {
    expect(montoCorto(500)).toBe('500')
  })
})
