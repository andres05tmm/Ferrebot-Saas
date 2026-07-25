/*
 * TabPerfil — perfil del usuario logueado: identidad (nombre, email, rol), personalización
 * (foto vía Cloudinary + color de acento) y su historial de acciones (ventas, gastos, abonos,
 * compras, caja) con resumen por rango. Todo contra /perfil* — el backend acota SIEMPRE al
 * usuario del token: aquí no se elige a quién mirar.
 */
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  Camera, Check, HandCoins, Loader2, Pencil, Receipt, ShoppingCart, Truck, Wallet, X,
} from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { cn } from '@/lib/utils'

// Paleta de acentos del perfil (personalización). El default sigue el tema del tenant.
const COLORES = ['#C8200E', '#E8590C', '#F0A202', '#2F9E44', '#0CA678', '#1971C2', '#6741D9', '#C2255C']
const COLOR_DEFAULT = '#C8200E'

const RANGOS = [
  { dias: 1, label: 'Hoy' },
  { dias: 7, label: '7 días' },
  { dias: 30, label: '30 días' },
]

const TIPOS = {
  venta:         { icon: ShoppingCart, label: 'Venta' },
  gasto:         { icon: Receipt,      label: 'Gasto' },
  abono:         { icon: HandCoins,    label: 'Abono' },
  compra:        { icon: Truck,        label: 'Compra' },
  caja_apertura: { icon: Wallet,       label: 'Caja' },
  caja_cierre:   { icon: Wallet,       label: 'Caja' },
}

const LIMITE = 30

function iniciales(nombre) {
  return (nombre || '?')
    .split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0].toUpperCase()).join('') || '?'
}

const FECHA_FEED = {
  day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota',
}

export default function TabPerfil() {
  const { data: perfil, loading, refetch } = useFetch('/perfil')
  const color = perfil?.color || COLOR_DEFAULT

  // ── Historial (paginado por offset, acumulando) ─────────────────────────────
  const [dias, setDias] = useState(7)
  const [feed, setFeed] = useState({ resumen: null, acciones: [], hayMas: false, cargando: true })
  const [cargandoMas, setCargandoMas] = useState(false)

  useEffect(() => {
    let cancelado = false
    setFeed(f => ({ ...f, cargando: true }))
    apiJson(`/perfil/acciones?dias=${dias}&limite=${LIMITE}`)
      .then(d => {
        if (cancelado) return
        setFeed({
          resumen: d.resumen, acciones: d.acciones,
          hayMas: d.acciones.length === LIMITE, cargando: false,
        })
      })
      .catch(() => { if (!cancelado) setFeed({ resumen: null, acciones: [], hayMas: false, cargando: false }) })
    return () => { cancelado = true }
  }, [dias])

  async function cargarMas() {
    setCargandoMas(true)
    try {
      const d = await apiJson(`/perfil/acciones?dias=${dias}&limite=${LIMITE}&offset=${feed.acciones.length}`)
      setFeed(f => ({
        ...f, acciones: [...f.acciones, ...d.acciones], hayMas: d.acciones.length === LIMITE,
      }))
    } catch { toast.error('No se pudo cargar más actividad') } finally { setCargandoMas(false) }
  }

  if (loading) {
    return (
      <div className="grid place-items-center py-24" role="status" aria-label="Cargando perfil">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    )
  }
  if (!perfil) return <p className="py-24 text-center text-sm text-muted-foreground">No se pudo cargar el perfil.</p>

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <TarjetaPerfil perfil={perfil} color={color} onCambio={refetch} />

      {/* Rango + resumen */}
      <div className="flex items-center gap-1.5">
        {RANGOS.map(r => (
          <button
            key={r.dias}
            onClick={() => setDias(r.dias)}
            className={cn(
              'h-8 px-3 rounded-full text-xs font-medium border transition-colors',
              dias === r.dias
                ? 'border-transparent text-white'
                : 'border-border bg-surface text-muted-foreground hover:text-foreground hover:bg-surface-2',
            )}
            style={dias === r.dias ? { backgroundColor: color } : undefined}
          >
            {r.label}
          </button>
        ))}
      </div>

      {feed.resumen && <Resumen resumen={feed.resumen} color={color} />}

      <Card className="p-0 overflow-hidden">
        <div className="px-3.5 py-2.5 border-b border-border-subtle">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Mi actividad
          </h2>
        </div>
        {feed.cargando ? (
          <p className="py-10 text-center text-sm text-muted-foreground">Cargando…</p>
        ) : feed.acciones.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            Sin actividad en este rango. ¡A vender! 💪
          </p>
        ) : (
          <>
            <ul className="divide-y divide-border-subtle">
              {feed.acciones.map((a, i) => <FilaAccion key={`${a.tipo}-${a.ref_id}-${i}`} accion={a} color={color} />)}
            </ul>
            {feed.hayMas && (
              <button
                onClick={cargarMas}
                disabled={cargandoMas}
                className="w-full py-2.5 text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-surface-2 border-t border-border-subtle disabled:opacity-60"
              >
                {cargandoMas ? 'Cargando…' : 'Cargar más'}
              </button>
            )}
          </>
        )}
      </Card>
    </div>
  )
}

