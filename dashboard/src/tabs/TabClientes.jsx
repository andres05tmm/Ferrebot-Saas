/*
 * TabClientes — CRUD de clientes, con la vista del FerreBot viejo: tabla con avatar, badge del tipo
 * de ID, tipo de persona, teléfono/correo y acciones; búsqueda con debounce y paginación de 50.
 *
 * Endpoints: GET /clientes (?q) · POST /clientes (dedup por documento → 200 = ya existía) ·
 * PUT /clientes/{id} (patch parcial) · DELETE /clientes/{id} (admin; 409 si tiene ventas).
 *
 * El modal de alta/edición trae los datos FISCALES solo si la empresa tiene 'facturacion_electronica':
 * país + buscador de CIUDAD con autocompletado (GET /clientes/ciudades, debounce 300 ms, mínimo 2
 * letras, lista desplegable) y RÉGIMEN como selector de dos opciones (responsable / no responsable de
 * IVA) — antes la ciudad era una lista suelta que casi nunca aparecía y el régimen un texto libre que
 * la facturación no sabía interpretar. Se persiste `regimen` con los literales que entiende
 * `modules/facturacion/ubl._normalizar_regimen`.
 *
 * `tipo_persona` NO es una columna: se deriva del tipo de documento (NIT → jurídica), que es
 * exactamente lo que hace la facturación al armar el customer (ubl.armar_customer).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useOutletContext } from 'react-router-dom'
import { toast } from 'sonner'
import {
  AlertCircle, ChevronLeft, ChevronRight, MapPin, Pencil, Plus, Search, Trash2, Users,
} from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { useFetch, useIsMobile } from '@/components/shared.jsx'
import { useRealtimeEvent } from '@/components/RealtimeProvider.jsx'
import { useFeatures } from '@/lib/features.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Label } from '@/components/ui/label.jsx'
import { Button } from '@/components/ui/button.jsx'
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import { cn } from '@/lib/utils'

const TIPOS_DOC = ['CC', 'NIT', 'CE', 'TI', 'PAS', 'NUIP']
const PAGINA = 50
// Literales que entiende la facturación (`_normalizar_regimen`); se guardan tal cual en `clientes.regimen`.
const REGIMEN_RESP = 'responsable_iva'
const REGIMEN_NO_RESP = 'no_responsable_iva'

/** Régimen guardado (texto libre histórico, '1'/'2', o los literales nuevos) → literal canónico. */
function normalizarRegimen(valor) {
  const t = String(valor ?? '').trim().toLowerCase()
  if (!t) return ''
  if (t.startsWith('no')) return REGIMEN_NO_RESP
  if (t === '1' || t.startsWith('responsable')) return REGIMEN_RESP
  if (t === '2') return REGIMEN_NO_RESP
  return ''
}

const PERSONAS = ['Natural', 'Jurídica']

/** Derivado del documento (NIT → jurídica), que es el criterio de ubl.armar_customer. */
const personaDerivada = (tipoDoc) => (tipoDoc === 'NIT' ? 'Jurídica' : 'Natural')

/** Tipo de persona a mostrar: manda el dato guardado (0066) y, si el cliente es viejo y no lo tiene,
 * se cae al derivado del documento — en el catálogo migrado hay S.A.S cargadas con CC. */
const personaDe = (cliente) => cliente?.tipo_persona || personaDerivada(cliente?.tipo_documento)

function iniciales(nombre) {
  const w = String(nombre || '').trim().split(/\s+/)
  if (!w[0]) return '?'
  return (w.length === 1 ? w[0].slice(0, 2) : w[0][0] + w[1][0]).toUpperCase()
}

const COLORES_AVATAR = [
  'bg-primary/15 text-primary', 'bg-success/15 text-success',
  'bg-info/15 text-info', 'bg-warning/15 text-warning',
]

function Avatar({ nombre, size = 'md' }) {
  const idx = nombre ? nombre.charCodeAt(0) % COLORES_AVATAR.length : 0
  return (
    <span className={cn(
      'rounded-full grid place-items-center font-bold border border-border shrink-0',
      COLORES_AVATAR[idx], size === 'lg' ? 'size-10 text-sm' : 'size-9 text-xs',
    )}>
      {iniciales(nombre)}
    </span>
  )
}

