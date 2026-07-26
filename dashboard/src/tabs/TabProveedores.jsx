/*
 * TabProveedores — el tab único de proveedores (absorbe "Cuentas por pagar"). SOLO admin.
 *
 * Forma maestro-detalle, como el Vendor Center de QuickBooks o el proveedor de Odoo: a la izquierda
 * TODOS los proveedores con cuánto se le debe a cada uno; a la derecha la ficha del seleccionado con
 * su estado de cuenta, pedidos, compras, facturas y pagos. Arriba, los cuatro números del conjunto.
 *
 * Lo que traía el tab viejo sigue vivo pero recolocado: registrar factura y abonar salen de la ficha
 * (ya ligados al proveedor), y el calendario de vencimientos + las reglas de aviso quedan plegados
 * abajo en `PanelAvisos`.
 */
import { useMemo, useState } from 'react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Building2, Plus, Search, Truck, Wallet } from 'lucide-react'
import { apiJson } from '@/lib/api'
import { cop, EstadoVacio, SkeletonFilas } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import ModalAbonoProveedor from '@/components/ModalAbonoProveedor.jsx'
import FichaProveedor from './proveedores/FichaProveedor.jsx'
import HuerfanasFacturas from './proveedores/HuerfanasFacturas.jsx'
import ModalFactura from './proveedores/ModalFactura.jsx'
import ModalProveedor from './proveedores/ModalProveedor.jsx'
import PanelAvisos from './proveedores/PanelAvisos.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])
const num = (v) => Number(v || 0)

export default function TabProveedores() {
  const { isAdmin } = useAuth()
  if (!isAdmin()) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        Los proveedores y sus cuentas son solo para administradores.
      </Card>
    )
  }
  return <Contenido />
}

