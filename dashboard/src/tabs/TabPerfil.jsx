/*
 * TabPerfil — cuenta del usuario logueado, patrón "account page" de producto (Linear/Stripe):
 * columna de identidad + cuenta + preferencias + seguridad/sesión, y a la derecha la actividad
 * propia agrupada por día. Todo contra /perfil* — el backend acota SIEMPRE al usuario del token.
 *
 * Theming por tokens del sistema (DESIGN.md): ningún color de marca hardcodeado. El color
 * personal (elegido por la persona, dato de su fila en `usuarios`) solo pinta su avatar.
 */
import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import {
  Building2, Camera, Check, HandCoins, KeyRound, LogOut, Mail, Moon, Pencil,
  Receipt, ShieldCheck, ShoppingCart, Sun, Truck, UserRound, Wallet, X,
} from 'lucide-react'
import { api, apiJson } from '@/lib/api'
import { useFetch, cop } from '@/components/shared.jsx'
import { useAuth } from '@/hooks/useAuth.js'
import { useBranding } from '@/lib/branding.jsx'
import { Card } from '@/components/ui/card.jsx'
import { Input } from '@/components/ui/input.jsx'
import { cn } from '@/lib/utils'

// Paleta del color personal (dato del usuario, no theming de la app; ver cabecera).
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

export function iniciales(nombre) {
  return (nombre || '?')
    .split(/\s+/).filter(Boolean).slice(0, 2).map(p => p[0].toUpperCase()).join('') || '?'
}

// Fecha en Bogotá como YYYY-MM-DD (clave de agrupación del feed) y utilidades de presentación.
function diaBogota(iso) {
  return new Date(iso).toLocaleDateString('en-CA', { timeZone: 'America/Bogota' })
}
function tituloDia(clave) {
  const hoy = diaBogota(new Date().toISOString())
  const ayer = diaBogota(new Date(Date.now() - 864e5).toISOString())
  if (clave === hoy) return 'Hoy'
  if (clave === ayer) return 'Ayer'
  return new Date(`${clave}T12:00:00-05:00`).toLocaleDateString('es-CO', {
    weekday: 'long', day: 'numeric', month: 'long', timeZone: 'America/Bogota',
  })
}
const HORA = { hour: '2-digit', minute: '2-digit', timeZone: 'America/Bogota' }

export default function TabPerfil() {
  const { data: perfil, loading, refetch } = useFetch('/perfil')
  const color = perfil?.color || COLOR_DEFAULT

  // Avisar al shell (avatar del HeaderBar) cuando el perfil cambie.
  function onCambio() {
    refetch()
    window.dispatchEvent(new CustomEvent('perfil:actualizado'))
  }

  if (loading) return <PerfilSkeleton />
  if (!perfil) {
    return (
      <p className="py-24 text-center text-sm text-muted-foreground">
        No se pudo cargar el perfil. Refresca la página o vuelve a iniciar sesión.
      </p>
    )
  }

  return (
    <div className="max-w-5xl mx-auto grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)] items-start">
      {/* ── Columna de cuenta ─────────────────────────────────────────────── */}
      <div className="space-y-4 min-w-0">
        <TarjetaIdentidad perfil={perfil} color={color} onCambio={onCambio} />
        <SeccionCuenta perfil={perfil} />
        <SeccionPreferencias perfil={perfil} color={color} onCambio={onCambio} />
        <SeccionSeguridad perfil={perfil} />
      </div>

      {/* ── Actividad ─────────────────────────────────────────────────────── */}
      <Actividad />
    </div>
  )
}

