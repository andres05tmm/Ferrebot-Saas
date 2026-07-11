/*
 * ModalRotar — cambia de operador en vivo. Cierra el tramo corriente y abre otro con el operador elegido
 * (o sin operador). POST /operacion/{id}/rotar. El tiempo de máquina activa NO se corta: sigue el mismo
 * cronómetro de la sesión; solo cambia a quién se atribuye el tramo.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import { useFetch } from '@/components/shared.jsx'
import { Campo, SELECT_CLS, BTN_PRIMARY, BTN_OUTLINE } from '../comunes.jsx'
import { postOperacion } from './net.js'

const arr = (x) => (Array.isArray(x) ? x : [])
const labelTrab = (t) => `${t.nombres || ''} ${t.apellidos || ''}`.trim() || `#${t.id}`

export default function ModalRotar({ sesion, onCerrar, onExito }) {
  const trabajadoresQ = useFetch(sesion ? '/trabajadores' : null)
  const [operadorId, setOperadorId] = useState('')
  const [enviando, setEnviando] = useState(false)

  async function rotar() {
    setEnviando(true)
    const r = await postOperacion(`/operacion/${sesion.sesion_id}/rotar`, {
      ...(operadorId ? { operador_id: Number(operadorId) } : {}),
    })
    setEnviando(false)
    if (!r.ok) { toast.error(r.error); return }
    toast.success('Operador rotado')
    onExito?.()
  }

  return (
    <Dialog open={sesion != null} onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="rotar-desc">
        <DialogHeader>
          <DialogTitle>Rotar operador — {sesion?.maquina}</DialogTitle>
          <DialogDescription id="rotar-desc">
            Cierra el tramo de {sesion?.operador || 'el operador actual'} y abre uno nuevo. La máquina
            sigue activa.
          </DialogDescription>
        </DialogHeader>
        <Campo label="Nuevo operador" hint="Déjalo sin operador si la máquina sigue sin asignado.">
          <select value={operadorId} onChange={(e) => setOperadorId(e.target.value)} className={SELECT_CLS}>
            <option value="">Sin operador</option>
            {arr(trabajadoresQ.data).map((t) => <option key={t.id} value={t.id}>{labelTrab(t)}</option>)}
          </select>
        </Campo>
        <div className="mt-3 flex justify-end gap-2">
          <button type="button" onClick={onCerrar} className={`${BTN_OUTLINE} h-9 cursor-pointer`}>Cancelar</button>
          <button type="button" onClick={rotar} disabled={enviando} className={`${BTN_PRIMARY} h-9 cursor-pointer`}>
            {enviando ? 'Rotando…' : 'Rotar operador'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