function TipoDocBadge({ tipo }) {
  if (!tipo) return null
  return (
    <span className={cn(
      'inline-block text-[10px] font-bold px-1.5 py-0.5 rounded border',
      tipo === 'NIT' ? 'bg-info/10 text-info border-info/30'
        : tipo === 'CE' ? 'bg-warning/10 text-warning border-warning/30'
          : 'bg-primary/10 text-primary border-primary/30',
    )}>{tipo}</span>
  )
}

export default function TabClientes() {
  const { refreshKey } = useOutletContext() ?? {}
  const features = useFeatures()
  const fiscal = features.includes('facturacion_electronica')
  const { isAdmin } = useAuth()
  const admin = isAdmin()
  const isMobile = useIsMobile()

  const [texto, setTexto] = useState('')      // lo que se escribe
  const [q, setQ] = useState('')              // lo que se consulta (debounce 300 ms, como el viejo)
  const [pagina, setPagina] = useState(0)
  const [creando, setCreando] = useState(false)
  const [editando, setEditando] = useState(null)
  const [eliminando, setEliminando] = useState(null)
  const timerRef = useRef(null)

  const clientesQ = useFetch(`/clientes${q ? `?q=${encodeURIComponent(q)}` : ''}`, [refreshKey])
  useRealtimeEvent(['reconnected'], clientesQ.refetch)

  function buscar(valor) {
    setTexto(valor)
    setPagina(0)
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setQ(valor.trim()), 300)
  }
  useEffect(() => () => clearTimeout(timerRef.current), [])

  const clientes = useMemo(
    () => (Array.isArray(clientesQ.data) ? clientesQ.data : []),
    [clientesQ.data],
  )
  const paginas = Math.max(1, Math.ceil(clientes.length / PAGINA))
  const paginaActual = Math.min(pagina, paginas - 1)
  const visibles = clientes.slice(paginaActual * PAGINA, (paginaActual + 1) * PAGINA)

  async function eliminar(cliente) {
    try {
      const res = await api(`/clientes/${cliente.id}`, { method: 'DELETE' })
      if (res.status === 409) {
        toast.error('El cliente tiene ventas o fiados: no se puede eliminar.')
      } else if (res.ok) {
        toast.success('Cliente eliminado')
        setEliminando(null)
        clientesQ.refetch()
      } else {
        toast.error('No se pudo eliminar el cliente')
      }
    } catch { toast.error('Error de conexión') }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight inline-flex items-center gap-2">
            <Users className="size-5 text-muted-foreground" /> Clientes
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            {clientesQ.loading ? 'Cargando…' : `${clientes.length} ${clientes.length === 1 ? 'cliente' : 'clientes'} ${q ? 'encontrados' : 'registrados'}`}
          </p>
        </div>
        <Button onClick={() => setCreando(true)} className="gap-1.5">
          <Plus className="size-4" /> Nuevo cliente
        </Button>
      </div>

      <div className="relative max-w-md">
        <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input value={texto} onChange={(e) => buscar(e.target.value)}
          placeholder="Buscar por nombre o documento…" aria-label="Buscar cliente" className="pl-9" />
      </div>

      <Card className="p-0 overflow-hidden">
        {clientesQ.loading ? (
          <p className="py-12 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : clientes.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">
            {q ? 'Sin resultados para esa búsqueda.' : 'No hay clientes registrados aún.'}
          </p>
        ) : (
          <>
            {isMobile ? (
              <div>
                {visibles.map(c => (
                  <FilaMovil key={c.id} cliente={c} admin={admin}
                    onEditar={() => setEditando(c)} onEliminar={() => setEliminando(c)} />
                ))}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead className="bg-surface-2/60">
                    <tr>
                      {['Cliente', 'Identificación', 'Tipo persona', 'Teléfono', 'Correo', ''].map((h, i) => (
                        <th key={h || `acc-${i}`}
                          className="px-3.5 py-2 text-left text-[10px] uppercase tracking-wider font-semibold text-muted-foreground border-b border-border">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visibles.map(c => (
                      <FilaEscritorio key={c.id} cliente={c} admin={admin}
                        onEditar={() => setEditando(c)} onEliminar={() => setEliminando(c)} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="flex items-center justify-between gap-2 flex-wrap px-3.5 py-2 border-t border-border-subtle bg-surface-2/30">
              <span className="text-[11px] text-muted-foreground">
                Mostrando {visibles.length} de {clientes.length} clientes
              </span>
              {paginas > 1 && (
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="outline" disabled={paginaActual === 0}
                    onClick={() => setPagina(p => Math.max(0, p - 1))} className="gap-1">
                    <ChevronLeft className="size-3.5" /> Anterior
                  </Button>
                  <span className="text-[11px] text-muted-foreground tabular-nums">
                    {paginaActual + 1} / {paginas}
                  </span>
                  <Button size="sm" variant="outline" disabled={paginaActual >= paginas - 1}
                    onClick={() => setPagina(p => Math.min(paginas - 1, p + 1))} className="gap-1">
                    Siguiente <ChevronRight className="size-3.5" />
                  </Button>
                </div>
              )}
            </div>
          </>
        )}
      </Card>

      {(creando || editando) && (
        <ModalCliente
          cliente={editando}
          fiscal={fiscal}
          onClose={() => { setCreando(false); setEditando(null) }}
          onGuardado={() => { setCreando(false); setEditando(null); clientesQ.refetch() }}
        />
      )}
      {eliminando && (
        <ModalEliminar cliente={eliminando} onClose={() => setEliminando(null)}
          onConfirmar={() => eliminar(eliminando)} />
      )}
    </div>
  )
}

// ── Filas ─────────────────────────────────────────────────────────────────────
function Acciones({ admin, onEditar, onEliminar, size = 'sm' }) {
  const dim = size === 'lg' ? 'size-9' : 'size-8'
  return (
    <div className="flex gap-1 justify-end shrink-0">
      <button onClick={onEditar} title="Editar cliente"
        className={cn(dim, 'grid place-items-center rounded-md text-info hover:bg-info/10')}>
        <Pencil className="size-3.5" />
      </button>
      {admin && (
        <button onClick={onEliminar} title="Eliminar cliente"
          className={cn(dim, 'grid place-items-center rounded-md text-destructive hover:bg-destructive/10')}>
          <Trash2 className="size-3.5" />
        </button>
      )}
    </div>
  )
}

function FilaEscritorio({ cliente, admin, onEditar, onEliminar }) {
  return (
    <tr className="border-b border-border-subtle last:border-0 hover:bg-surface-2/40">
      <td className="px-3.5 py-2.5">
        <div className="flex items-center gap-2.5">
          <Avatar nombre={cliente.nombre} />
          <div className="min-w-0">
            <div className="text-[13px] font-semibold truncate">{cliente.nombre}</div>
            {cliente.direccion && (
              <div className="text-[10px] text-muted-foreground flex items-center gap-1 mt-0.5 truncate">
                <MapPin className="size-3 shrink-0" /><span className="truncate">{cliente.direccion}</span>
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <TipoDocBadge tipo={cliente.tipo_documento} />
          <span className="text-[11px] text-muted-foreground tabular-nums">{cliente.documento || '—'}</span>
        </div>
      </td>
      <td className="px-3.5 py-2.5 text-[11px] text-muted-foreground">{personaDe(cliente)}</td>
      <td className="px-3.5 py-2.5 text-[11px] tabular-nums">
        {cliente.telefono || <span className="text-muted-foreground">—</span>}
      </td>
      <td className="px-3.5 py-2.5 text-[11px] max-w-44 truncate">
        {cliente.correo || <span className="text-muted-foreground">—</span>}
      </td>
      <td className="px-3.5 py-2.5">
        <Acciones admin={admin} onEditar={onEditar} onEliminar={onEliminar} />
      </td>
    </tr>
  )
}

function FilaMovil({ cliente, admin, onEditar, onEliminar }) {
  return (
    <div className="flex items-center gap-2.5 px-3.5 py-2.5 border-b border-border-subtle last:border-0">
      <Avatar nombre={cliente.nombre} size="lg" />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-semibold truncate">{cliente.nombre}</div>
        <div className="flex items-center gap-1.5 mt-1 flex-wrap text-[11px] text-muted-foreground">
          <TipoDocBadge tipo={cliente.tipo_documento} />
          {cliente.documento && <span>{cliente.documento}</span>}
          {cliente.telefono && <span>· {cliente.telefono}</span>}
        </div>
      </div>
      <Acciones admin={admin} onEditar={onEditar} onEliminar={onEliminar} size="lg" />
    </div>
  )
}

// ── Modal de alta / edición ───────────────────────────────────────────────────
function ModalCliente({ cliente, fiscal, onClose, onGuardado }) {
  const esEdicion = !!cliente
  const [f, setF] = useState({
    nombre: cliente?.nombre || '',
    tipo_documento: cliente?.tipo_documento || 'CC',
    tipo_persona: personaDe(cliente),
    documento: cliente?.documento || '',
    telefono: cliente?.telefono || '',
    correo: cliente?.correo || '',
    direccion: cliente?.direccion || '',
    ciudad_dane: cliente?.ciudad_dane || '',
    regimen: normalizarRegimen(cliente?.regimen),
  })
  const [enviando, setEnviando] = useState(false)
  const [error, setError] = useState('')
  // Mientras nadie toque el tipo de persona, sigue al documento (NIT → jurídica); apenas se elige a
  // mano, manda la elección (una S.A.S cargada con CC tiene que poder quedar jurídica).
  const personaManual = useRef(!!cliente?.tipo_persona)
  const set = (k) => (e) => setF(prev => ({ ...prev, [k]: e.target.value }))
  const setTipoDoc = (e) => setF(prev => ({
    ...prev,
    tipo_documento: e.target.value,
    tipo_persona: personaManual.current ? prev.tipo_persona : personaDerivada(e.target.value),
  }))

  async function guardar() {
    if (!f.nombre.trim()) { setError('El nombre es obligatorio'); return }
    setError('')
    const payload = {
      nombre: f.nombre.trim(),
      tipo_documento: f.tipo_documento,
      tipo_persona: f.tipo_persona,
      documento: f.documento.trim() || null,
      telefono: f.telefono.trim() || null,
      correo: f.correo.trim() || null,
      direccion: f.direccion.trim() || null,
    }
    if (fiscal) {
      payload.ciudad_dane = f.ciudad_dane || null
      payload.regimen = f.regimen || null
    }
    setEnviando(true)
    try {
      const res = esEdicion
        ? await api(`/clientes/${cliente.id}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        })
        : await api('/clientes', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
        })
      if (!esEdicion && res.status === 200) {
        toast.message('Ya existe un cliente con ese documento')
      } else if (res.ok) {
        toast.success(esEdicion ? 'Cliente actualizado' : 'Cliente creado')
      } else {
        setError('No se pudo guardar el cliente'); return
      }
      onGuardado()
    } catch { setError('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Dialog open onOpenChange={(abierto) => { if (!abierto) onClose() }}>
      <DialogContent className="max-w-md max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{esEdicion ? 'Editar cliente' : 'Nuevo cliente'}</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <Label htmlFor="cli-nombre">Nombre *</Label>
            <Input id="cli-nombre" autoFocus value={f.nombre} onChange={set('nombre')}
              placeholder="Ej: JUAN CARLOS PÉREZ" aria-label="Nombre"
              onKeyDown={(e) => { if (e.key === 'Enter') guardar() }} />
          </div>

          <div className="grid grid-cols-[110px_1fr] gap-2">
            <div>
              <Label htmlFor="cli-tipo">Tipo ID</Label>
              <select id="cli-tipo" value={f.tipo_documento} onChange={setTipoDoc}
                aria-label="Tipo de documento"
                className="h-9 w-full px-2 rounded-md border border-border bg-surface text-sm">
                {TIPOS_DOC.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <Label htmlFor="cli-doc">Identificación</Label>
              <Input id="cli-doc" value={f.documento} onChange={set('documento')}
                placeholder="Ej: 1234567890" aria-label="Documento" />
            </div>
          </div>

          <div>
            <Label>Tipo de persona</Label>
            <div className="flex gap-2">
              {PERSONAS.map(tp => (
                <button key={tp} type="button" aria-pressed={f.tipo_persona === tp}
                  onClick={() => { personaManual.current = true; setF(prev => ({ ...prev, tipo_persona: tp })) }}
                  className={cn(
                    'flex-1 h-9 rounded-md text-xs font-semibold border transition-colors',
                    f.tipo_persona === tp
                      ? 'bg-primary/10 text-primary border-primary'
                      : 'border-border text-muted-foreground hover:bg-surface-2',
                  )}>
                  {tp}
                </button>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <Label htmlFor="cli-tel">Teléfono</Label>
              <Input id="cli-tel" value={f.telefono} onChange={set('telefono')}
                placeholder="300 123 4567" aria-label="Teléfono" />
            </div>
            <div>
              <Label htmlFor="cli-mail">Correo</Label>
              <Input id="cli-mail" type="email" value={f.correo} onChange={set('correo')}
                placeholder="cliente@correo.com" aria-label="Correo" />
            </div>
          </div>

          <div>
            <Label htmlFor="cli-dir">Dirección</Label>
            <Input id="cli-dir" value={f.direccion} onChange={set('direccion')}
              placeholder="Calle 10 # 5-20" aria-label="Dirección" />
          </div>

          {fiscal && (
            <div className="pt-2 border-t border-border-subtle space-y-3">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Datos fiscales</p>

              <CiudadPicker
                daneInicial={f.ciudad_dane}
                onSelect={(dane) => setF(prev => ({ ...prev, ciudad_dane: dane }))}
              />

              <div>
                <Label>Régimen fiscal</Label>
                <div className="flex gap-2">
                  {[
                    { valor: REGIMEN_NO_RESP, label: 'No responsable de IVA' },
                    { valor: REGIMEN_RESP, label: 'Responsable de IVA' },
                  ].map(({ valor, label }) => (
                    <button key={valor} type="button" aria-pressed={f.regimen === valor}
                      onClick={() => setF(prev => ({ ...prev, regimen: prev.regimen === valor ? '' : valor }))}
                      className={cn(
                        'flex-1 h-9 rounded-md text-[11px] font-semibold border transition-colors',
                        f.regimen === valor
                          ? 'bg-info/10 text-info border-info'
                          : 'border-border text-muted-foreground hover:bg-surface-2',
                      )}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-center gap-2 p-2.5 rounded-md bg-destructive/10 border border-destructive/30 text-destructive text-[11px]">
              <AlertCircle className="size-3.5 shrink-0" />{error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancelar</Button>
          <Button onClick={guardar} disabled={enviando}>
            {enviando ? 'Guardando…' : esEdicion ? 'Guardar cambios' : 'Crear cliente'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ModalEliminar({ cliente, onClose, onConfirmar }) {
  return (
    <Dialog open onOpenChange={(abierto) => { if (!abierto) onClose() }}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Eliminar cliente</DialogTitle>
        </DialogHeader>
        <div>
          <p className="text-sm font-medium">{cliente.nombre}</p>
          <p className="text-[11px] text-muted-foreground mt-0.5">
            {[cliente.tipo_documento, cliente.documento].filter(Boolean).join(' ') || 'Sin identificación'}
          </p>
        </div>
        <div className="flex items-start gap-2 p-2.5 rounded-md bg-warning/10 border border-warning/30 text-warning text-[11px]">
          <AlertCircle className="size-3.5 shrink-0 mt-px" />
          <span>Si el cliente tiene ventas o fiados registrados, no podrá eliminarse.</span>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancelar</Button>
          <Button variant="destructive" onClick={onConfirmar}>Sí, eliminar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// ── Ciudad (autocompletado sobre el catálogo de MATIAS) ───────────────────────
function CiudadPicker({ daneInicial, onSelect }) {
  const [paises, setPaises] = useState([])
  const [paisId, setPaisId] = useState(45)          // 45 = Colombia (default de MATIAS)
  const [texto, setTexto] = useState('')
  const [opciones, setOpciones] = useState([])
  const [abierto, setAbierto] = useState(false)
  const [buscando, setBuscando] = useState(false)
  const [elegida, setElegida] = useState(daneInicial ? `DANE ${daneInicial}` : '')

  useEffect(() => {
    let cancelado = false
    apiJson('/clientes/paises')
      .then(d => { if (!cancelado) setPaises(Array.isArray(d) ? d : []) })
      .catch(() => { if (!cancelado) setPaises([]) })
    return () => { cancelado = true }
  }, [])

  // Debounce 300 ms y mínimo 2 letras (igual que el viejo): sin esto se disparaba una consulta por
  // tecla y la respuesta lenta pisaba a la rápida, así que la lista casi nunca alcanzaba a aparecer.
  const buscarCiudades = useCallback(async (q, pais) => {
    if (q.trim().length < 2) { setOpciones([]); return }
    setBuscando(true)
    try {
      const d = await apiJson(`/clientes/ciudades?pais_id=${pais}&q=${encodeURIComponent(q.trim())}`)
      setOpciones(Array.isArray(d) ? d : [])
    } catch { setOpciones([]) } finally { setBuscando(false) }
  }, [])

  useEffect(() => {
    const t = setTimeout(() => buscarCiudades(texto, paisId), 300)
    return () => clearTimeout(t)
  }, [texto, paisId, buscarCiudades])

  function elegir(c) {
    // El catálogo puede traer `dane_code` en 0 (sin DANE): ahí no se guarda basura, se deja vacío y la
    // facturación cae a la ciudad por defecto de la empresa.
    onSelect(c.dane_code ? String(c.dane_code) : '')
    setElegida([c.nombre, c.departamento].filter(Boolean).join(', '))
    setTexto('')
    setOpciones([])
    setAbierto(false)
  }

  return (
    <div className="space-y-2">
      <div>
        <Label htmlFor="cli-pais">País</Label>
        <select id="cli-pais" value={paisId} aria-label="País"
          onChange={(e) => { setPaisId(Number(e.target.value)); setTexto(''); setOpciones([]); onSelect(''); setElegida('') }}
          className="h-9 w-full px-2 rounded-md border border-border bg-surface text-sm">
          {paises.length > 0
            ? paises.map(p => <option key={p.matias_id} value={p.matias_id}>{p.nombre}</option>)
            : <option value={45}>Colombia</option>}
        </select>
      </div>

      <div className="relative">
        <Label htmlFor="cli-ciudad">Ciudad</Label>
        <Input id="cli-ciudad" value={texto} aria-label="Buscar ciudad"
          placeholder={elegida || 'Escribe 2 letras para buscar…'}
          onChange={(e) => { setTexto(e.target.value); setAbierto(true) }}
          onFocus={() => setAbierto(true)}
          onBlur={() => setTimeout(() => setAbierto(false), 150)} />
        {abierto && (buscando || opciones.length > 0) && (
          <div className="absolute top-full left-0 right-0 mt-1 z-50 bg-surface border border-border rounded-md max-h-48 overflow-y-auto shadow-md">
            {buscando ? (
              <p className="px-3 py-2 text-[11px] text-muted-foreground">Buscando…</p>
            ) : opciones.map(c => (
              <button key={c.matias_id} type="button" onMouseDown={() => elegir(c)}
                className="w-full text-left px-3 py-2 text-[12px] hover:bg-surface-2 border-b border-border-subtle last:border-0">
                <span className="font-semibold">{c.nombre}</span>
                {c.departamento && <span className="text-muted-foreground"> — {c.departamento}</span>}
              </button>
            ))}
          </div>
        )}
        {elegida && <p className="mt-1 text-[11px] text-muted-foreground">Ciudad: {elegida}</p>}
      </div>
    </div>
  )
}
