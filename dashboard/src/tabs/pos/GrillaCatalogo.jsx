/*
 * GrillaCatalogo — la vitrina tap-first del POS (reforma grilla híbrida, patrón del FerreBot viejo).
 *
 * SIEMPRE visible: chips [★ Favoritos] [⚡ Frecuentes] [Todo] [categorías reales del tenant] y cards
 * de producto (nombre + precio de referencia + badge de cantidad en carrito + estrella de favorito).
 * Con término de búsqueda, la lista viene YA filtrada/rankeada del tab (filtro local instantáneo o
 * fallback del servidor); sin término, muestra el chip activo. El precio de la card es REFERENCIA del
 * catálogo — el precio real de la línea lo pone el servidor al agregar (server-authoritative).
 *
 * Sin virtualización a propósito (<1k productos típicos): cap visual + "mostrar más".
 */
import { memo, useState } from 'react'
import { Star, Zap } from 'lucide-react'
import { cop } from '@/components/shared.jsx'

const CAP_VISUAL = 60

const CardProducto = memo(function CardProducto({ p, enCarrito, esFav, resaltado, onTap, onFav }) {
  return (
    <div className="relative">
      <button onClick={() => onTap(p)}
        aria-label={`Agregar ${p.nombre}`}
        className={`w-full h-full flex flex-col items-start gap-0.5 p-2 pr-7 rounded-md border text-left transition-colors ${
          resaltado ? 'border-primary bg-primary/10' : 'border-border bg-surface hover:bg-surface-2'}`}>
        <span className="text-caption font-medium leading-tight line-clamp-2 min-h-[2em]">{p.nombre}</span>
        <span className="text-caption tabular text-muted-foreground">{cop(Number(p.precio_venta))}</span>
      </button>
      {enCarrito > 0 && (
        <span className="absolute -top-1.5 -left-1.5 min-w-5 h-5 px-1 grid place-items-center rounded-full bg-primary text-primary-foreground text-[10px] font-bold tabular"
          aria-label={`${enCarrito} en el carrito`}>
          {enCarrito}
        </span>
      )}
      <button onClick={(e) => { e.stopPropagation(); onFav(p.id) }}
        aria-label={esFav ? `Quitar ${p.nombre} de favoritos` : `Marcar ${p.nombre} como favorito`}
        aria-pressed={esFav}
        className="absolute top-1 right-1 size-5 grid place-items-center rounded text-muted-foreground hover:text-warning">
        <Star className={`size-3.5 ${esFav ? 'fill-warning text-warning' : ''}`} />
      </button>
    </div>
  )
})

function Chip({ activo, onClick, children }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={activo}
      className={`inline-flex items-center gap-1 h-7 px-2.5 rounded-full border text-caption whitespace-nowrap transition-colors ${
        activo ? 'border-primary bg-primary/10 text-primary font-medium'
          : 'border-border bg-surface text-muted-foreground hover:bg-surface-2'}`}>
      {children}
    </button>
  )
}

export default function GrillaCatalogo({
  productos,            // lista a pintar: filtrada (buscando) o el catálogo completo (sin término)
  buscando,             // hay término activo → se ignoran los chips y se pinta `productos` tal cual
  fuente,               // 'local' | 'servidor' (hint de búsqueda inteligente)
  frecuentesIds,        // Set de ids frecuentes (GET /productos/frecuentes)
  favoritos,            // Set de ids favoritos
  onToggleFav,
  cantidades,           // Map producto_id → cantidad en carrito (badge)
  categorias,           // categorías reales del tenant (derivadas del catálogo)
  chip, setChip,        // chip activo: 'favs' | 'frecuentes' | 'todo' | <categoría>
  sel,                  // índice resaltado por teclado (solo aplica buscando)
  onTap,
}) {
  const [verTodos, setVerTodos] = useState(false)

  let lista = productos
  if (!buscando) {
    if (chip === 'favs') lista = productos.filter(p => favoritos.has(p.id))
    else if (chip === 'frecuentes') lista = productos.filter(p => frecuentesIds.has(p.id))
    else if (chip !== 'todo') lista = productos.filter(p => p.categoria === chip)
  }
  const visibles = verTodos ? lista : lista.slice(0, CAP_VISUAL)

  return (
    <div className="mt-3">
      {!buscando && (
        <div className="flex items-center gap-1.5 overflow-x-auto pb-1 -mx-1 px-1" role="group" aria-label="Filtros del catálogo">
          <Chip activo={chip === 'favs'} onClick={() => { setChip('favs'); setVerTodos(false) }}>
            <Star className="size-3" /> Favoritos
          </Chip>
          <Chip activo={chip === 'frecuentes'} onClick={() => { setChip('frecuentes'); setVerTodos(false) }}>
            <Zap className="size-3" /> Frecuentes
          </Chip>
          <Chip activo={chip === 'todo'} onClick={() => { setChip('todo'); setVerTodos(false) }}>Todo</Chip>
          {categorias.map(c => (
            <Chip key={c} activo={chip === c} onClick={() => { setChip(c); setVerTodos(false) }}>{c}</Chip>
          ))}
        </div>
      )}

      {buscando && fuente === 'servidor' && lista.length > 0 && (
        <p className="text-caption text-info mb-1.5">búsqueda inteligente (alias y parecidos)</p>
      )}

      {lista.length === 0 ? (
        <p className="py-8 text-center text-body-sm text-muted-foreground">
          {buscando ? 'Sin resultados — revisa la escritura o usa la venta varia.'
            : chip === 'favs' ? 'Marca productos con la estrella y quedarán aquí.'
            : 'Sin productos en esta vista.'}
        </p>
      ) : (
        <>
          <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1.5" role="list" aria-label="Productos">
            {visibles.map((p, i) => (
              <CardProducto key={p.id} p={p}
                enCarrito={cantidades.get(p.id) || 0}
                esFav={favoritos.has(p.id)}
                resaltado={buscando && i === sel}
                onTap={onTap} onFav={onToggleFav} />
            ))}
          </div>
          {!verTodos && lista.length > CAP_VISUAL && (
            <button onClick={() => setVerTodos(true)}
              className="w-full mt-2 h-8 rounded-md border border-border text-caption text-muted-foreground hover:bg-surface-2">
              Mostrar los {lista.length - CAP_VISUAL} restantes
            </button>
          )}
        </>
      )}
    </div>
  )
}
