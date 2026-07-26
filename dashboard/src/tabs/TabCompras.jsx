/*
 * TabCompras — un solo lugar para todo lo que se le compra al proveedor.
 *
 * El flujo del negocio (reforma 2026-07): la compra se registra AL HACER EL PEDIDO, con sus
 * productos, cantidades y costo unitario, y con la forma de pago. La mercancía todavía no entra al
 * inventario: entra cuando se marca "Llegó", y de ahí en adelante se puede corregir si se digitó
 * algo mal. El cronómetro mide cuánto tarda cada proveedor.
 *
 * Qué se ve según la empresa:
 *   - con la feature `pedidos_proveedor`: el ciclo completo (PanelPedidos).
 *   - sin ella: el registro directo de siempre (PanelObra), que también es el flujo del vertical
 *     construcción — compras imputadas a obra y viajes de material NO son pedidos de mercancía (no
 *     mueven inventario, llevan resbalo), así que conviven como su propia sección.
 */
import { useState } from 'react'
import { useAuth } from '@/hooks/useAuth.js'
import { esConstruccion, useFeatures } from '@/lib/features.jsx'
import PanelObra from './compras/PanelObra.jsx'
import PanelPedidos from './compras/PanelPedidos.jsx'

const SECCIONES = [
  { id: 'pedidos', label: 'A proveedor' },
  { id: 'obra', label: 'De obra / viaje' },
]

export default function TabCompras() {
  const features = useFeatures()
  const { isAdmin } = useAuth()
  const construccion = esConstruccion(features)
  const conCiclo = features.includes('pedidos_proveedor')
  const [seccion, setSeccion] = useState('pedidos')

  // Sin el ciclo de pedidos, el tab se ve exactamente como antes de la reforma.
  if (!conCiclo) return <PanelObra />

  // En construcción conviven las dos naturalezas de compra: se eligen con un segmentado.
  if (construccion) {
    return (
      <div className="space-y-3">
        <div className="flex gap-2">
          {SECCIONES.map(s => (
            <button key={s.id} onClick={() => setSeccion(s.id)} aria-pressed={seccion === s.id}
              className={`px-2.5 py-1 rounded-md border text-body-sm ${
                seccion === s.id ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
              {s.label}
            </button>
          ))}
        </div>
        {seccion === 'pedidos' ? <PanelPedidos esAdmin={isAdmin()} /> : <PanelObra />}
      </div>
    )
  }

  return <PanelPedidos esAdmin={isAdmin()} />
}
