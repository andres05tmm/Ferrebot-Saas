/*
 * features.test.jsx — gating del pack `pos` (ADR 0008 / Fase A2).
 *
 * El POS dejó de ser núcleo: sus rutas se gatean por `pos`. Un negocio de servicios (agenda/faq/
 * whatsapp, SIN pos) NO debe ver ningún tab POS; un tenant con `pos` (Punto Rojo) los sigue viendo.
 */
import { describe, it, expect } from 'vitest'
import { isRouteEnabled, RUTA_FEATURE, resolveHomePath, esAtencionCliente } from './features.jsx'
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

// ── ADR 0018 — dos familias de dashboard (gating por vertical) ───────────────────────────────────
// El flag `pos` no basta: packs de servicio reusan el catálogo POS y arrastran `pos` por dependencia.
// `esAtencionCliente` discrimina la familia; un restaurante (pos + pack_pedidos) NO ve el retail.
describe('dos familias de dashboard (ADR 0018)', () => {
  // Rutas retail/contables que SOLO debe ver la familia ferretería.
  const RETAIL = ['/hoy', '/ventas', '/caja', '/inventario', '/compras', '/proveedores', '/gastos',
    '/top-productos', '/kardex', '/historial']
  const NUCLEO = ['/clientes', '/resultados']

  const FERRETERIA = ['pos']
  const RESTAURANTE = ['pos', 'pack_pedidos', 'pack_faq', 'canal_whatsapp']
  const BARBERIA = ['pack_agenda', 'pack_faq', 'canal_whatsapp']
  const HOTEL = ['pack_agenda', 'pack_reservas', 'pack_faq', 'canal_whatsapp']

  it('esAtencionCliente: true con packs de servicio, false en ferretería pura', () => {
    expect(esAtencionCliente(FERRETERIA)).toBe(false)
    expect(esAtencionCliente(RESTAURANTE)).toBe(true)
    expect(esAtencionCliente(BARBERIA)).toBe(true)
    expect(esAtencionCliente(HOTEL)).toBe(true)
    expect(esAtencionCliente([])).toBe(false)
  })

  it('ferretería (pos): home /hoy, ve retail, NO ve /pedidos', () => {
    expect(resolveHomePath(FERRETERIA)).toBe('/hoy')
    expect(isRouteEnabled('/caja', FERRETERIA)).toBe(true)
    expect(isRouteEnabled('/compras', FERRETERIA)).toBe(true)
    expect(isRouteEnabled('/pedidos', FERRETERIA)).toBe(false)
  })

  it('restaurante (pos + pack_pedidos): home /pedidos, NO ve retail, sí su vertical', () => {
    expect(resolveHomePath(RESTAURANTE)).toBe('/pedidos')
    for (const ruta of ['/hoy', '/caja', '/compras', '/inventario', '/gastos', '/ventas']) {
      expect(isRouteEnabled(ruta, RESTAURANTE)).toBe(false)
    }
    expect(isRouteEnabled('/pedidos', RESTAURANTE)).toBe(true)
    expect(isRouteEnabled('/conocimiento', RESTAURANTE)).toBe(true)
    expect(isRouteEnabled('/clientes', RESTAURANTE)).toBe(true)
  })

  it('barbería (pack_agenda): home /inicio, ve /agenda, NO ve retail', () => {
    expect(resolveHomePath(BARBERIA)).toBe('/inicio')
    expect(isRouteEnabled('/agenda', BARBERIA)).toBe(true)
    for (const ruta of RETAIL) {
      expect(isRouteEnabled(ruta, BARBERIA)).toBe(false)
    }
  })

  it('hotel (pack_agenda + pack_reservas): home /inicio', () => {
    expect(resolveHomePath(HOTEL)).toBe('/inicio')
    for (const ruta of RETAIL) {
      expect(isRouteEnabled(ruta, HOTEL)).toBe(false)
    }
  })

  it('el núcleo (/clientes, /resultados) es visible en TODA familia', () => {
    for (const features of [FERRETERIA, RESTAURANTE, BARBERIA, HOTEL, []]) {
      for (const ruta of NUCLEO) {
        expect(isRouteEnabled(ruta, features)).toBe(true)
      }
    }
  })
})
