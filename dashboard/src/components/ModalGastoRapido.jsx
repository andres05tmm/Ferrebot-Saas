/*
 * ModalGastoRapido — registrar un gasto sin salir del cockpit (/hoy en retail, /panel en obra).
 * POST /gastos (idempotente); exige caja abierta (409 → mensaje claro con el porqué).
 *
 * Familia construcción (F2.7): el modal habla el idioma de la obra — categoría VERTICAL (spec 09,
 * la misma taxonomía del bot y la bandeja) + imputación opcional a obra/máquina. Antes solo ofrecía
 * la taxonomía retail y los gastos manuales del dueño salían "Sin clasificar" en los reportes de obra.
 * La `categoria` POS (NOT NULL del backend) se deriva de la vertical. Retail queda idéntico.
 */
import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { useFeatures, esConstruccion } from '@/lib/features.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import { CATEGORIAS_GASTO_VERTICAL, CATEGORIA_POS_DEL_VERTICAL } from '@/tabs/construccion/comunes.jsx'

const CATEGORIAS = [
  ['transporte', 'Transporte'], ['papeleria', 'Papelería'], ['servicios', 'Servicios'],
  ['nomina', 'Nómina'], ['mantenimiento', 'Mantenimiento'], ['otros', 'Otros'],
]

const arr = (x) => (Array.isArray(x) ? x : [])
const SELECT = 'h-10 w-full rounded-md border border-input bg-surface px-2 text-sm text-foreground sm:h-9'

export default function ModalGastoRapido({ abierto, onCerrar, onRegistrado }) {
  const construccion = esConstruccion(useFeatures())
  const [categoria, setCategoria] = useState('otros')            // taxonomía retail
  const [categoriaObra, setCategoriaObra] = useState('OTRO')     // taxonomía vertical (construcción)
  const [obraId, setObraId] = useState('')
  const [maquinaId, setMaquinaId] = useState('')
  const [monto, setMonto] = useState('')
  const [concepto, setConcepto] = useState('')
  const [enviando, setEnviando] = useState(false)

  // Catálogos solo en construcción y solo con el modal abierto (path falsy = hook en reposo).
  const obrasQ = useFetch(construccion && abierto ? '/obras' : null)
  const maquinasQ = useFetch(construccion && abierto ? '/maquinas' : null)

  const valido = Number(monto) > 0
  // Key estable mientras el payload no cambie: un reintento tras timeout (el server SÍ commiteó) es
  // replay, no duplicado. Editar cualquier campo renueva la key (payload nuevo = operación nueva).
  const idemKey = useMemo(
    () => crypto.randomUUID(),
    [categoria, categoriaObra, obraId, maquinaId, monto, concepto],
  )

  async function registrar(e) {
    e?.preventDefault?.()
    if (!valido || enviando) return
    const payload = construccion
      ? {
        categoria: CATEGORIA_POS_DEL_VERTICAL[categoriaObra] || 'otros',
        categoria_gasto: categoriaObra,
        monto: Number(monto),
        concepto: concepto.trim() || null,
        ...(obraId ? { obra_id: Number(obraId) } : {}),
        ...(maquinaId ? { maquina_id: Number(maquinaId) } : {}),
      }
      : { categoria, monto: Number(monto), concepto: concepto.trim() || null }
    setEnviando(true)
    try {
      const res = await api('/gastos', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idemKey },
        body: JSON.stringify(payload),
      })
      if (res.ok) {
        toast.success('Gasto registrado')
        setMonto(''); setConcepto(''); setObraId(''); setMaquinaId('')
        onRegistrado?.()
        onCerrar()
      } else if (res.status === 409) {
        toast.error('No hay caja abierta: abre la caja antes de registrar gastos.')
      } else {
        toast.error('No se pudo registrar el gasto')
      }
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open={abierto} onOpenChange={(o) => { if (!o && !enviando) onCerrar() }}>
      <DialogContent aria-describedby="gasto-rapido-desc">
        <DialogHeader>
          <DialogTitle>Registrar gasto</DialogTitle>
          <DialogDescription id="gasto-rapido-desc">
            {construccion
              ? 'Sale de la caja menor; puedes imputarlo a una obra o máquina.'
              : 'Sale de la caja abierta y queda en la contabilidad del día.'}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={registrar} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="gr-monto">Monto</Label>
            <Input id="gr-monto" type="number" inputMode="numeric" min="0" step="any" autoFocus
              value={monto} onChange={(e) => setMonto(e.target.value)} placeholder="0" />
          </div>

          {construccion ? (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="gr-cat-obra">Categoría</Label>
                <select id="gr-cat-obra" value={categoriaObra} onChange={(e) => setCategoriaObra(e.target.value)} className={SELECT}>
                  {CATEGORIAS_GASTO_VERTICAL.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="gr-obra">Obra (opcional)</Label>
                  <select id="gr-obra" value={obraId} onChange={(e) => setObraId(e.target.value)} className={SELECT}>
                    <option value="">Sin obra</option>
                    {arr(obrasQ.data).filter((o) => o.estado !== 'LIQUIDADA').map((o) => (
                      <option key={o.id} value={o.id}>{o.nombre}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="gr-maquina">Máquina (opcional)</Label>
                  <select id="gr-maquina" value={maquinaId} onChange={(e) => setMaquinaId(e.target.value)} className={SELECT}>
                    <option value="">Sin máquina</option>
                    {arr(maquinasQ.data).map((m) => (
                      <option key={m.id} value={m.id}>{m.codigo ? `${m.codigo} · ${m.nombre}` : m.nombre}</option>
                    ))}
                  </select>
                </div>
              </div>
            </>
          ) : (
            <div className="space-y-1.5">
              <Label>Categoría</Label>
              <div className="flex gap-1.5 flex-wrap">
                {CATEGORIAS.map(([v, l]) => (
                  <button key={v} type="button" onClick={() => setCategoria(v)} aria-pressed={categoria === v}
                    className={`px-2 py-0.5 rounded-md border text-caption ${
                      categoria === v ? 'border-primary bg-primary/10 text-primary' : 'border-border'}`}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="gr-concepto">Concepto (opcional)</Label>
            <Input id="gr-concepto" value={concepto} onChange={(e) => setConcepto(e.target.value)}
              placeholder={construccion ? 'ACPM retro, almuerzos cuadrilla…' : 'Almuerzo, transporte de mercancía…'} />
          </div>
          <Button type="submit" disabled={!valido || enviando} className="w-full">
            {enviando ? 'Registrando…' : 'Registrar gasto'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
