/*
 * Routes config — fuente única de verdad de la IA del dashboard.
 * Consumida por App (Routes), Sidebar/MobileNav (nav) y CommandPalette (búsqueda).
 * El gating por empresa lo decide `isRouteEnabled(path, features)` con las features de GET /config.
 */
import {
  LayoutDashboard, ShoppingCart, Wallet, Package,
  Users, Truck, Building2, Receipt,
  History, TrendingUp, Trophy, BookOpen,
  FileText, FileCheck, Calculator, FileCog,
} from 'lucide-react'
import { isRouteEnabled } from './lib/features.jsx'

export const ROUTES = [
  // Hoy — top-level, sin grupo
  { path: '/hoy',                 label: 'Hoy',                 icon: LayoutDashboard, group: 'top' },

  // Operación
  { path: '/ventas',              label: 'Ventas Rápidas',      icon: ShoppingCart,    group: 'operacion' },
  { path: '/caja',                label: 'Caja',                icon: Wallet,          group: 'operacion' },
  { path: '/inventario',          label: 'Inventario',          icon: Package,         group: 'operacion' },

  // Gestión
  { path: '/clientes',            label: 'Clientes',            icon: Users,           group: 'gestion' },
  { path: '/compras',             label: 'Compras',             icon: Truck,           group: 'gestion' },
  { path: '/proveedores',         label: 'Proveedores',         icon: Building2,       group: 'gestion' },
  { path: '/gastos',              label: 'Gastos',              icon: Receipt,         group: 'gestion' },

  // Reportes
  { path: '/historial',           label: 'Historial',           icon: History,         group: 'reportes' },
  { path: '/resultados',          label: 'Resultados financieros', icon: TrendingUp,   group: 'reportes' },
  { path: '/top-productos',       label: 'Top productos',       icon: Trophy,          group: 'reportes' },
  { path: '/kardex',              label: 'Kárdex',              icon: BookOpen,        group: 'reportes' },

  // Fiscal
  { path: '/facturacion',         label: 'Facturación',         icon: FileText,        group: 'fiscal' },
  { path: '/facturas-recibidas',  label: 'Facturas recibidas',  icon: FileCheck,       group: 'fiscal' },
  { path: '/libro-iva',           label: 'Libro IVA',           icon: Calculator,      group: 'fiscal' },
  { path: '/compras-fiscal',      label: 'Compras Fiscal',      icon: FileCog,         group: 'fiscal' },
]

export const GROUPS = [
  { id: 'operacion', label: 'Operación', collapsedByDefault: false },
  { id: 'gestion',   label: 'Gestión',   collapsedByDefault: false },
  { id: 'reportes',  label: 'Reportes',  collapsedByDefault: false },
  { id: 'fiscal',    label: 'Fiscal',    collapsedByDefault: true  },
]

export function routesByGroup(groupId, features = []) {
  return ROUTES.filter(r => r.group === groupId && isRouteEnabled(r.path, features))
}

export function findRoute(path) {
  return ROUTES.find(r => r.path === path)
}
