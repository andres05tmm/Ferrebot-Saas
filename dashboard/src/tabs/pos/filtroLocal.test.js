import { describe, expect, it } from 'vitest'
import { filtrarYRankear, normalizarLocal } from './filtroLocal.js'

const CATALOGO = [
  { id: 1, nombre: 'Vinilo azul tipo 1', codigo: 'V-100', categoria: 'Pinturas' },
  { id: 2, nombre: 'Vinilo rojo tipo 1', codigo: 'V-101', categoria: 'Pinturas' },
  { id: 3, nombre: 'Estuco plástico x 25kg', codigo: 'E-01', categoria: 'Pinturas' },
  { id: 4, nombre: 'Martillo uña 16oz', codigo: '7701234', categoria: 'Herramientas' },
  { id: 5, nombre: 'Azulejo baño blanco', codigo: null, categoria: 'Construcción' },
]

describe('normalizarLocal — espejo de normalizar() del backend', () => {
  it('baja a minúsculas, quita tildes y ñ, colapsa espacios', () => {
    expect(normalizarLocal('  CAFÉ   Ñoño ')).toBe('cafe nono')
    expect(normalizarLocal('Vinílo  AZUL')).toBe('vinilo azul')
    expect(normalizarLocal(null)).toBe('')
  })
})

describe('filtrarYRankear', () => {
  it('sin término devuelve vacío (la grilla muestra el chip activo, no una búsqueda)', () => {
    expect(filtrarYRankear(CATALOGO, '')).toEqual([])
    expect(filtrarYRankear(CATALOGO, '   ')).toEqual([])
  })

  it('código exacto gana sobre cualquier match de nombre', () => {
    const r = filtrarYRankear(CATALOGO, 'V-100')
    expect(r[0].id).toBe(1)
  })

  it('startsWith de nombre gana sobre palabras sueltas; tilde-insensible', () => {
    const r = filtrarYRankear(CATALOGO, 'vinílo')
    expect(r.map(p => p.id)).toEqual([1, 2])   // los dos vinilos, alfabético (azul < rojo)
  })

  it('toda palabra del query debe prefijar alguna palabra del nombre (multi-palabra)', () => {
    const r = filtrarYRankear(CATALOGO, 'vin roj')
    expect(r.map(p => p.id)).toEqual([2])
  })

  it('el nivel includes atrapa por categoría/código; 0 matches → lista vacía', () => {
    expect(filtrarYRankear(CATALOGO, 'pinturas').length).toBe(3)
    expect(filtrarYRankear(CATALOGO, 'tiner')).toEqual([])
  })

  it('"azul" no confunde Vinilo azul con Azulejo por ranking (prefijo de palabra primero)', () => {
    const r = filtrarYRankear(CATALOGO, 'azul')
    // Azulejo (startsWith del nombre) rankea 1; vinilo azul rankea 2 — ambos aparecen.
    expect(r.map(p => p.id)).toEqual([5, 1])
  })
})