function Contenido() {
  const { refreshKey } = useOutletContext() ?? {}
  const navigate = useNavigate()
  const [busca, setBusca] = useState('')
  const [seleccionId, setSeleccionId] = useState(null)
  const [abonoAbierto, setAbonoAbierto] = useState(false)
  const [facturaPara, setFacturaPara] = useState(null)
  const [editando, setEditando] = useState(null)   // null = cerrado · {} = alta · proveedor = edición

  const estadoQ = useQuery({
    queryKey: ['proveedores', 'estado', refreshKey],
    queryFn: () => apiJson('/proveedores/estado'),
  })
  useRealtimeEvent(['reconnected', 'pagar_aviso'], () => estadoQ.refetch())

  const proveedores = arr(estadoQ.data)
  const total = useMemo(() => ({
    deuda: proveedores.reduce((a, p) => a + num(p.saldo_pendiente), 0),
    vencido: proveedores.reduce((a, p) => a + num(p.vencido), 0),
    conDeuda: proveedores.filter(p => num(p.saldo_pendiente) > 0).length,
    enCamino: proveedores.reduce((a, p) => a + (p.pedidos_en_camino || 0), 0),
  }), [proveedores])

  const filtrados = useMemo(() => {
    const q = busca.trim().toLowerCase()
    if (!q) return proveedores
    return proveedores.filter(p => `${p.nombre} ${p.nit || ''}`.toLowerCase().includes(q))
  }, [proveedores, busca])

  // Sin selección explícita se muestra el primero de la lista (el de más deuda, que es el orden del API).
  const seleccion = proveedores.find(p => p.id === seleccionId) || filtrados[0] || null

  function recargar() { estadoQ.refetch() }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Kpi label="Se debe en total" valor={cop(total.deuda)} icon={Wallet} />
        <Kpi label="Vencido" valor={cop(total.vencido)} icon={AlertTriangle}
          tono={total.vencido > 0 ? 'text-danger' : ''} />
        <Kpi label="Proveedores con deuda" valor={total.conDeuda} icon={Building2} />
        <Kpi label="Pedidos en camino" valor={total.enCamino} icon={Truck} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] gap-3 items-start">
        <Card className="p-0 overflow-hidden">
          <div className="p-2.5 border-b border-border-subtle flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="size-4 absolute left-2 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
              <Input value={busca} onChange={(e) => setBusca(e.target.value)}
                aria-label="Buscar proveedor" placeholder="Buscar proveedor…" className="h-9 pl-8" />
            </div>
            <Button size="sm" variant="outline" className="h-9 gap-1 shrink-0"
              onClick={() => setEditando({})}>
              <Plus className="size-4" /> Nuevo
            </Button>
          </div>
          {estadoQ.isLoading ? (
            <SkeletonFilas filas={6} className="px-3" />
          ) : filtrados.length === 0 ? (
            proveedores.length === 0 ? (
              <EstadoVacio icon={Building2} titulo="Todavía no hay proveedores"
                detalle="Registra a quienes te surten: sus pedidos, facturas y abonos se agrupan solos en su ficha."
                accion={(
                  <Button size="sm" onClick={() => setEditando({})} className="gap-1">
                    <Plus className="size-4" /> Registrar el primero
                  </Button>
                )} />
            ) : (
              <EstadoVacio icon={Search} titulo="Ningún proveedor coincide"
                detalle={`Nada con "${busca.trim()}". Prueba con parte del nombre o el NIT.`} />
            )
          ) : (
            <ul className="divide-y divide-border-subtle max-h-[32rem] overflow-y-auto">
              {filtrados.map(p => {
                const activo = seleccion?.id === p.id
                return (
                  <li key={p.id}>
                    <button onClick={() => setSeleccionId(p.id)} aria-current={activo || undefined}
                      className={`w-full px-3 py-2.5 text-left flex items-center gap-2 hover:bg-surface-2 ${activo ? 'bg-surface-2' : ''}`}>
                      <div className="min-w-0 flex-1">
                        <div className="text-[13px] font-medium truncate">{p.nombre}</div>
                        <div className="text-[11px] text-muted-foreground">
                          {p.facturas_pendientes > 0
                            ? `${p.facturas_pendientes} factura(s) pendiente(s)`
                            : 'Sin deuda'}
                          {p.pedidos_en_camino > 0 && ` · ${p.pedidos_en_camino} en camino`}
                        </div>
                      </div>
                      <span className={`text-[13px] font-semibold tabular-nums shrink-0 ${num(p.vencido) > 0 ? 'text-danger' : ''}`}>
                        {cop(num(p.saldo_pendiente))}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>

        <div className="space-y-3 min-w-0">
          {seleccion ? (
            <FichaProveedor key={seleccion.id} proveedor={seleccion}
              onAbonar={() => setAbonoAbierto(true)}
              onFactura={(p) => setFacturaPara(p)}
              onComprar={() => navigate('/compras')}
              onEditar={(p) => setEditando(p)} />
          ) : (
            <Card>
              <EstadoVacio icon={Building2} titulo="Elige un proveedor"
                detalle="A la izquierda está la lista con lo que se le debe a cada uno." />
            </Card>
          )}
          <HuerfanasFacturas proveedores={proveedores} onAsignada={recargar} />
          <PanelAvisos />
        </div>
      </div>

      <ModalAbonoProveedor abierto={abonoAbierto} onCerrar={() => setAbonoAbierto(false)}
        onRegistrado={recargar} />
      {editando && (
        <ModalProveedor proveedor={editando.id ? editando : null}
          onCerrar={() => setEditando(null)}
          onGuardado={(creado) => {
            setEditando(null)
            if (creado?.id) setSeleccionId(creado.id)
            recargar()
          }} />
      )}
      {facturaPara && (
        <ModalFactura proveedor={facturaPara} onCerrar={() => setFacturaPara(null)}
          onCreada={() => { setFacturaPara(null); recargar() }} />
      )}
    </div>
  )
}

function Kpi({ label, valor, icon: Icon, tono = '' }) {
  return (
    <Card className="p-3.5">
      <div className="flex items-center gap-2">
        <Icon className="size-4 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</span>
      </div>
      <div className={`mt-1.5 text-2xl font-semibold tabular-nums ${tono}`}>{valor}</div>
    </Card>
  )
}
