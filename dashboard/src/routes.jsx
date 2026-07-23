/*
 * Routes config — fuente única de verdad de la IA del dashboard.
 * Consumida por App (Routes), Sidebar/MobileNav (nav) y CommandPalette (búsqueda).
 * El gating por empresa lo decide `isRouteEnabled(path, features)` con las features de GET /config.
 *
 * DOS familias de navegación (F2.1): la familia CONSTRUCCIÓN agrupa por flujo de trabajo (patrón
 * Procore: Obra / Comercial / Recursos / Materiales / Plata) vía `grupoObra`/`labelObra` opcionales
 * por ruta y `groupsFor(features)`; el resto de familias conserva los GROUPS de siempre. El gating
 * por feature (features.jsx) es ortogonal y no cambia.
 */
import {
  LayoutDashboard, Home, ShoppingCart, Wallet, Package,
  Users, Truck, Building2, Receipt,
  History, TrendingUp, Trophy, BookOpen,
  FileText, FileCheck, Calculator, FileCog,
  CalendarDays, Headset, BookText, HandCoins, ChefHat, Banknote,
  CreditCard, FileSpreadsheet, Star, BedDouble, Undo2, Percent, Library, Landmark, Scale,
  HardHat, Wrench, ClipboardList, TrendingDown, Gauge, PackageSearch, Timer, Armchair, Flame, QrCode,
} from 'lucide-react'
import { esConstruccion, isRouteEnabled } from './lib/features.jsx'

export const ROUTES = [
  // Portadas — top-level, sin grupo. Mutuamente excluyentes: `/inicio` (agente de servicios) y `/hoy`
  // (cockpit POS). El gating (isRouteEnabled/resolveHomePath) deja visible solo la del tenant.
  { path: '/inicio',              label: 'Inicio',              icon: Home,            group: 'top' },
  { path: '/hoy',                 label: 'Hoy',                 icon: LayoutDashboard, group: 'top' },
  // Portada de la familia CONSTRUCCIÓN (vertical PIM): cockpit del dueño (KPIs del mes, obras por
  // riesgo, máquinas, alertas). Solo visible para construcción (isRouteEnabled) y admin (guardia en
  // el propio panel + resolveHomePath por rol). `/obras` sigue siendo la vista operativa CRUD.
  { path: '/panel',               label: 'Panel',               icon: Gauge,           group: 'top' },

  // Operación
  { path: '/ventas',              label: 'Ventas Rápidas',      icon: ShoppingCart,    group: 'operacion' },
  { path: '/devoluciones',        label: 'Devoluciones',        icon: Undo2,           group: 'operacion' },
  { path: '/caja',                label: 'Caja',                icon: Wallet,          group: 'operacion', grupoObra: 'plata',      labelObra: 'Caja menor', ordenObra: 3 },
  { path: '/inventario',          label: 'Inventario',          icon: Package,         group: 'operacion', grupoObra: 'materiales', labelObra: 'Materiales' },
  { path: '/agenda',              label: 'Agenda',              icon: CalendarDays,    group: 'operacion' },
  { path: '/reservas',            label: 'Reservas',            icon: BedDouble,       group: 'operacion' },
  { path: '/pedidos',             label: 'Pedidos',             icon: ChefHat,         group: 'operacion' },
  { path: '/mesas',               label: 'Mesas',               icon: Armchair,        group: 'operacion' },
  { path: '/kds',                 label: 'Cocina',              icon: Flame,           group: 'operacion' },
  { path: '/menu-qr',             label: 'Menú QR',             icon: QrCode,          group: 'operacion' },
  { path: '/cotizaciones',        label: 'Cotizaciones',        icon: FileSpreadsheet, group: 'operacion' },
  { path: '/postventa',           label: 'Postventa',           icon: Star,            group: 'operacion' },
  { path: '/conversaciones',      label: 'Conversaciones',      icon: Headset,         group: 'operacion' },
  { path: '/conocimiento',        label: 'Conocimiento',        icon: BookText,        group: 'operacion' },

  // Construcción (vertical PIM) — gated por sus flags finas (cotizaciones_aiu/obras/maquinaria/herramientas/nomina).
  // En la familia construcción se reparten por flujo (grupoObra); "AIU" cae del label (jerga interna).
  { path: '/cotizaciones-obra',   label: 'Cotizaciones AIU',    icon: ClipboardList,   group: 'construccion', grupoObra: 'comercial', labelObra: 'Cotizaciones', ordenObra: 1 },
  { path: '/obras',               label: 'Obras',               icon: HardHat,         group: 'construccion', grupoObra: 'obra' },
  { path: '/calendario',          label: 'Calendario',          icon: CalendarDays,    group: 'construccion', grupoObra: 'obra' },
  { path: '/maquinas',            label: 'Maquinaria',          icon: Truck,           group: 'construccion', grupoObra: 'recursos' },
  { path: '/operacion',           label: 'Operación',           icon: Timer,           group: 'construccion', grupoObra: 'obra',      labelObra: 'Operación en vivo' },
  { path: '/herramientas',        label: 'Herramientas',        icon: Wrench,          group: 'construccion', grupoObra: 'recursos' },
  { path: '/trabajadores',        label: 'Trabajadores',        icon: Users,           group: 'construccion', grupoObra: 'recursos' },
  { path: '/nomina',              label: 'Nómina',              icon: Wallet,          group: 'construccion', grupoObra: 'recursos' },
  { path: '/resbalos',            label: 'Resbalos y precios',  icon: TrendingDown,    group: 'construccion', grupoObra: 'comercial', ordenObra: 3 },

  // Gestión
  { path: '/clientes',            label: 'Clientes',            icon: Users,           group: 'gestion', grupoObra: 'comercial', ordenObra: 2 },
  { path: '/cartera',             label: 'Cartera',             icon: HandCoins,       group: 'gestion', grupoObra: 'plata', ordenObra: 1 },
  { path: '/cobros',              label: 'Cobros',              icon: CreditCard,      group: 'gestion', grupoObra: 'plata' },
  { path: '/compras',             label: 'Compras',             icon: Truck,           group: 'gestion', grupoObra: 'materiales', labelObra: 'Compras de obra' },
  { path: '/pedidos-proveedor',   label: 'Pedidos a proveedor', icon: PackageSearch,   group: 'gestion', grupoObra: 'materiales' },
  { path: '/proveedores',         label: 'Proveedores',         icon: Building2,       group: 'gestion', grupoObra: 'materiales' },
  { path: '/cuentas-por-pagar',   label: 'Cuentas por pagar',   icon: Banknote,        group: 'gestion', grupoObra: 'plata' },
  { path: '/gastos',              label: 'Gastos',              icon: Receipt,         group: 'gestion', grupoObra: 'plata', labelObra: 'Gastos de obra', ordenObra: 2 },
  { path: '/conciliacion',        label: 'Conciliación',        icon: Landmark,        group: 'gestion', grupoObra: 'plata' },

  // Reportes
  { path: '/historial',           label: 'Historial',           icon: History,         group: 'reportes' },
  { path: '/resultados',          label: 'Resultados financieros', icon: TrendingUp,   group: 'reportes' },
  { path: '/top-productos',       label: 'Top productos',       icon: Trophy,          group: 'reportes' },
  { path: '/kardex',              label: 'Kárdex',              icon: BookOpen,        group: 'reportes' },

  // Fiscal
  { path: '/facturacion',         label: 'Facturación',         icon: FileText,        group: 'fiscal' },
  { path: '/facturas-recibidas',  label: 'Facturas recibidas',  icon: FileCheck,       group: 'fiscal' },
  { path: '/libro-iva',           label: 'Libro IVA',           icon: Calculator,      group: 'fiscal' },
  { path: '/libros',              label: 'Libros contables',    icon: Library,         group: 'fiscal' },
  { path: '/estados-financieros', label: 'Estados financieros', icon: Scale,           group: 'fiscal' },
  { path: '/retenciones',         label: 'Retenciones',         icon: Percent,         group: 'fiscal' },
  { path: '/compras-fiscal',      label: 'Compras Fiscal',      icon: FileCog,         group: 'fiscal' },
]

