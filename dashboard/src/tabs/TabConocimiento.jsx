/*
 * TabConocimiento — conocimiento del negocio para el agente (pack FAQ). Gateado por la feature
 * 'pack_faq' (la ruta se oculta sin ella). El negocio "nutre" entradas (titulo, contenido, activo,
 * orden) que el agente consulta con responder_faq: horarios, ubicación, precios, formas de pago,
 * parqueo, políticas… Solo admin edita (el backend ya lo exige; aquí se oculta el form con gracia).
 * CRUD por api.js contra /faq/conocimiento.
 */
import { useState } from 'react'
import { toast } from 'sonner'
import { Trash2, Power, BookText } from 'lucide-react'
import { api } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'

const arr = (d) => (Array.isArray(d) ? d : [])

async function enviar(path, method, body, okMsg, after) {
  try {
    const res = await api(path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
    if (res.ok) { toast.success(okMsg); after?.(); return true }
    if (res.status === 403) toast.error('Necesitas permisos de administrador')
    else toast.error('No se pudo guardar')
  } catch { toast.error('Error de conexión') }
  return false
}

export default function TabConocimiento() {
  const { isAdmin } = useAuth()
  const admin = isAdmin()
  // Admin ve también las inactivas (para reactivarlas); el staff solo las activas.
  const q = useFetch(`/faq/conocimiento${admin ? '?incluir_inactivas=true' : ''}`, [admin])
  const entradas = arr(q.data)
  const vacio = { titulo: '', contenido: '', orden: 0, activo: true }
  const [f, setF] = useState(vacio)
  const [editId, setEditId] = useState(null)
  const set = (k) => (e) => setF(p => ({ ...p, [k]: e.target.value }))

  async function guardar() {
    if (!f.titulo.trim() || !f.contenido.trim()) { toast.error('Título y contenido son obligatorios'); return }
    const body = { titulo: f.titulo.trim(), contenido: f.contenido.trim(), orden: Number(f.orden) || 0, activo: !!f.activo }
    const ok = editId
      ? await enviar(`/faq/conocimiento/${editId}`, 'PUT', body, 'Entrada actualizada', q.refetch)
      : await enviar('/faq/conocimiento', 'POST', body, 'Entrada creada', q.refetch)
    if (ok) { setF(vacio); setEditId(null) }
  }

  function editar(e) {
    setF({ titulo: e.titulo, contenido: e.contenido, orden: e.orden, activo: e.activo })
    setEditId(e.id)
  }

  function toggleActivo(e) {
    return enviar(
      `/faq/conocimiento/${e.id}`, 'PUT',
      { titulo: e.titulo, contenido: e.contenido, orden: e.orden, activo: !e.activo },
      e.activo ? 'Entrada desactivada' : 'Entrada activada', q.refetch,
    )
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <Card className="p-3">
        <h2 className="text-sm font-semibold mb-2 inline-flex items-center gap-1.5">
          <BookText className="size-4 text-primary" /> Conocimiento del negocio
        </h2>
        {q.loading ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : entradas.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            Aún no hay información cargada — agrega horarios, ubicación, precios…
          </p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {entradas.map(e => (
              <li key={e.id} className={`py-2.5 flex items-start gap-2 ${!e.activo ? 'opacity-60' : ''}`}>
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-[13px] truncate">
                    {e.titulo} {!e.activo && <span className="text-[11px] text-muted-foreground">(inactiva)</span>}
                  </div>
                  <div className="text-[12px] text-muted-foreground line-clamp-2">{e.contenido}</div>
                </div>
                {admin && (
                  <div className="flex items-center gap-1 shrink-0">
                    <Button size="sm" variant="ghost" onClick={() => editar(e)}>Editar</Button>
                    <Button size="sm" variant="ghost" className={e.activo ? '' : 'text-muted-foreground'}
                      aria-label={`${e.activo ? 'Desactivar' : 'Activar'} entrada ${e.id}`}
                      onClick={() => toggleActivo(e)}>
                      <Power className="size-3.5" />
                    </Button>
                    <Button size="sm" variant="ghost" aria-label={`Borrar entrada ${e.id}`} className="text-destructive"
                      onClick={() => enviar(`/faq/conocimiento/${e.id}`, 'DELETE', null, 'Entrada borrada', q.refetch)}>
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {admin ? (
        <Card className="p-3.5">
          <h3 className="text-sm font-semibold mb-3">{editId ? 'Editar entrada' : 'Nueva entrada'}</h3>
          <div className="space-y-2">
            <Input value={f.titulo} onChange={set('titulo')} placeholder="Título (p. ej. Horarios, Ubicación)"
              aria-label="Título" className="h-9" />
            <textarea value={f.contenido} onChange={set('contenido')} aria-label="Contenido" rows={6}
              className="w-full px-3 py-2 rounded-md border border-border bg-surface text-sm"
              placeholder="Información del negocio (horarios, dirección, formas de pago, parqueo, políticas…)" />
            <div className="flex items-end gap-3">
              <label className="flex flex-col gap-1 w-24">
                <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Orden</span>
                <Input type="number" value={f.orden} onChange={set('orden')} aria-label="Orden" className="h-9" />
              </label>
              <label className="inline-flex items-center gap-2 text-sm pb-2">
                <input type="checkbox" checked={!!f.activo}
                  onChange={e => setF(p => ({ ...p, activo: e.target.checked }))} aria-label="Activa" />
                Activa
              </label>
            </div>
            <div className="flex justify-end gap-2">
              {editId && <Button variant="ghost" onClick={() => { setF(vacio); setEditId(null) }}>Cancelar</Button>}
              <Button onClick={guardar}>{editId ? 'Guardar' : 'Crear entrada'}</Button>
            </div>
          </div>
        </Card>
      ) : (
        <Card className="p-6 text-center text-sm text-muted-foreground">
          Solo un administrador puede editar el conocimiento del negocio.
        </Card>
      )}
    </div>
  )
}