// ── Identidad: avatar + nombre editable + rol ────────────────────────────────
function TarjetaIdentidad({ perfil, color, onCambio }) {
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

  function guardarNombre() {
    const limpio = nombre.trim()
    setEditando(false)
    if (!limpio || limpio === perfil.nombre) { setNombre(perfil.nombre); return }
    guardarPerfil({ nombre: limpio }, 'Nombre actualizado', onCambio)
  }

  return (
    <Card className="p-5">
      <div className="flex items-center gap-4">
        <div className="relative shrink-0 group">
          <AvatarGrande perfil={perfil} color={color} />
          <button
            onClick={() => fileRef.current?.click()}
            disabled={subiendo}
            title="Cambiar foto"
            aria-label="Cambiar foto de perfil"
            className={cn(
              'absolute inset-0 rounded-full grid place-items-center text-white transition-opacity',
              'bg-black/45 opacity-0 group-hover:opacity-100 focus-visible:opacity-100',
              subiendo && 'opacity-100',
            )}
          >
            <Camera className={cn('size-5', subiendo && 'animate-pulse')} />
          </button>
          <input ref={fileRef} type="file" accept="image/*" onChange={subirFoto} className="hidden" />
        </div>

        <div className="min-w-0 flex-1">
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
                className="h-9 text-[15px] font-semibold"
                aria-label="Nombre"
              />
              <button onClick={guardarNombre} title="Guardar"
                className="size-9 shrink-0 grid place-items-center rounded-md border border-border bg-surface hover:bg-surface-2">
                <Check className="size-4 text-success" />
              </button>
              <button onClick={() => { setNombre(perfil.nombre); setEditando(false) }} title="Cancelar"
                className="size-9 shrink-0 grid place-items-center rounded-md border border-border bg-surface hover:bg-surface-2">
                <X className="size-4 text-muted-foreground" />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-1 min-w-0 group/nombre">
              <h2 className="text-[17px] font-semibold tracking-tight truncate">{perfil.nombre}</h2>
              <button
                onClick={() => setEditando(true)}
                title="Editar nombre"
                aria-label="Editar nombre"
                className="size-7 grid place-items-center rounded text-muted-foreground/0 group-hover/nombre:text-muted-foreground hover:!text-foreground shrink-0 transition-colors"
              >
                <Pencil className="size-3.5" />
              </button>
            </div>
          )}
          <div className="flex items-center gap-2 mt-1 min-w-0">
            <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-primary-soft text-primary capitalize shrink-0">
              {perfil.rol === 'super_admin' ? 'súper admin' : perfil.rol}
            </span>
            {perfil.email && (
              <span className="text-[12px] text-muted-foreground truncate">{perfil.email}</span>
            )}
          </div>
        </div>
      </div>
    </Card>
  )
}

function AvatarGrande({ perfil, color }) {
  if (perfil.avatar_url) {
    return (
      <img
        src={perfil.avatar_url}
        alt={perfil.nombre}
        className="size-16 rounded-full object-cover bg-surface-2"
        style={{ boxShadow: `0 0 0 2px var(--color-card, #fff), 0 0 0 4px ${color}` }}
      />
    )
  }
  return (
    <div
      className="size-16 rounded-full grid place-items-center text-lg font-bold text-white"
      style={{ backgroundColor: color }}
    >
      {iniciales(perfil.nombre)}
    </div>
  )
}

// ── Cuenta: datos de solo lectura ────────────────────────────────────────────
function SeccionCuenta({ perfil }) {
  const branding = useBranding()
  // "junio de 2026" → "Junio de 2026" (solo la inicial; `capitalize` de CSS pondría "De" en mayúscula).
  const crudo = perfil.creado_en
    ? new Date(perfil.creado_en).toLocaleDateString('es-CO', { month: 'long', year: 'numeric', timeZone: 'America/Bogota' })
    : null
  const miembroDesde = crudo ? crudo.charAt(0).toUpperCase() + crudo.slice(1) : null
  return (
    <Seccion titulo="Cuenta">
      <FilaDato icon={Building2} label="Empresa" valor={branding?.nombre_comercial || '—'} />
      <FilaDato icon={Mail} label="Correo" valor={perfil.email || 'Sin correo de acceso'} muted={!perfil.email} />
      {miembroDesde && <FilaDato icon={UserRound} label="Miembro desde" valor={miembroDesde} />}
    </Seccion>
  )
}

// ── Preferencias: color personal + tema ──────────────────────────────────────
function SeccionPreferencias({ perfil, color, onCambio }) {
  const [tema, setTema] = useState(() =>
    typeof document !== 'undefined' ? document.documentElement.getAttribute('data-theme') || 'light' : 'light'
  )
  function toggleTema() {
    setTema(t => (t === 'dark' ? 'light' : 'dark'))
    // El dueño del estado es AppShell: se le avisa y él aplica data-theme + localStorage.
    window.dispatchEvent(new CustomEvent('ferrebot:toggle-theme'))
  }
  return (
    <Seccion titulo="Preferencias">
      <div className="px-4 py-3 space-y-2">
        <span className="block text-[13px]">Mi color</span>
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Color personal">
          {COLORES.map(c => (
            <button
              key={c}
              role="radio"
              aria-checked={c === color}
              onClick={() => guardarPerfil({ color: c }, null, onCambio)}
              title={c}
              aria-label={`Color ${c}`}
              className="size-6 rounded-full grid place-items-center transition-transform hover:scale-110 focus-visible:scale-110"
              style={{ backgroundColor: c }}
            >
              {c === color && <Check className="size-3.5 text-white" strokeWidth={3} />}
            </button>
          ))}
        </div>
      </div>
      <button
        onClick={toggleTema}
        className="w-full px-4 py-3 flex items-center justify-between gap-3 hover:bg-surface-2 transition-colors text-left"
      >
        <span className="text-[13px]">Tema</span>
        <span className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
          {tema === 'dark' ? <Moon className="size-4" /> : <Sun className="size-4" />}
          {tema === 'dark' ? 'Oscuro' : 'Claro'}
        </span>
      </button>
    </Seccion>
  )
}

