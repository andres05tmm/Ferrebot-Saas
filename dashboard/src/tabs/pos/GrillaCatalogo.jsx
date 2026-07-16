/*
 * GrillaCatalogo — la vitrina tap-first del POS, réplica del diseño del FerreBot viejo.
 *
 * Chips con icono ([Todos] [★ Favoritos] [🏆 Top productos] [categorías del tenant]) + selector de
 * columnas (4/5/6, persistido). En "Todos" los productos van AGRUPADOS por categoría con header de
 * sección (icono + título + línea + conteo), empezando por TOP PRODUCTOS DEL MES (frecuentes). Las
 * cards llevan icono de categoría, estrella de favorito, precio en rojo y badge % si hay precio
 * mayorista. Buscando, la lista llega YA filtrada/rankeada del tab (local o respaldo del servidor).
 * El precio de la card es REFERENCIA del catálogo — el real lo pone el servidor al agregar.
 */
import { memo, useEffect, useMemo, useState } from 'react'
import { LayoutGrid, Percent, Star, Trophy } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { guardarLS, leerLS } from './piezas.jsx'
import { etiquetaCategoria, iconoCategoria } from './categorias.js'
import { filtrarSubcat, ordenarProductos, subcatsDe } from './subcategorias.js'

const CAP_SECCION = 12
const COLS_KEY = 'pos_cols_v1'
// Con la grilla a ancho completo (el carrito ya no es columna fija) caben hasta 8 columnas.
const COLS_CLS = {
  4: 'lg:grid-cols-4', 5: 'lg:grid-cols-5', 6: 'lg:grid-cols-6',
  7: 'lg:grid-cols-7', 8: 'lg:grid-cols-8',
}

const CardProducto = memo(function CardProducto({ p, enCarrito, esFav, resaltado, onTap, onFav }) {
  const { Icono, color } = iconoCategoria(p.categoria)
  const mayorista = p.precio_umbral != null && p.precio_sobre_umbral != null
  return (
    <div className="relative">
      <button onClick={() => onTap(p)}
        aria-label={`Agregar ${p.nombre}`}
        className={`w-full h-full flex flex-col items-start gap-1 p-2.5 pr-7 rounded-lg border text-left transition-colors ${
          resaltado ? 'border-primary bg-primary/10' : 'border-border bg-surface hover:border-primary/40 hover:bg-surface-2'}`}>
        <Icono className={`size-4 ${color}`} aria-hidden="true" />
        <span className="text-caption font-medium leading-tight line-clamp-2 min-h-[2em]">{p.nombre}</span>
        <span className="text-body-sm font-semibold tabular text-foreground">{cop(Number(p.precio_venta))}</span>
      </button>
      {enCarrito > 0 && (
        <span className="absolute -top-1.5 -left-1.5 min-w-5 h-5 px-1 grid place-items-center rounded-full bg-primary text-primary-foreground text-[10px] font-bold tabular"
          aria-label={`${enCarrito} en el carrito`}>
          {enCarrito}
        </span>
      )}
      {mayorista && (
        <span className="absolute bottom-1.5 right-1.5 size-4 grid place-items-center rounded border border-info/30 text-info"
          title={`Mayorista: ≥${p.precio_umbral} u a ${cop(Number(p.precio_sobre_umbral))}`}>
          <Percent className="size-2.5" aria-label="Tiene precio mayorista" />
        </span>
      )}
      <button onClick={(e) => { e.stopPropagation(); onFav(p.id) }}
        aria-label={esFav ? `Quitar ${p.nombre} de favoritos` : `Marcar ${p.nombre} como favorito`}
        aria-pressed={esFav}
        className="absolute top-1.5 right-1.5 size-6 grid place-items-center rounded text-muted-foreground/60 hover:text-warning">
        <Star className={`size-3.5 ${esFav ? 'fill-warning text-warning' : ''}`} />
      </button>
    </div>
  )
})

function Chip({ activo, onClick, children }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={activo}
      className={`inline-flex items-center gap-1.5 h-8 px-3 rounded-full border text-caption whitespace-nowrap transition-colors ${
        activo ? 'border-primary bg-primary/10 text-primary font-semibold'
          : 'border-border bg-surface text-muted-foreground hover:bg-surface-2'}`}>
      {children}
    </button>
  )
}