// ── Tarjeta de identidad: foto, nombre editable, email, rol, color ────────────
function TarjetaPerfil({ perfil, color, onCambio }) {
  const fileRef = useRef(null)
  const [subiendo, setSubiendo] = useState(false)
  const [editando, setEditando] = useState(false)
  const [nombre, setNombre] = useState(perfil.nombre)
  useEffect(() => { setNombre(perfil.nombre) }, [perfil.nombre])

  async function subirFoto(e) {
    const file = e.target.files?.[0]
    e.target.value = ''   // permitir re-elegir el mismo archivo
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    setSubiendo(true)
    try {
      const res = await api('/perfil/foto', { method: 'POST', body: form })
      if (res.ok) { toast.success('Foto actualizada'); onCambio() }
      else if (res.status === 503) toast.error('Fotos no disponibles: la empresa no tiene Cloudinary configurado')
      else toast.error('No se pudo subir la foto')
    } catch { toast.error('Error de conexión') } finally { setSubiendo(false) }
  }

  async function guardar(cambios, msj) {
    try {
      const res = await api('/perfil', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cambios),
      })
      if (res.ok) { if (msj) toast.success(msj); onCambio() }
      else toast.error('No se pudo guardar')
    } catch { toast.error('Error de conexión') }
  }

  function guardarNombre() {
    const limpio = nombre.trim()
    setEditando(false)
    if (!limpio || limpio === perfil.nombre) { setNombre(perfil.nombre); return }
    guardar({ nombre: limpio }, 'Nombre actualizado')
  }

  return (
    <Card className="p-0 overflow-hidden">
      {/* Banda de color personal */}
      <div className="h-20" style={{ background: `linear-gradient(120deg, ${color}, ${color}99)` }} />
      <div className="px-4 pb-4">
        <div className="flex items-end gap-3 -mt-9">
          {/* Avatar: foto o iniciales sobre el color */}
          <div className="relative shrink-0">
            {perfil.avatar_url ? (
              <img
                src={perfil.avatar_url}
                alt={perfil.nombre}
                className="size-[76px] rounded-full object-cover border-4 border-surface bg-surface"
              />
            ) : (
              <div
                className="size-[76px] rounded-full border-4 border-surface grid place-items-center text-xl font-bold text-white"
                style={{ backgroundColor: color }}
              >
                {iniciales(perfil.nombre)}
              </div>
            )}
            <button
              onClick={() => fileRef.current?.click()}
              disabled={subiendo}
              title="Cambiar foto"
              aria-label="Cambiar foto de perfil"
              className="absolute -bottom-0.5 -right-0.5 size-7 grid place-items-center rounded-full bg-surface border border-border text-muted-foreground hover:text-foreground shadow-sm"
            >
              {subiendo ? <Loader2 className="size-3.5 animate-spin" /> : <Camera className="size-3.5" />}
            </button>
            <input ref={fileRef} type="file" accept="image/*" onChange={subirFoto} className="hidden" />
          </div>

          <div className="min-w-0 flex-1 pb-0.5">
            {editando ? (
              <div className="flex items-center gap-1.5">
                <Input
                  value={nombre}
                  onChange={e => setNombre(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') guardarNombre()
                    if (e.key === 'Escape') { setNombre(perfil.nombre); setEditando(false) }
                  }}
                  autoFocus
                  maxLength={60}
                  className="h-8 text-base font-semibold"
                  aria-label="Nombre"
                />
                <button onClick={guardarNombre} title="Guardar" className="size-8 grid place-items-center rounded-md border border-border bg-surface hover:bg-surface-2">
                  <Check className="size-4 text-success" />
                </button>
                <button onClick={() => { setNombre(perfil.nombre); setEditando(false) }} title="Cancelar" className="size-8 grid place-items-center rounded-md border border-border bg-surface hover:bg-surface-2">
                  <X className="size-4 text-muted-foreground" />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-1.5 min-w-0">
                <h2 className="text-lg font-semibold truncate">{perfil.nombre}</h2>
                <button
                  onClick={() => setEditando(true)}
                  title="Editar nombre"
                  className="size-6 grid place-items-center rounded text-muted-foreground hover:text-foreground shrink-0"
                >
                  <Pencil className="size-3.5" />
                </button>
              </div>
            )}
            <div className="flex items-center gap-2 mt-0.5 text-[12px] text-muted-foreground min-w-0">
              <span
                className="px-1.5 py-px rounded font-medium capitalize text-white shrink-0"
                style={{ backgroundColor: color }}
              >
                {perfil.rol === 'super_admin' ? 'súper admin' : perfil.rol}
              </span>
              {perfil.email && <span className="truncate">{perfil.email}</span>}
            </div>
          </div>
        </div>

        {/* Color de acento */}
        <div className="flex items-center gap-2 mt-3.5">
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground">Mi color</span>
          <div className="flex gap-1.5">
            {COLORES.map(c => (
              <button
                key={c}
                onClick={() => guardar({ color: c })}
                title={c}
                aria-label={`Color ${c}`}
                className={cn(
                  'size-6 rounded-full transition-transform hover:scale-110',
                  c === color && 'ring-2 ring-offset-2 ring-offset-surface',
                )}
                style={{ backgroundColor: c, '--tw-ring-color': c }}
              />
            ))}
          </div>
        </div>
      </div>
    </Card>
  )
}