// ── Seguridad y sesión ───────────────────────────────────────────────────────
function SeccionSeguridad({ perfil }) {
  const { logout } = useAuth()
  const [enviando, setEnviando] = useState(false)

  async function cambiarPassword() {
    if (!perfil.email || enviando) return
    setEnviando(true)
    try {
      const res = await api('/auth/reset/solicitar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: perfil.email }),
      })
      if (res.ok) toast.success(`Te enviamos un correo a ${perfil.email} para cambiar la contraseña`)
      else if (res.status === 429) toast.error('Demasiadas solicitudes. Espera unos minutos.')
      else toast.error('No se pudo enviar el correo')
    } catch { toast.error('Error de conexión') } finally { setEnviando(false) }
  }

  return (
    <Seccion titulo="Seguridad y sesión">
      <button
        onClick={cambiarPassword}
        disabled={!perfil.email || enviando}
        title={perfil.email ? undefined : 'Tu usuario no tiene correo de acceso'}
        className="w-full px-4 py-3 flex items-center gap-3 hover:bg-surface-2 transition-colors text-left disabled:opacity-50 disabled:hover:bg-transparent"
      >
        <KeyRound className="size-4 text-muted-foreground shrink-0" />
        <span className="text-[13px]">{enviando ? 'Enviando correo…' : 'Cambiar contraseña'}</span>
      </button>
      <button
        onClick={logout}
        className="w-full px-4 py-3 flex items-center gap-3 hover:bg-destructive/5 transition-colors text-left text-destructive"
      >
        <LogOut className="size-4 shrink-0" />
        <span className="text-[13px] font-medium">Cerrar sesión</span>
      </button>
    </Seccion>
  )
}

