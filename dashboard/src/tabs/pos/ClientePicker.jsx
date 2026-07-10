/*
 * ClientePicker — buscar y fijar el cliente de la venta (opcional; obligatorio para fiado, lo valida
 * el backend). Búsqueda server-side con debounce corto: la lista de clientes no se precarga.
 * El botón "+" crea el cliente al vuelo con lo escrito (réplica del alta inline del viejo).
 */
import { useEffect, useState } from 'react'
import { Plus, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, apiJson } from '@/lib/api'
import { Input } from '@/components/ui/input.jsx'

export default function ClientePicker({ cliente, onSelect }) {
  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])
  const [creando, setCreando] = useState(false)

  async function crearRapido() {
    const nombre = q.trim()
    if (!nombre || creando) return
    setCreando(true)
    try {
      const res = await api('/clientes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nombre }),
      })
      if (!res.ok) { toast.error('No se pudo crear el cliente'); return }
      const nuevo = await res.json()
      onSelect(nuevo); setQ(''); setResultados([])
      toast.success(`Cliente ${nuevo.nombre} creado`)
    } catch { toast.error('Error de conexión') } finally { setCreando(false) }
  }
  useEffect(() => {
    const term = q.trim()
    if (!term) { setResultados([]); return undefined }
    const ctrl = new AbortController()
    const t = setTimeout(() => {
      apiJson(`/clientes?q=${encodeURIComponent(term)}`, { signal: ctrl.signal })
        .then(d => setResultados(Array.isArray(d) ? d : []))
        .catch(err => { if (err?.name !== 'AbortError') setResultados([]) })
    }, 200)
    return () => { clearTimeout(t); ctrl.abort() }
  }, [q])

  if (cliente) {
    return (
      <div className="mt-3">
        <span className="block text-caption uppercase tracking-wider text-muted-foreground mb-1">Cliente (opcional)</span>
        <div className="flex items-center gap-2 text-body-sm">
          <span className="font-medium truncate flex-1">{cliente.nombre}</span>
          <button onClick={() => onSelect(null)} aria-label="Quitar cliente"
            className="size-6 grid place-items-center rounded-md text-muted-foreground hover:text-foreground">
            <X className="size-3.5" />
          </button>
        </div>
      </div>
    )
  }
  return (
    <div className="mt-3">
      <span className="block text-caption uppercase tracking-wider text-muted-foreground mb-1">Cliente (opcional)</span>
      <div className="flex items-center gap-1.5">
        <Input value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Buscar por nombre o cédula/NIT…" aria-label="Buscar cliente" className="h-9 text-body-sm flex-1" />
        <button onClick={crearRapido} disabled={!q.trim() || creando} aria-label="Crear cliente"
          title="Crear cliente con este nombre"
          className="size-9 grid place-items-center rounded-md bg-primary text-primary-foreground disabled:opacity-40 shrink-0">
          <Plus className="size-4" />
        </button>
      </div>
      {resultados.length > 0 && (
        <ul className="mt-1 divide-y divide-border-subtle max-h-40 overflow-y-auto scrollbar-aurora">
          {resultados.map(c => (
            <li key={c.id}>
              <button onClick={() => { onSelect(c); setQ(''); setResultados([]) }}
                className="w-full text-left py-1.5 px-1 text-body-sm hover:bg-surface-2 rounded-md truncate">
                {c.nombre}{c.documento ? ` · ${c.documento}` : ''}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
