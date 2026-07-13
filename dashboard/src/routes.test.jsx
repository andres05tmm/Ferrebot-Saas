/*
 * routes.test.jsx — la IA por familia (F2.1): la familia construcción navega por flujo de trabajo
 * (Obra/Comercial/Recursos/Materiales/Plata, benchmark Procore) con labels del gremio; las demás
 * familias conservan los GROUPS de siempre con sus labels intactos.
 */
import { describe, expect, it } from 'vitest'
import { GROUPS, GROUPS_CONSTRUCCION, groupsFor, groupOf, routesByGroup } from './routes.jsx'

// PIM real: vertical construcción + pos arrastrado (caja/inventario) + fiados/cobranza + FE.
const PIM = ['construccion', 'obras', 'maquinaria', 'herramientas', 'cotizaciones_aiu', 'nomina',
  'cartera_alquiler', 'resbalos', 'pos', 'ventas', 'caja', 'inventario', 'fiados', 'pack_cobranza',
  'facturacion_electronica']
const POS = ['pos', 'facturacion_electronica']

const pathsDe = (grupo, features) => routesByGroup(grupo, features).map(r => r.path)
const labelDe = (grupo, features, path) => routesByGroup(grupo, features).find(r => r.path === path)?.label

describe('IA por familia (F2.1)', () => {
  it('groupsFor: construcción ve los grupos por flujo; el resto los GROUPS de siempre', () => {
    expect(groupsFor(PIM)).toBe(GROUPS_CONSTRUCCION)
    expect(groupsFor(POS)).toBe(GROUPS)
    expect(groupsFor([])).toBe(GROUPS)
  })

  it('OBRA agrupa el campo: obras, calendario y la operación en vivo', () => {
    expect(pathsDe('obra', PIM)).toEqual(['/obras', '/calendario', '/operacion'])
    expect(labelDe('obra', PIM, '/operacion')).toBe('Operación en vivo')
  })

  it('COMERCIAL agrupa vender: cotizaciones (sin "AIU"), clientes y precios', () => {
    expect(pathsDe('comercial', PIM)).toEqual(['/cotizaciones-obra', '/clientes', '/resbalos'])
    expect(labelDe('comercial', PIM, '/cotizaciones-obra')).toBe('Cotizaciones')
  })

  it('RECURSOS agrupa fierro + gente: maquinaria, herramientas, trabajadores, nómina', () => {
    expect(pathsDe('recursos', PIM)).toEqual(['/maquinas', '/herramientas', '/trabajadores', '/nomina'])
  })

  it('MATERIALES traduce el retail: Materiales, Compras de obra, Proveedores', () => {
    expect(pathsDe('materiales', PIM)).toEqual(['/inventario', '/compras', '/proveedores'])
    expect(labelDe('materiales', PIM, '/inventario')).toBe('Materiales')
    expect(labelDe('materiales', PIM, '/compras')).toBe('Compras de obra')
  })

  it('PLATA agrupa el dinero: cartera, gastos de obra, caja menor (solo lo habilitado)', () => {
    expect(pathsDe('plata', PIM)).toEqual(['/cartera', '/gastos', '/caja'])
    expect(labelDe('plata', PIM, '/caja')).toBe('Caja menor')
    expect(labelDe('plata', PIM, '/gastos')).toBe('Gastos de obra')
    // Sin pagos_online ni pack_pagar, /cobros y /cuentas-por-pagar no aparecen.
    expect(pathsDe('plata', PIM)).not.toContain('/cobros')
    expect(pathsDe('plata', PIM)).not.toContain('/cuentas-por-pagar')
  })

  it('los grupos viejos quedan vacíos para construcción (el nav los oculta solo)', () => {
    for (const grupo of ['operacion', 'construccion', 'gestion']) {
      expect(pathsDe(grupo, PIM)).toEqual([])
    }
  })

  it('fiscal conserva su grupo también en construcción (PIM tiene FE)', () => {
    expect(pathsDe('fiscal', PIM)).toContain('/facturacion')
  })

  it('groupOf resuelve el grupo por familia (para el activo del nav móvil)', () => {
    expect(groupOf('/gastos', PIM)).toBe('plata')
    expect(groupOf('/gastos', POS)).toBe('gestion')
    expect(groupOf('/inventario', PIM)).toBe('materiales')
    expect(groupOf('/inventario', POS)).toBe('operacion')
  })

  it('regresión Punto Rojo: el retail conserva grupos y labels de siempre', () => {
    expect(pathsDe('operacion', POS)).toContain('/caja')
    expect(labelDe('operacion', POS, '/caja')).toBe('Caja')
    expect(labelDe('operacion', POS, '/inventario')).toBe('Inventario')
    expect(pathsDe('gestion', POS)).toContain('/compras')
    expect(labelDe('gestion', POS, '/compras')).toBe('Compras')
  })
})
