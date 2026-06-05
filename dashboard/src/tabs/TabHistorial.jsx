/*
 * TabHistorial — historial de ventas con dos vistas (Día / Mes), estado en el query string.
 * Recableado a endpoints SaaS; el detalle de cada venta trae sus líneas (GET /ventas/{id}).
 */
import { useSearchParams } from 'react-router-dom'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs.jsx'
import VistaDia from './historial/VistaDia.jsx'
import VistaMes from './historial/VistaMes.jsx'

export default function TabHistorial() {
  const [params, setParams] = useSearchParams()
  const view = params.get('view') === 'mes' ? 'mes' : 'dia'

  function setView(next) {
    const np = new URLSearchParams(params)
    if (next === 'dia') np.delete('view')   // 'dia' es el default → URL más limpia
    else np.set('view', next)
    setParams(np, { replace: true })
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">Historial de ventas</h1>
        <p className="text-xs text-muted-foreground mt-0.5 capitalize">
          {new Date().toLocaleDateString('es-CO', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'America/Bogota' })}
        </p>
      </header>

      <Tabs value={view} onValueChange={setView}>
        <TabsList>
          <TabsTrigger value="dia">Día</TabsTrigger>
          <TabsTrigger value="mes">Mes</TabsTrigger>
        </TabsList>
        <TabsContent value="dia" className="mt-4"><VistaDia /></TabsContent>
        <TabsContent value="mes" className="mt-4"><VistaMes /></TabsContent>
      </Tabs>
    </div>
  )
}
