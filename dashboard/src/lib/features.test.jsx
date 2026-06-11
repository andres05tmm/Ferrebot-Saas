/*
 * features.test.jsx — gating del pack `pos` (ADR 0008 / Fase A2).
 *
 * El POS dejó de ser núcleo: sus rutas se gatean por `pos`. Un negocio de servicios (agenda/faq/
 * whatsapp, SIN pos) NO debe ver ningún tab POS; un tenant con `pos` (Punto Rojo) los sigue viendo.
 */
import { describe, it, expect } from 'vitest'
import { isRouteEnabled, RUTA_FEATURE, resolveHomePath } from './features.jsx'
import { ROUTES, routesByGroup, GROUPS } from '../routes.jsx'

const RUTAS_POS = ['/ventas', '/caja', '/inventario', '/compras', '/proveedores', '/gastos',
  '/top-productos', '/kardex', '/historial']
// Tras Fase 1 la portada `/hoy` también se gatea por `pos`; el núcleo transversal queda en estas dos.
const RUTAS_NUCLEO = ['/clientes', '/resultados']

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

  it('las rutas núcleo (Clientes, Resultados) están visibles aun sin features', () => {
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

describe('resolución de la home por features (Fase 1)', () => {
  it('con `pos` la portada es /hoy (cockpit POS intacto)', () => {
    expect(resolveHomePath(POS)).toBe('/hoy')
    expect(resolveHomePath(['pos'])).toBe('/hoy')
    // …y con pos, /hoy se ve pero /inicio no (portadas excluyentes).
    expect(isRouteEnabled('/hoy', POS)).toBe(true)
    expect(isRouteEnabled('/inicio', POS)).toBe(false)
  })

  it('un negocio de servicios (sin pos) aterriza en /inicio', () => {
    expect(resolveHomePath(SERVICIOS)).toBe('/inicio')
    // Basta agenda o whatsapp; incluso sin packs, el núcleo de servicio llega a /inicio.
    expect(resolveHomePath(['pack_agenda'])).toBe('/inicio')
    expect(resolveHomePath(['canal_whatsapp'])).toBe('/inicio')
    expect(resolveHomePath([])).toBe('/inicio')
  })

  it('las portadas /hoy y /inicio son mutuamente excluyentes en el nav', () => {
    // Servicios: ve /inicio, NO /hoy.
    expect(isRouteEnabled('/inicio', SERVICIOS)).toBe(true)
    expect(isRouteEnabled('/hoy', SERVICIOS)).toBe(false)
    // POS: ve /hoy, NO /inicio.
    expect(isRouteEnabled('/inicio', POS)).toBe(false)
    expect(isRouteEnabled('/hoy', POS)).toBe(true)
  })

  it('solo una portada (top) queda visible según el tenant', () => {
    const topDe = (features) => ROUTES.filter(r => r.group === 'top' && isRouteEnabled(r.path, features)).map(r => r.path)
    expect(topDe(SERVICIOS)).toEqual(['/inicio'])
    expect(topDe(POS)).toEqual(['/hoy'])
  })
})