// ── Actividad: rango + resumen + feed agrupado por día ───────────────────────
function Actividad() {
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

  // Agrupar por día en Bogotá preservando el orden (ya viene fecha DESC del backend).
  const grupos = []
  for (const a of feed.acciones) {
    const clave = diaBogota(a.fecha)
    const ultimo = grupos[grupos.length - 1]
    if (ultimo && ultimo.clave === clave) ultimo.items.push(a)
    else grupos.push({ clave, items: [a] })
  }

  return (
    <Card className="p-0 overflow-hidden min-w-0">
      <div className="px-4 pt-3.5 pb-3 border-b border-border-subtle">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-[13px] font-semibold">Mi actividad</h2>
          <div className="flex gap-1" role="tablist" aria-label="Rango de actividad">
            {RANGOS.map(r => (
              <button
                key={r.dias}
                role="tab"
                aria-selected={dias === r.dias}
                onClick={() => setDias(r.dias)}
                className={cn(
                  'h-7 px-2.5 rounded-md text-[12px] font-medium transition-colors',
                  dias === r.dias
                    ? 'bg-primary-soft text-primary'
                    : 'text-muted-foreground hover:text-foreground hover:bg-surface-2',
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
        {feed.resumen && !feed.cargando && <ResumenLinea resumen={feed.resumen} />}
      </div>

      {feed.cargando ? (
        <FeedSkeleton />
      ) : feed.acciones.length === 0 ? (
        <div className="py-14 px-6 text-center">
          <p className="text-sm font-medium">Sin actividad en este rango</p>
          <p className="text-[12px] text-muted-foreground mt-1 max-w-xs mx-auto">
            Las ventas, gastos, abonos y compras que registres con tu usuario van a aparecer aquí.
          </p>
        </div>
      ) : (
        <>
          {grupos.map(g => (
            <section key={g.clave} aria-label={tituloDia(g.clave)}>
              <div className="px-4 py-1.5 bg-surface-2/50 border-b border-border-subtle text-[11px] font-semibold uppercase tracking-wider text-muted-foreground capitalize">
                {tituloDia(g.clave)}
              </div>
              <ul className="divide-y divide-border-subtle">
                {g.items.map((a, i) => <FilaAccion key={`${a.tipo}-${a.ref_id}-${i}`} accion={a} />)}
              </ul>
            </section>
          ))}
          {feed.hayMas && (
            <button
              onClick={cargarMas}
              disabled={cargandoMas}
              className="w-full py-2.5 text-[12px] font-medium text-muted-foreground hover:text-foreground hover:bg-surface-2 border-t border-border-subtle disabled:opacity-60"
            >
              {cargandoMas ? 'Cargando…' : 'Cargar más'}
            </button>
          )}
        </>
      )}
    </Card>
  )
}

// Resumen del rango en UNA línea quieta (nada de grilla de KPI-cards clonadas).
function ResumenLinea({ resumen }) {
  const partes = [
    `${resumen.ventas} ${resumen.ventas === 1 ? 'venta' : 'ventas'}`,
    Number(resumen.total_vendido) > 0 ? `${cop(Number(resumen.total_vendido))} vendidos` : null,
    resumen.gastos ? `${resumen.gastos} ${resumen.gastos === 1 ? 'gasto' : 'gastos'}` : null,
    resumen.abonos ? `${resumen.abonos} ${resumen.abonos === 1 ? 'abono' : 'abonos'}` : null,
    resumen.compras ? `${resumen.compras} ${resumen.compras === 1 ? 'compra' : 'compras'}` : null,
  ].filter(Boolean)
  return (
    <p className="mt-1.5 text-[12px] text-muted-foreground tabular">{partes.join(' · ')}</p>
  )
}

function FilaAccion({ accion }) {
  const t = TIPOS[accion.tipo] || TIPOS.venta
  const Icon = t.icon
  const anulada = accion.estado === 'anulada'
  return (
    <li className="px-4 h-[52px] flex items-center gap-3 text-[13px] hover:bg-surface-2/50 transition-colors">
      <span className="size-7 rounded-md grid place-items-center shrink-0 bg-surface-2 text-muted-foreground">
        <Icon className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <span className={cn('font-medium truncate block', anulada && 'line-through text-muted-foreground')}>
          {accion.detalle}
        </span>
      </div>
      {anulada && (
        <span className="px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-destructive/10 text-destructive shrink-0">
          anulada
        </span>
      )}
      <div className="text-right shrink-0">
        {accion.monto != null && (
          <div className={cn('tabular font-semibold leading-tight', anulada && 'line-through text-muted-foreground')}>
            {cop(Number(accion.monto))}
          </div>
        )}
        <div className="text-[11px] text-muted-foreground tabular leading-tight">
          {new Date(accion.fecha).toLocaleTimeString('es-CO', HORA)}
        </div>
      </div>
    </li>
  )
}

// ── Piezas compartidas ───────────────────────────────────────────────────────
function Seccion({ titulo, children }) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-4 pt-3 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {titulo}
      </div>
      <div className="divide-y divide-border-subtle border-t border-border-subtle">{children}</div>
    </Card>
  )
}

function FilaDato({ icon: Icon, label, valor, muted }) {
  return (
    <div className="px-4 py-3 flex items-center gap-3">
      <Icon className="size-4 text-muted-foreground shrink-0" />
      <span className="text-[13px] text-muted-foreground flex-1">{label}</span>
      <span className={cn('text-[13px] font-medium truncate max-w-[55%] text-right',
        muted && 'text-muted-foreground font-normal')}>
        {valor}
      </span>
    </div>
  )
}

async function guardarPerfil(cambios, msj, onCambio) {
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

// ── Skeletons (product register: skeleton, no spinner al centro) ─────────────
function PerfilSkeleton() {
  return (
    <div className="max-w-5xl mx-auto grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)] items-start" role="status" aria-label="Cargando perfil">
      <div className="space-y-4">
        <Card className="p-5">
          <div className="flex items-center gap-4">
            <div className="size-16 rounded-full bg-surface-2 animate-pulse" />
            <div className="flex-1 space-y-2">
              <div className="h-4 w-32 rounded bg-surface-2 animate-pulse" />
              <div className="h-3 w-44 rounded bg-surface-2 animate-pulse" />
            </div>
          </div>
        </Card>
        {[3, 2, 2].map((filas, i) => (
          <Card key={i} className="p-4 space-y-3">
            {Array.from({ length: filas }).map((_, j) => (
              <div key={j} className="h-3.5 rounded bg-surface-2 animate-pulse" style={{ width: `${85 - j * 15}%` }} />
            ))}
          </Card>
        ))}
      </div>
      <FeedSkeleton card />
    </div>
  )
}

function FeedSkeleton({ card = false }) {
  const filas = (
    <div className="px-4 py-3 space-y-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="size-7 rounded-md bg-surface-2 animate-pulse shrink-0" />
          <div className="h-3.5 rounded bg-surface-2 animate-pulse flex-1" style={{ maxWidth: `${70 - (i % 3) * 12}%` }} />
          <div className="h-3.5 w-16 rounded bg-surface-2 animate-pulse shrink-0" />
        </div>
      ))}
    </div>
  )
  return card ? <Card className="p-0">{filas}</Card> : filas
}
