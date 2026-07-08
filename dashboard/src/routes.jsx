/*
 * Routes config — fuente única de verdad de la IA del dashboard.
 * Consumida por App (Routes), Sidebar/MobileNav (nav) y CommandPalette (búsqueda).
 * El gating por empresa lo decide `isRouteEnabled(path, features)` con las features de GET /config.
 */
import {
  LayoutDashboard, Home, ShoppingCart, Wallet, Package,
  Users, Truck, Building2, Receipt,
  History, TrendingUp, Trophy, BookOpen,
  FileText, FileCheck, Calculator, FileCog,
  CalendarDays, Headset, BookText, HandCoins, ChefHat, Banknote,
  CreditCard, FileSpreadsheet, Star, BedDouble, Undo2, Percent, Library, Landmark, Scale,
  HardHat, Wrench, ClipboardList, TrendingDown, Gauge,
} from 'lucide-react'
import { isRouteEnabled } from './lib/features.jsx'

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
  { path: '/caja',                label: 'Caja',                icon: Wallet,          group: 'operacion' },
  { path: '/inventario',          label: 'Inventario',          icon: Package,         group: 'operacion' },
  { path: '/agenda',              label: 'Agenda',              icon: CalendarDays,    group: 'operacion' },
  { path: '/reservas',            label: 'Reservas',            icon: BedDouble,       group: 'operacion' },
  { path: '/pedidos',             label: 'Pedidos',             icon: ChefHat,         group: 'operacion' },
  { path: '/cotizaciones',        label: 'Cotizaciones',        icon: FileSpreadsheet, group: 'operacion' },
  { path: '/postventa',           label: 'Postventa',           icon: Star,            group: 'operacion' },
  { path: '/conversaciones',      label: 'Conversaciones',      icon: Headset,         group: 'operacion' },
  { path: '/conocimiento',        label: 'Conocimiento',        icon: BookText,        group: 'operacion' },

  // Construcción (vertical PIM) — gated por sus flags finas (cotizaciones_aiu/obras/maquinaria/herramientas/nomina).
  { path: '/cotizaciones-obra',   label: 'Cotizaciones AIU',    icon: ClipboardList,   group: 'construccion' },
  { path: '/obras',               label: 'Obras',               icon: HardHat,         group: 'construccion' },
  { path: '/maquinas',            label: 'Maquinaria',          icon: Truck,           group: 'construccion' },
  { path: '/herramientas',        label: 'Herramientas',        icon: Wrench,          group: 'construccion' },
  { path: '/trabajadores',        label: 'Trabajadores',        icon: Users,           group: 'construccion' },
  { path: '/nomina',              label: 'Nómina',              icon: Wallet,          group: 'construccion' },
  { path: '/resbalos',            label: 'Resbalos y precios',  icon: TrendingDown,    group: 'construccion' },

  // Gestión
  { path: '/clientes',            label: 'Clientes',            icon: Users,           group: 'gestion' },
  { path: '/cartera',             label: 'Cartera',             icon: HandCoins,       group: 'gestion' },
  { path: '/cobros',              label: 'Cobros',              icon: CreditCard,      group: 'gestion' },
  { path: '/compras',             label: 'Compras',             icon: Truck,           group: 'gestion' },
  { path: '/proveedores',         label: 'Proveedores',         icon: Building2,       group: 'gestion' },
  { path: '/cuentas-por-pagar',   label: 'Cuentas por pagar',   icon: Banknote,        group: 'gestion' },
  { path: '/gastos',              label: 'Gastos',              icon: Receipt,         group: 'gestion' },
  { path: '/conciliacion',        label: 'Conciliación',        icon: Landmark,        group: 'gestion' },

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

export function routesByGroup(groupId, features = []) {
  return ROUTES.filter(r => r.group === groupId && isRouteEnabled(r.path, features))
}

export function findRoute(path) {
  return ROUTES.find(r => r.path === path)
}