// Header de sección del viejo: icono + TÍTULO + línea divisoria que corre + conteo a la derecha.
function HeaderSeccion({ Icono, color, titulo, conteo }) {
  return (
    <div className="flex items-center gap-2 mt-4 mb-2 first:mt-3">
      <Icono className={`size-4 shrink-0 ${color}`} aria-hidden="true" />
      <h3 className="text-caption font-semibold uppercase tracking-wider text-muted-foreground whitespace-nowrap">{titulo}</h3>
      <span className="h-px flex-1 bg-border" aria-hidden="true" />
      <span className="text-caption tabular text-muted-foreground">{conteo}</span>
    </div>
  )
}

function Seccion({ id, Icono, color, titulo, items, gridCls, render }) {
  const [expandida, setExpandida] = useState(false)
  const visibles = expandida ? items : items.slice(0, CAP_SECCION)
  return (
    <section aria-label={titulo}>
      <HeaderSeccion Icono={Icono} color={color} titulo={titulo} conteo={items.length} />
      <div className={gridCls} role="list">
        {visibles.map(render)}
      </div>
      {!expandida && items.length > CAP_SECCION && (
        <button onClick={() => setExpandida(true)}
          className="w-full mt-1.5 h-8 rounded-md border border-border text-caption text-muted-foreground hover:bg-surface-2">
          Ver los {items.length - CAP_SECCION} restantes de {titulo.toLowerCase()}
        </button>
      )}
    </section>
  )
}