export const GROUPS = [
  { id: 'operacion',    label: 'Operación',    collapsedByDefault: false },
  { id: 'construccion', label: 'Construcción', collapsedByDefault: false },
  { id: 'gestion',      label: 'Gestión',      collapsedByDefault: false },
  { id: 'reportes',  label: 'Reportes',  collapsedByDefault: false },
  { id: 'fiscal',    label: 'Fiscal',    collapsedByDefault: true  },
]

// Grupos de la familia CONSTRUCCIÓN (F2.1): navegación por flujo de trabajo, benchmark Procore
// (Portfolio/Field/Financials/Resources) hablado en el idioma del gremio. Reportes/fiscal conservan
// su grupo (las rutas fiscales aplican si el tenant tiene esas features; vacíos se ocultan solos).
export const GROUPS_CONSTRUCCION = [
  { id: 'obra',       label: 'Obra',       collapsedByDefault: false },
  { id: 'comercial',  label: 'Comercial',  collapsedByDefault: false },
  { id: 'recursos',   label: 'Recursos',   collapsedByDefault: false },
  { id: 'materiales', label: 'Materiales', collapsedByDefault: false },
  { id: 'plata',      label: 'Plata',      collapsedByDefault: false },
  { id: 'reportes',   label: 'Reportes',   collapsedByDefault: false },
  { id: 'fiscal',     label: 'Fiscal',     collapsedByDefault: true  },
]

/** Los grupos de navegación de la familia del tenant. */
export function groupsFor(features = []) {
  return esConstruccion(features) ? GROUPS_CONSTRUCCION : GROUPS
}

// Grupo efectivo de una ruta en la familia dada. En construcción manda `grupoObra`; reportes/fiscal
// conservan su grupo; una ruta sin grupoObra no pertenece a ningún grupo de obra (el gating por
// features ya suprime las que no aplican — esto es solo el fallback coherente).
function grupoDe(route, esCons) {
  if (!esCons) return route.group
  if (route.grupoObra) return route.grupoObra
  return route.group === 'reportes' || route.group === 'fiscal' ? route.group : null
}

/** Grupo (id) al que pertenece un path para el tenant — para marcar el grupo activo en el nav móvil. */
export function groupOf(path, features = []) {
  const route = ROUTES.find(r => r.path === path)
  return route ? grupoDe(route, esConstruccion(features)) : null
}

export function routesByGroup(groupId, features = []) {
  const esCons = esConstruccion(features)
  const items = ROUTES.filter(r => grupoDe(r, esCons) === groupId && isRouteEnabled(r.path, features))
  if (!esCons) return items
  return items
    // Orden deliberado dentro del grupo de obra (`ordenObra`); sin él, el orden del array (sort estable).
    .sort((a, b) => (a.ordenObra ?? 99) - (b.ordenObra ?? 99))
    // Label efectivo resuelto aquí para que los consumidores (nav, palette) usen `item.label` sin más.
    .map(r => (r.labelObra ? { ...r, label: r.labelObra } : r))
}

export function findRoute(path) {
  return ROUTES.find(r => r.path === path)
}
