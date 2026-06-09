/*
 * features.test.jsx — gating del pack `pos` (ADR 0008 / Fase A2).
 *
 * El POS dejó de ser núcleo: sus rutas se gatean por `pos`. Un negocio de servicios (agenda/faq/
 * whatsapp, SIN pos) NO debe ver ningún tab POS; un tenant con `pos` (Punto Rojo) los sigue viendo.
 */
import { describe, it, expect } from 'vitest'
import { isRouteEnabled, RUTA_FEATURE } from './features.jsx'
import { ROUTES, routesByGroup, GROUPS } from '../routes.jsx'

const RUTAS_POS = ['/ventas', '/caja', '/inventario', '/compras', '/proveedores', '/gastos',
  '/top-productos', '/kardex', '/historial']
const RUTAS_NUCLEO = ['/hoy', '/clientes', '/resultados']

// Features de un negocio de SERVICIOS (clinica-demo): sin `pos`.
const SERVICIOS = ['pack_agenda', 'pack_faq', 'canal_whatsapp']
// Features de Punto Rojo (ferretería) con el grandfather aplicado.
const POS = ['pos', 'facturacion_electronica']

describe('gating del pack pos', () => {
  it('todas las rutas POS están gateadas por la capacidad `pos`', () => {
    for (const ruta of RUTAS_POS) {
      expect(RUTA_FEATURE[ruta]).toBe('pos')
    }
  })

  it('las rutas POS están OCULTAS sin la capacidad `pos`', () => {
    for (const ruta of RUTAS_POS) {
      expect(isRouteEnabled(ruta, [])).toBe(false)
      expect(isRouteEnabled(ruta, SERVICIOS)).toBe(false)
    }
  })

  it('las rutas POS están VISIBLES con la capacidad `pos`', () => {
    for (const ruta of RUTAS_POS) {
      expect(isRouteEnabled(ruta, POS)).toBe(true)
    }
  })

  it('las rutas núcleo (Hoy, Clientes, Resultados) están visibles aun sin features', () => {
    for (const ruta of RUTAS_NUCLEO) {
      expect(isRouteEnabled(ruta, [])).toBe(true)
      expect(isRouteEnabled(ruta, SERVICIOS)).toBe(true)
    }
  })

  it('un negocio de servicios NO ve ningún tab POS en el menú (ningún grupo)', () => {
    const visibles = GROUPS.flatMap(g => routesByGroup(g.id, SERVICIOS)).map(r => r.path)
    for (const ruta of RUTAS_POS) {
      expect(visibles).not.toContain(ruta)
    }
    // Sí ve sus packs de servicios.
    expect(visibles).toContain('/agenda')
    expect(visibles).toContain('/conversaciones')
    expect(visibles).toContain('/conocimiento')
  })

  it('no-regresión: un tenant con `pos` (Punto Rojo) SIGUE viendo Ventas/Inventario/Caja/etc.', () => {
    const visibles = GROUPS.flatMap(g => routesByGroup(g.id, POS)).map(r => r.path)
    for (const ruta of RUTAS_POS) {
      expect(visibles).toContain(ruta)
    }
    // Pero NO ve los packs de servicios que no tiene.
    expect(visibles).not.toContain('/agenda')
  })
})