export default function GrillaCatalogo({
  productos,            // lista a pintar: filtrada (buscando) o el catálogo completo (sin término)
  buscando,             // hay término activo → se ignoran los chips y se pinta `productos` tal cual
  fuente,               // 'local' | 'servidor' (hint de búsqueda inteligente)
  frecuentesIds,        // Set de ids frecuentes (GET /productos/frecuentes) = "Top productos del mes"
  favoritos, onToggleFav,
  cantidades,           // Map producto_id → cantidad en carrito (badge)
  categorias,           // categorías reales del tenant (derivadas del catálogo)
  chip, setChip,        // chip activo: 'favs' | 'top' | 'todo' | <categoría>
  sel,                  // índice resaltado por teclado (solo aplica buscando)
  onTap,
  slotBusqueda,         // SOLO el input de búsqueda: va PRIMERO en la barra sticky
  slotExtras,           // más vendidos / hints del tab: debajo de la barra, scrollea con el contenido
}) {
  const [cols, setCols] = useState(() => {
    const c = Number(leerLS(COLS_KEY, [6])[0])
    return COLS_CLS[c] ? c : 6
  })
  const gridCls = `grid grid-cols-2 sm:grid-cols-3 ${COLS_CLS[cols]} gap-1.5`

  // Subcategorías (réplica del viejo): al elegir una categoría aparece la segunda fila de chips
  // (Brochas/Rodillos, Lijas, Drywall ×6…). Cambiar de categoría resetea la subcategoría.
  const subs = useMemo(() => subcatsDe(chip), [chip])
  const [subcat, setSubcat] = useState(null)
  useEffect(() => { setSubcat(null) }, [chip])

  const render = (p, i) => (
    <CardProducto key={p.id} p={p}
      enCarrito={cantidades.get(p.id) || 0}
      esFav={favoritos.has(p.id)}
      resaltado={buscando && i === sel}
      onTap={onTap} onFav={onToggleFav} />
  )

  // Secciones de la vista "Todos": Top del mes primero, luego cada categoría en su orden natural
  // (los nombres del catálogo traen número de orden: "1 Artículos…", "2 Pinturas…").
  const secciones = useMemo(() => {
    if (buscando || chip !== 'todo') return []
    const s = []
    const top = productos.filter(p => frecuentesIds.has(p.id))
    if (top.length) s.push({ id: 'top', Icono: Trophy, color: 'text-warning', titulo: 'Top productos del mes', items: top })
    for (const c of categorias) {
      const items = ordenarProductos(c, productos.filter(p => p.categoria === c))
      if (items.length) {
        const { Icono, color } = iconoCategoria(c)
        s.push({ id: c, Icono, color, titulo: etiquetaCategoria(c), items })
      }
    }
    const sinCat = productos.filter(p => !p.categoria)
    if (sinCat.length) {
      s.push({ id: '_sin', Icono: LayoutGrid, color: 'text-muted-foreground', titulo: 'Sin categoría', items: sinCat })
    }
    return s
  }, [buscando, chip, productos, categorias, frecuentesIds])

  let lista = productos
  let cabecera = null
  if (!buscando && chip !== 'todo') {
    if (chip === 'favs') {
      lista = productos.filter(p => favoritos.has(p.id))
      cabecera = { Icono: Star, color: 'text-warning', titulo: 'Favoritos' }
    } else if (chip === 'top') {
      lista = productos.filter(p => frecuentesIds.has(p.id))
      cabecera = { Icono: Trophy, color: 'text-warning', titulo: 'Top productos del mes' }
    } else {
      lista = ordenarProductos(chip, productos.filter(p => p.categoria === chip))
      if (subcat) lista = filtrarSubcat(lista, subs, subcat)
      const sub = subs.find(s => s.key === subcat)
      cabecera = sub
        ? { Icono: sub.Icono, color: iconoCategoria(chip).color, titulo: `${etiquetaCategoria(chip)} · ${sub.label}` }
        : { ...iconoCategoria(chip), titulo: etiquetaCategoria(chip) }
    }
  }

  return (
    <div>
      {/* Barra sticky: búsqueda PRIMERO (el camino más rápido), luego filtros. Queda pegada bajo el
          header de la app (h-14) al scrollear el catálogo — con 600+ productos los filtros nunca se
          pierden. Los -mx compensan el padding del <main> para que el fondo cubra el ancho completo. */}
      <div className="sticky top-14 z-20 bg-background -mx-4 md:-mx-6 px-4 md:px-6 pt-1 pb-2 border-b border-border-subtle space-y-2.5">
        {slotBusqueda}

        {!buscando && (
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1 -mx-1 px-1 flex-1" role="group" aria-label="Filtros del catálogo">
              <Chip activo={chip === 'todo'} onClick={() => setChip('todo')}>
                <LayoutGrid className="size-3.5" /> Todos
              </Chip>
              <Chip activo={chip === 'favs'} onClick={() => setChip('favs')}>
                <Star className="size-3.5" /> Favoritos
              </Chip>
              <Chip activo={chip === 'top'} onClick={() => setChip('top')}>
                <Trophy className="size-3.5" /> Top productos
              </Chip>
              {categorias.map(c => {
                const { Icono, color } = iconoCategoria(c)
                return (
                  <Chip key={c} activo={chip === c} onClick={() => setChip(c)}>
                    <Icono className={`size-3.5 ${color}`} /> {etiquetaCategoria(c)}
                  </Chip>
                )
              })}
            </div>
            <div className="hidden lg:flex items-center gap-1 shrink-0" role="group" aria-label="Columnas de la grilla">
              <span className="text-caption text-muted-foreground mr-0.5">Col:</span>
              {Object.keys(COLS_CLS).map(Number).map(n => (
                <button key={n} onClick={() => { setCols(n); guardarLS(COLS_KEY, [n]) }}
                  aria-label={`${n} columnas`} aria-pressed={cols === n}
                  className={`size-7 grid place-items-center rounded-md border text-caption tabular transition-colors ${
                    cols === n ? 'border-primary bg-primary/10 text-primary font-semibold'
                      : 'border-border text-muted-foreground hover:bg-surface-2'}`}>
                  {n}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Segunda fila: subcategorías de la categoría elegida (réplica del viejo). */}
        {!buscando && subs.length > 0 && (
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 -mx-1 px-1" role="group"
            aria-label={`Subcategorías de ${etiquetaCategoria(chip)}`}>
            <Chip activo={subcat == null} onClick={() => setSubcat(null)}>Todas</Chip>
            {subs.map(s => (
              <Chip key={s.key} activo={subcat === s.key} onClick={() => setSubcat(s.key)}>
                <s.Icono className="size-3.5" /> {s.label}
              </Chip>
            ))}
          </div>
        )}
      </div>

      {slotExtras}

      {buscando && fuente === 'servidor' && productos.length > 0 && (
        <p className="text-caption text-info mt-2">búsqueda inteligente (alias y parecidos)</p>
      )}

      {buscando || chip !== 'todo' ? (
        lista.length === 0 ? (
          <p className="py-8 text-center text-body-sm text-muted-foreground">
            {buscando ? 'Sin resultados — revisa la escritura o usa la venta miscelánea.'
              : chip === 'favs' ? 'Marca productos con la estrella y quedarán aquí.'
              : 'Sin productos en esta vista.'}
          </p>
        ) : (
          <>
            {cabecera && <HeaderSeccion {...cabecera} conteo={lista.length} />}
            <div className={`${gridCls} ${cabecera ? '' : 'mt-2'}`} role="list" aria-label="Productos">
              {lista.map(render)}
            </div>
          </>
        )
      ) : (
        secciones.length === 0 ? (
          <p className="py-8 text-center text-body-sm text-muted-foreground">El catálogo está vacío.</p>
        ) : (
          secciones.map(s => (
            <Seccion key={s.id} {...s} gridCls={gridCls} render={render} />
          ))
        )
      )}
    </div>
  )
}
