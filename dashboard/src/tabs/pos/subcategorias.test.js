import { describe, expect, it } from 'vitest'
import { filtrarSubcat, ordenarProductos, subcatsDe } from './subcategorias.js'

const p = (nombre, precio = '1000') => ({ nombre, precio_venta: precio })

describe('subcatsDe — reglas del FerreBot viejo por categoría', () => {
  it('ferretería, pinturas y tornillería tienen subcategorías; otras no', () => {
    expect(subcatsDe('1 Artículos de Ferreteria').map(s => s.label)).toEqual([
      'Brochas / Rodillos', 'Lijas', 'Cintas', 'Cerraduras', 'Brocas / Discos', 'Herramientas', 'Varios',
    ])
    expect(subcatsDe('2 Pinturas y Disolventes').length).toBe(8)
    expect(subcatsDe('3 Tornilleria').length).toBe(8)
    expect(subcatsDe('5 Materiales Electricos')).toEqual([])
  })
})

describe('filtrarSubcat — mismos predicados del viejo', () => {
  const ferr = subcatsDe('1 Artículos de Ferreteria')
  const items = [
    p('Brocha de 2"'), p('Rodillo Felpa'), p('Lija Esmeril N°60'), p('Cinta Enmascarar'),
    p('Cerradura de Alcoba'), p('Broca 1/4'), p('Martillo uña'), p('ACCESORIO SANITARIO'),
  ]

  it('cada subcategoría atrapa lo suyo (tilde-insensible)', () => {
    expect(filtrarSubcat(items, ferr, 'ferr_brochas').map(i => i.nombre)).toEqual(['Brocha de 2"', 'Rodillo Felpa'])
    expect(filtrarSubcat(items, ferr, 'ferr_lijas').map(i => i.nombre)).toEqual(['Lija Esmeril N°60'])
    expect(filtrarSubcat(items, ferr, 'ferr_cerraduras').map(i => i.nombre)).toEqual(['Cerradura de Alcoba'])
    expect(filtrarSubcat(items, ferr, 'ferr_herr').map(i => i.nombre)).toEqual(['Martillo uña'])
  })

  it('"Varios" es el resto: lo que ninguna hermana atrapó', () => {
    expect(filtrarSubcat(items, ferr, 'ferr_varios').map(i => i.nombre)).toEqual(['ACCESORIO SANITARIO'])
  })

  it('tornillería: drywall por calibre (6x/8x/10x sin espacios) y puntillas', () => {
    const torn = subcatsDe('3 Tornilleria')
    const tt = [p('TORNILLO DRYWALL 6X1'), p('TORNILLO DRYWALL 8 X 2'), p('Puntilla 2"'), p('Chazo plástico')]
    expect(filtrarSubcat(tt, torn, 'torn_dry6').map(i => i.nombre)).toEqual(['TORNILLO DRYWALL 6X1'])
    expect(filtrarSubcat(tt, torn, 'torn_dry8').map(i => i.nombre)).toEqual(['TORNILLO DRYWALL 8 X 2'])
    expect(filtrarSubcat(tt, torn, 'torn_puntillas').map(i => i.nombre)).toEqual(['Puntilla 2"'])
    expect(filtrarSubcat(tt, torn, 'torn_arandelas').map(i => i.nombre)).toEqual(['Chazo plástico'])
  })
})

describe('ordenarProductos — el orden de tornillería del viejo', () => {
  it('drywall primero (×6→×8→×10, por largo), el resto por precio; otras categorías intactas', () => {
    const items = [
      p('Grapa', '500'), p('TORNILLO DRYWALL 10X2', '90'), p('TORNILLO DRYWALL 6X2', '60'),
      p('TORNILLO DRYWALL 6X1', '50'), p('Puntilla', '200'),
    ]
    expect(ordenarProductos('3 Tornilleria', items).map(i => i.nombre)).toEqual([
      'TORNILLO DRYWALL 6X1', 'TORNILLO DRYWALL 6X2', 'TORNILLO DRYWALL 10X2', 'Puntilla', 'Grapa',
    ])
    expect(ordenarProductos('2 Pinturas y Disolventes', items)).toEqual(items)
  })
})
