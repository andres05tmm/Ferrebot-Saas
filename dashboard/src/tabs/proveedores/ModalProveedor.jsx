/*
 * ModalProveedor — dar de alta o corregir un proveedor.
 *
 * Antes los proveedores solo aparecían de rebote al registrar una compra (creados por nombre), así
 * que no había dónde anotar el teléfono ni con quién se habla. Con `proveedor` edita; sin él, crea.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'

const CAMPOS = [
  ['nombre', 'Nombre del proveedor', 'Ferre Mayorista'],
  ['nit', 'NIT (opcional)', '900123456-7'],
  ['telefono', 'Teléfono (opcional)', '3001234567'],
  ['correo', 'Correo (opcional)', 'ventas@proveedor.com'],
  ['contacto_nombre', 'Con quién se habla (opcional)', 'Doña Marta'],
  ['contacto_telefono', 'Teléfono del contacto (opcional)', '3009876543'],
]

export default function ModalProveedor({ proveedor, onCerrar, onGuardado }) {
  const [f, setF] = useState(() => ({
    nombre: proveedor?.nombre ?? '', nit: proveedor?.nit ?? '',
    telefono: proveedor?.telefono ?? '', correo: proveedor?.correo ?? '',
    contacto_nombre: proveedor?.contacto_nombre ?? '',
    contacto_telefono: proveedor?.contacto_telefono ?? '',
  }))
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))
  const editando = !!proveedor

  async function guardar(e) {
    e?.preventDefault?.()
    if (!f.nombre.trim() || enviando) return
    const body = Object.fromEntries(
      Object.entries(f).map(([k, v]) => [k, v.trim() || null]),
    )
    body.nombre = f.nombre.trim()
    setEnviando(true)
    try {
      const res = await api(editando ? `/proveedores/${proveedor.id}` : '/proveedores', {
        method: editando ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      if (res.status === 409) { toast.error('Ya hay un proveedor con ese nombre'); return }
      if (!res.ok) { toast.error('No se pudo guardar el proveedor'); return }
      toast.success(editando ? 'Proveedor actualizado' : 'Proveedor registrado')
      onGuardado(await res.json().catch(() => null))
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open onOpenChange={(o) => { if (!o) onCerrar() }}>
      <DialogContent aria-describedby="prov-desc">
        <DialogHeader>
          <DialogTitle>{editando ? `Editar ${proveedor.nombre}` : 'Nuevo proveedor'}</DialogTitle>
          <DialogDescription id="prov-desc">
            Solo el nombre es obligatorio; lo demás se completa cuando se sepa.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={guardar} className="space-y-3">
          {CAMPOS.map(([k, label, ph], i) => (
            <div key={k} className="space-y-1.5">
              <Label htmlFor={`prov-${k}`}>{label}</Label>
              <Input id={`prov-${k}`} value={f[k]} onChange={set(k)} aria-label={label}
                placeholder={ph} autoFocus={i === 0} />
            </div>
          ))}
          <Button type="submit" disabled={!f.nombre.trim() || enviando} className="w-full">
            {enviando ? 'Guardando…' : editando ? 'Guardar cambios' : 'Registrar proveedor'}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  )
}
