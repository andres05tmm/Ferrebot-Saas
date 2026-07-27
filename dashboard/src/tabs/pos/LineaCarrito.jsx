/*
 * LineaCarrito — una línea del carrito del POS. El precio de catálogo viene del SERVIDOR
 * (prop `precio` = respuesta de GET /productos/{id}/precio); varia/especial son explícitos del cliente.
 */
import { Trash2 } from '@/lib/icons.jsx'
import { cop } from '@/components/shared.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Seg } from './piezas.jsx'

const FRACCIONES = [['¼', 0.25], ['½', 0.5], ['¾', 0.75], ['1', 1]]
const GRANEL = { grm: 'g', gramos: 'g', cms: 'cm' }   // sub-unidades de venta a granel

export default function LineaCarrito({ it, precio, onCantidad, onQuitar, onEspecial, onEmpaque }) {
  const granel = !it.varia && GRANEL[(it.unidad_medida || '').toLowerCase()]
  // Empaque entero (0072): el bulto de cemento a precio fijo. Solo aparece si el producto tiene las
  // dos mitades del dato — cuántas unidades trae y cuánto vale completo.
  const contenido = Number(it.contenido_paquete) || 0
  const empaque = !it.varia && contenido > 0 && it.precio_paquete != null
  const empaques = empaque && it.por_empaque ? Number(it.cantidad) / contenido : null
  // Precio a mano / pesos: total explícito de la línea (no consulta al servidor).
  const manual = !it.varia && it.precio_manual != null
  const usaServidor = !it.varia && !it.usarEspecial && !manual
  const cargando = usaServidor && precio?.loading
  const unit = it.varia ? Number(it.precio_unitario)
    : manual ? Number(it.precio_manual) / (Number(it.cantidad) || 1)
    : it.usarEspecial ? Number(it.precio_especial)
    : precio?.precio_unitario
  const faltanMayorista = it.precio_umbral != null && !it.usarEspecial &&
    Number(it.cantidad) > 0 && Number(it.cantidad) < it.precio_umbral

  return (
    <li className="py-2">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-body-sm truncate">{it.nombre}</div>
          {it.desc && <div className="text-caption text-muted-foreground truncate">{it.desc}</div>}
          <div className="text-caption text-muted-foreground tabular flex items-center gap-1.5">
            {cargando ? 'calculando…' : unit != null ? `${cop(unit)} c/u` : '—'}
            {precio?.regla && precio.regla !== 'simple' && !it.usarEspecial && (
              <span className="rounded bg-info/15 text-info px-1 text-micro uppercase">{precio.regla}</span>
            )}
            {granel && <span className="text-micro uppercase">/{granel}</span>}
          </div>
        </div>
        <Input type="number" min="0" step="any" value={it.cantidad}
          onChange={(e) => onCantidad(e.target.value)}
          aria-label={`Cantidad de ${it.nombre}`} className="w-16 h-8 text-center" />
        <span className="w-20 text-right text-body-sm tabular shrink-0">
          {cop(it.varia ? Number(it.precio_unitario) * Number(it.cantidad || 0)
            : manual ? Number(it.precio_manual)
            : it.usarEspecial ? Number(it.precio_especial) * Number(it.cantidad || 0)
            : (precio?.total ?? Number(it.precio_normal || 0) * Number(it.cantidad || 0)))}
        </span>
        <button onClick={onQuitar} aria-label={`Quitar ${it.nombre}`}
          className="size-8 grid place-items-center rounded-md text-muted-foreground hover:text-destructive">
          <Trash2 className="size-4" />
        </button>
      </div>

      {/* Multiplicadores (patrón del FerreBot viejo): SETEAN la cantidad — predecible al ojo; la
          edición libre queda en el input. El precio se re-consulta solo (efecto firmaPrecios).
          Vendiendo por bulto multiplican BULTOS, no unidades sueltas. */}
      {!it.varia && (
        <div className="mt-1.5 flex items-center gap-1" role="group" aria-label={`Cantidad rápida de ${it.nombre}`}>
          {[2, 5, 10].map(n => {
            const cant = it.por_empaque ? n * contenido : n
            return (
              <Seg key={n} activo={Number(it.cantidad) === cant} onClick={() => onCantidad(String(cant))}
                aria-label={`×${n} de ${it.nombre}`}>×{n}</Seg>
            )
          })}
        </div>
      )}
      {empaque && (
        <div className="mt-1.5 flex items-center gap-1" role="group" aria-label={`Presentación de ${it.nombre}`}>
          <Seg activo={!it.por_empaque} onClick={() => onEmpaque(false)}>
            Suelto{granel ? '' : ` (${it.unidad_medida})`}
          </Seg>
          <Seg activo={!!it.por_empaque} onClick={() => onEmpaque(true)}>
            {it.nombre_paquete || 'Empaque'} de {contenido}
          </Seg>
          {empaques != null && (
            <span className="text-caption text-muted-foreground tabular">
              {empaques} × {cop(Number(it.precio_paquete))}
            </span>
          )}
        </div>
      )}
      {it.permite_fraccion && !it.usarEspecial && (
        <div className="mt-1.5 flex items-center gap-1" role="group" aria-label={`Fracción de ${it.nombre}`}>
          {FRACCIONES.map(([et, val]) => (
            <Seg key={et} activo={Number(it.cantidad) === val} onClick={() => onCantidad(String(val))}
              aria-label={`${et} de ${it.nombre}`}>{et}</Seg>
          ))}
        </div>
      )}
      {faltanMayorista && (
        <p className="mt-1 text-caption text-info">
          ≥ {it.precio_umbral} u: {cop(it.precio_sobre_umbral)} c/u — te faltan {it.precio_umbral - Number(it.cantidad)} para mayorista
        </p>
      )}
      {!it.varia && it.precio_especial != null && (
        <div className="mt-1.5 flex items-center gap-1" role="group" aria-label={`Precio de ${it.nombre}`}>
          <Seg activo={!it.usarEspecial} onClick={() => onEspecial(false)}>Normal</Seg>
          <Seg activo={it.usarEspecial} onClick={() => onEspecial(true)}>Especial {cop(it.precio_especial)}</Seg>
        </div>
      )}
    </li>
  )
}
