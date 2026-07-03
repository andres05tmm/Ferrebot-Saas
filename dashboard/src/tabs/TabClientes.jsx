/*
 * TabClientes — clientes (E6, recableado a endpoints SaaS).
 * GET /clientes (?q) lista/búsqueda; POST /clientes alta (dedup por documento → 200 = ya existía).
 * Los campos FISCALES (país/ciudad vía /clientes/paises y /clientes/ciudades, régimen) se muestran
 * SOLO si la empresa tiene 'facturacion_electronica' (de /config / FeaturesProvider); si está OFF,
 * form básico y NO se llaman esos endpoints. Live: re-fetch ante reconnected.
 */
import { useEffect, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import { Search, UserPlus } from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { useFetch } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'

const TIPOS_DOC = ['CC', 'NIT', 'CE', 'TI', 'PAS', 'NUIP']

export default function TabClientes() {
  const { refreshKey } = useOutletContext() ?? {}
  const features = useFeatures()
  const fiscal = features.includes('facturacion_electronica')

  const [q, setQ] = useState('')
  const clientesQ = useFetch(`/clientes${q ? `?q=${encodeURIComponent(q)}` : ''}`, [refreshKey])
  useRealtimeEvent(['reconnected'], clientesQ.refetch)

  const clientes = Array.isArray(clientesQ.data) ? clientesQ.data : []

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
      <Card className="p-3">
        <div className="relative mb-2">
          <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
          <Input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Buscar por nombre o documento…" aria-label="Buscar cliente" className="pl-9" />
        </div>
        {clientesQ.loading ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : clientes.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Sin clientes.</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {clientes.map(c => (
              <li key={c.id} className="py-2 flex items-center gap-2 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{c.nombre}</div>
                  <div className="text-[11px] text-muted-foreground truncate">
                    {[c.documento, c.telefono].filter(Boolean).join(' · ') || '—'}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <ClienteForm fiscal={fiscal} onCreado={clientesQ.refetch} />
    </div>
  )
}

function ClienteForm({ fiscal, onCreado }) {
  const [f, setF] = useState({
    nombre: '', tipo_documento: 'CC', documento: '', telefono: '', correo: '', direccion: '',
    ciudad_dane: '', regimen: '',
  })
  const [enviando, setEnviando] = useState(false)
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))

  async function crear() {
    if (!f.nombre.trim()) { toast.error('El nombre es obligatorio'); return }
    const payload = {
      nombre: f.nombre.trim(),
      tipo_documento: f.tipo_documento,
      documento: f.documento.trim() || null,
      telefono: f.telefono.trim() || null,
      correo: f.correo.trim() || null,
      direccion: f.direccion.trim() || null,
    }
    if (fiscal) {
      payload.ciudad_dane = f.ciudad_dane || null
      payload.regimen = f.regimen.trim() || null
    }
    setEnviando(true)
    try {
      const res = await api('/clientes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (res.status === 200) {
        toast.message('Ya existe un cliente con ese documento')
      } else if (res.ok) {
        toast.success('Cliente creado')
      } else {
        toast.error('No se pudo crear el cliente'); return
      }
      setF({ nombre: '', tipo_documento: 'CC', documento: '', telefono: '', correo: '', direccion: '', ciudad_dane: '', regimen: '' })
      onCreado()
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Card className="p-3.5">
      <h2 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
        <UserPlus className="size-4" /> Nuevo cliente
      </h2>
      <div className="space-y-2">
        <Input value={f.nombre} onChange={set('nombre')} placeholder="Nombre *" aria-label="Nombre" className="h-9" />
        <div className="flex gap-2">
          <select value={f.tipo_documento} onChange={set('tipo_documento')} aria-label="Tipo de documento"
            className="h-9 px-2 rounded-md border border-border bg-surface text-sm w-24">
            {TIPOS_DOC.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <Input value={f.documento} onChange={set('documento')} placeholder="Documento" aria-label="Documento" className="flex-1 h-9" />
        </div>
        <Input value={f.telefono} onChange={set('telefono')} placeholder="Teléfono" aria-label="Teléfono" className="h-9" />
        <Input value={f.correo} onChange={set('correo')} placeholder="Correo" aria-label="Correo" className="h-9" />
        <Input value={f.direccion} onChange={set('direccion')} placeholder="Dirección" aria-label="Dirección" className="h-9" />

        {fiscal && (
          <div className="pt-2 mt-1 border-t border-border-subtle space-y-2">
            <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Datos fiscales</p>
            <CiudadPicker value={f.ciudad_dane} onSelect={(dane) => setF(prev => ({ ...prev, ciudad_dane: dane }))} />
            <Input value={f.regimen} onChange={set('regimen')} placeholder="Régimen" aria-label="Régimen" className="h-9" />
          </div>
        )}

        <button onClick={crear} disabled={enviando}
          className="w-full h-10 rounded-md bg-primary text-primary-foreground font-medium hover:bg-primary-hover disabled:opacity-60">
          {enviando ? 'Guardando…' : 'Crear cliente'}
        </button>
      </div>
    </Card>
  )
}

function PaisSelect({ paisId, onChange }) {
  const [paises, setPaises] = useState([])
  useEffect(() => {
    let cancelado = false
    apiJson('/clientes/paises')
      .then(d => { if (!cancelado) setPaises(Array.isArray(d) ? d : []) })
      .catch(() => { if (!cancelado) setPaises([]) })
    return () => { cancelado = true }
  }, [])
  if (paises.length === 0) return null
  return (
    <select value={paisId} onChange={(e) => onChange(Number(e.target.value))} aria-label="País"
      className="h-9 px-2 rounded-md border border-border bg-surface text-sm w-full">
      {paises.map(p => <option key={p.matias_id} value={p.matias_id}>{p.nombre}</option>)}
    </select>
  )
}

function CiudadPicker({ value, onSelect }) {
  const [q, setQ] = useState('')
  const [paisId, setPaisId] = useState(45)
  const [resultados, setResultados] = useState([])
  const [seleccion, setSeleccion] = useState('')

  useEffect(() => {
    if (!q.trim()) { setResultados([]); return undefined }
    let cancelado = false
    apiJson(`/clientes/ciudades?pais_id=${paisId}&q=${encodeURIComponent(q.trim())}`)
      .then(d => { if (!cancelado) setResultados(Array.isArray(d) ? d : []) })
      .catch(() => { if (!cancelado) setResultados([]) })
    return () => { cancelado = true }
  }, [q, paisId])

  return (
    <div className="space-y-2">
      <PaisSelect paisId={paisId} onChange={setPaisId} />
      <Input value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Buscar ciudad…" aria-label="Buscar ciudad" className="h-9" />
      {seleccion && <p className="text-[11px] text-muted-foreground">Ciudad: {seleccion} (DANE {value})</p>}
      {resultados.length > 0 && (
        <ul className="divide-y divide-border-subtle max-h-40 overflow-y-auto scrollbar-aurora">
          {resultados.map(c => (
            <li key={c.matias_id}>
              <button onClick={() => { onSelect(String(c.dane_code)); setSeleccion(`${c.nombre}, ${c.departamento}`); setQ(''); setResultados([]) }}
                className="w-full text-left py-1.5 px-1 text-[12px] hover:bg-surface-2 rounded-md truncate">
                {c.nombre} · {c.departamento}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
