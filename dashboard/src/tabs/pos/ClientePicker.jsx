/*
 * ClientePicker — buscar y fijar el cliente de la venta (opcional; obligatorio para fiado, lo valida
 * el backend). Búsqueda server-side con debounce corto: la lista de clientes no se precarga.
 */
import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { apiJson } from '@/lib/api'
import { Input } from '@/components/ui/input.jsx'

export default function ClientePicker({ cliente, onSelect }) {
  const [q, setQ] = useState('')
  const [resultados, setResultados] = useState([])
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
      <div className="flex items-center gap-2 mt-1 text-body-sm">
        <span className="text-muted-foreground">Cliente:</span>
        <span className="font-medium truncate flex-1">{cliente.nombre}</span>
        <button onClick={() => onSelect(null)} aria-label="Quitar cliente"
          className="size-6 grid place-items-center rounded-md text-muted-foreground hover:text-foreground">
          <X className="size-3.5" />
        </button>
      </div>
    )
  }
  return (
    <div className="mt-1">
      <Input value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Cliente (opcional)…" aria-label="Buscar cliente" className="h-8 text-body-sm" />
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