function Resumen({ resumen, color }) {
  const items = [
    { label: 'Ventas', valor: resumen.ventas, extra: cop(Number(resumen.total_vendido)) },
    { label: 'Gastos', valor: resumen.gastos },
    { label: 'Abonos', valor: resumen.abonos },
    { label: 'Compras', valor: resumen.compras },
  ]
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
      {items.map(it => (
        <Card key={it.label} className="p-3">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{it.label}</div>
          <div className="text-xl font-bold tabular" style={{ color }}>{it.valor}</div>
          {it.extra && <div className="text-[11px] text-muted-foreground tabular truncate">{it.extra}</div>}
        </Card>
      ))}
    </div>
  )
}

function FilaAccion({ accion, color }) {
  const t = TIPOS[accion.tipo] || TIPOS.venta
  const Icon = t.icon
  const anulada = accion.estado === 'anulada'
  return (
    <li className="px-3.5 py-2.5 flex items-center gap-3 text-[13px]">
      <span
        className="size-8 rounded-full grid place-items-center shrink-0"
        style={{ backgroundColor: `${color}1A`, color }}
      >
        <Icon className="size-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className={cn('font-medium truncate', anulada && 'line-through text-muted-foreground')}>
          {accion.detalle}
        </div>
        <div className="text-[11px] text-muted-foreground">
          {new Date(accion.fecha).toLocaleString('es-CO', FECHA_FEED)}
          {anulada && ' · anulada'}
        </div>
      </div>
      {accion.monto != null && (
        <span className={cn('tabular font-semibold shrink-0', anulada && 'line-through text-muted-foreground')}>
          {cop(Number(accion.monto))}
        </span>
      )}
    </li>
  )
}
