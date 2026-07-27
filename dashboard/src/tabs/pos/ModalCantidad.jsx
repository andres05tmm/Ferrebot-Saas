/*
 * ModalCantidad — al tocar un producto que se vende por fracción o sub-unidad, abre el modal de
 * captura (réplica del dashboard viejo): pintura por fracción de galón, lija por cm, puntilla por
 * gramos, tintilla por ml, producto por kilo. Determina la CANTIDAD decimal; el precio final de la
 * línea lo pone el servidor vía /precio, y el total de abajo a la derecha es EDITABLE (regatear un
 * monto) → si el cajero lo cambia, esa cifra viaja como precio de la línea (override).
 *
 * Tipo `unidad` (fallback): captura de cantidad para productos unitarios — el botón # de la card lo
 * abre para vender 400 tornillos sin taps repetidos. Enter confirma.
 */
import { useState } from 'react'
import { cop } from '@/components/shared.jsx'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { Label } from '@/components/ui/label.jsx'
import { Seg } from './piezas.jsx'
import {
  atajosKg, fraccionQueCasa, fraccionesOrdenadas, paqueteCompleto, paqueteDe, previewMotor,
  subunidadesDesdePesos, tipoVenta,
} from './cantidad.js'

// Etiquetas del envase por tipo de granel con botones de paquete (gramos/ml).
const ENVASE = {
  gramos: { sub: 'g', nombre: 'caja', full: 'Caja completa', half: '½ caja', quarter: '¼ caja' },
  ml: { sub: 'ml', nombre: 'tarro', full: 'Tarro completo', half: '½ tarro', quarter: '¼ tarro' },
}

function kgDesc(n) {
  if (!n) return ''
  const ent = Math.floor(n)
  const medio = Math.abs(n - ent - 0.5) < 0.001
  if (medio && ent === 0) return '½ kg'
  if (medio) return `${ent}½ kg`
  return `${n} kg`
}

// Botón "KPI" (réplica del viejo): título grande + precio neutro debajo. Se usa para las fracciones
// de pintura y los accesos rápidos de granel/kg.
function BotonKpi({ activo, onClick, titulo, precio, sub }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={activo}
      className={`flex flex-col items-center justify-center gap-1 h-14 rounded-md border px-2 text-center transition-colors ${
        activo ? 'border-primary bg-primary/10' : 'border-border bg-surface hover:bg-surface-2'}`}>
      <span className={`text-body-sm font-semibold leading-none ${activo ? 'text-primary' : 'text-foreground'}`}>{titulo}</span>
      {precio != null
        ? <span className="text-caption tabular leading-none text-muted-foreground">{cop(precio)}</span>
        : sub ? <span className="text-caption leading-none text-muted-foreground">{sub}</span> : null}
    </button>
  )
}

export default function ModalCantidad({ prod, onCerrar, onConfirmar }) {
  const tipo = prod ? (tipoVenta(prod) || 'unidad') : null
  return (
    <Dialog open={prod != null} onOpenChange={(o) => { if (!o) onCerrar() }}>
      {/* `unidad` es solo cantidad + total: cuadro compacto (en móvil el grande tapaba media pantalla). */}
      <DialogContent aria-describedby="cant-desc" className={tipo === 'unidad' ? 'max-w-xs gap-3' : undefined}>
        {prod && (
          <FormCantidad key={prod.id} prod={prod} tipo={tipo}
            onConfirmar={onConfirmar} onCancelar={onCerrar} />
        )}
      </DialogContent>
    </Dialog>
  )
}

function FormCantidad({ prod, tipo, onConfirmar, onCancelar }) {
  const pv = Number(prod.precio_venta) || 0
  const paquete = paqueteDe(prod)
  const empaque = paqueteCompleto(prod)

  // Estado (un solo hook por campo; el `key={prod.id}` del padre lo resetea entre productos).
  const [unidades, setUnidades] = useState(0)        // pintura: galones/unidades completas
  const [fracSel, setFracSel] = useState(null)       // pintura: fila de fracción elegida (o null)
  const [modo, setModo] = useState('sub')            // gramos/ml: 'sub' | 'pesos'
  const [valor, setValor] = useState('')             // gramos/ml: sub-unidades o pesos según `modo`
  const [cmVal, setCmVal] = useState('')             // cm
  const [kgVal, setKgVal] = useState('')             // kg
  const [uniVal, setUniVal] = useState('')           // unidad: cantidad entera
  // Se está vendiendo el EMPAQUE entero (la bolsa a su precio fijo), no unidades sueltas. Lo prende
  // el botón del empaque y lo apaga cualquier otra captura: el precio de bulto nunca se adivina.
  const [porEmpaque, setPorEmpaque] = useState(false)
  // Total editable de abajo: `precio` es lo que se muestra/edita; `tocado` marca que el cajero lo
  // cambió a mano (regateo). Mientras no lo toque, sigue al total calculado.
  const [precio, setPrecio] = useState('')
  const [tocado, setTocado] = useState(false)

  const r = resolver()                               // { cantidad, precioManual (base), total, desc }
  // El override editable gana sobre el precioManual base (pesos / pintura mixta).
  const precioManual = tocado && Number(precio) > 0 ? Number(precio) : r.precioManual
  const totalMostrado = tocado ? precio : String(Math.round(r.total))
  const valido = r.cantidad > 0 && (precioManual == null || precioManual > 0)

  function resolver() {
    if (tipo === 'fraccion') {
      const cantidad = unidades + (fracSel ? Number(fracSel.decimal) : 0)
      const desc = [unidades > 0 ? `${unidades} u` : '', fracSel ? fracSel.fraccion : '']
        .filter(Boolean).join(' + ')
      // Unidades enteras + fracción a la vez: el motor no lo expresa en una cantidad; el modal ya
      // sabe el total exacto (unidades a precio lleno + la fracción a su precio bonito).
      if (unidades > 0 && fracSel) {
        const total = unidades * pv + Number(fracSel.precio_total)
        return { cantidad, precioManual: total, total, desc }
      }
      return { cantidad, precioManual: null, total: previewMotor(prod, cantidad), desc }
    }
    if (tipo === 'gramos' || tipo === 'ml') {
      const env = ENVASE[tipo]
      const valorNum = Number(valor) || 0
      if (modo === 'pesos') {
        const cantidad = subunidadesDesdePesos(prod, valorNum)
        return { cantidad, precioManual: valorNum, total: valorNum, desc: `${cantidad} ${env.sub}` }
      }
      const cantidad = valorNum
      const desc = paquete && cantidad >= paquete && cantidad % paquete === 0
        ? `${cantidad / paquete} ${env.nombre}(s)` : `${cantidad} ${env.sub}`
      return {
        cantidad, precioManual: null, porEmpaque: porEmpaque && !!empaque,
        total: previewMotor(prod, cantidad, { porEmpaque: porEmpaque && !!empaque }), desc,
      }
    }
    if (tipo === 'cm') {
      const cantidad = Number(cmVal) || 0
      return { cantidad, precioManual: null, total: previewMotor(prod, cantidad), desc: `${cantidad} cm` }
    }
    if (tipo === 'unidad') {
      const cantidad = Number(uniVal) || 0
      return { cantidad, precioManual: null, total: previewMotor(prod, cantidad), desc: cantidad > 0 ? `${cantidad} u` : '' }
    }
    // kg
    const cantidad = Number(kgVal) || 0
    if (porEmpaque && empaque) {
      const n = cantidad / empaque.factor
      return {
        cantidad, precioManual: null, porEmpaque: true,
        total: previewMotor(prod, cantidad, { porEmpaque: true }),
        desc: `${n} ${empaque.nombre}${n === 1 ? '' : 's'} (${cantidad} kg)`,
      }
    }
    return { cantidad, precioManual: null, total: previewMotor(prod, cantidad), desc: kgDesc(cantidad) }
  }

  function confirmar() {
    if (!valido) return
    onConfirmar({ cantidad: r.cantidad, precioManual, desc: r.desc, porEmpaque: !!r.porEmpaque })
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>{prod.nombre}</DialogTitle>
        <DialogDescription id="cant-desc">{subtitulo(tipo, prod, pv, empaque, paquete)}</DialogDescription>
      </DialogHeader>

      <div className="space-y-3">
        {tipo === 'fraccion' && (
          <>
            <div>
              <Label className="text-caption uppercase tracking-wider text-muted-foreground">Unidades completas</Label>
              <div className="mt-1.5 flex items-center gap-2">
                <Button type="button" variant="outline" size="icon" aria-label="Menos"
                  onClick={() => setUnidades((n) => Math.max(0, n - 1))}>−</Button>
                <span className="w-12 text-center text-body font-semibold tabular">{unidades}</span>
                <Button type="button" variant="outline" size="icon" aria-label="Más"
                  onClick={() => setUnidades((n) => n + 1)}>+</Button>
                <span className="text-caption text-muted-foreground">× {cop(pv)}</span>
              </div>
            </div>
            <div>
              <Label className="text-caption uppercase tracking-wider text-muted-foreground">Fracción adicional</Label>
              <div className="mt-1.5 grid grid-cols-3 gap-1.5">
                <BotonKpi activo={fracSel == null} onClick={() => setFracSel(null)}
                  titulo="Ninguna" sub="sólo unidades" />
                {fraccionesOrdenadas(prod).map((f) => (
                  <BotonKpi key={f.fraccion} activo={fracSel?.fraccion === f.fraccion}
                    onClick={() => setFracSel(f)} titulo={f.fraccion} precio={Number(f.precio_total)} />
                ))}
              </div>
            </div>
          </>
        )}

        {(tipo === 'gramos' || tipo === 'ml') && (
          <>
            <div className="grid grid-cols-3 gap-1.5">
              {/* El envase completo va a SU precio (`precio_paquete`); la media y el cuarto son
                  cantidades sueltas a precio de sub-unidad. */}
              {[[ENVASE[tipo].full, paquete, true], [ENVASE[tipo].half, paquete / 2, false],
                [ENVASE[tipo].quarter, paquete / 4, false]]
                .map(([et, q, entero]) => (
                  <BotonKpi key={et} activo={modo === 'sub' && Number(valor) === q && porEmpaque === !!(entero && empaque)}
                    onClick={() => { setModo('sub'); setPorEmpaque(!!(entero && empaque)); setValor(String(q)) }}
                    titulo={et} precio={previewMotor(prod, q, { porEmpaque: !!(entero && empaque) })} />
                ))}
            </div>
            <div className="flex gap-1.5">
              <Seg activo={modo === 'pesos'} onClick={() => { setModo('pesos'); setValor('') }}>$ Pesos</Seg>
              <Seg activo={modo === 'sub'} onClick={() => { setModo('sub'); setValor('') }}>
                {ENVASE[tipo].sub} {tipo === 'gramos' ? 'Gramos' : 'Mililitros'}
              </Seg>
            </div>
            <Input type="number" min="0" step="any" value={valor} autoFocus
              onChange={(e) => setValor(e.target.value)}
              placeholder={modo === 'pesos' ? 'ej: 2000' : `${ENVASE[tipo].sub}`}
              aria-label={modo === 'pesos' ? 'Monto en pesos' : `Cantidad en ${ENVASE[tipo].sub}`} />
          </>
        )}

        {tipo === 'cm' && (
          <div>
            <Label className="text-caption uppercase tracking-wider text-muted-foreground">Cantidad en centímetros</Label>
            <div className="mt-1.5 flex items-center gap-2">
              <Input type="number" min="0" step="any" value={cmVal} autoFocus
                onChange={(e) => setCmVal(e.target.value)} aria-label="Cantidad en centímetros" />
              <span className="text-caption text-muted-foreground">cm</span>
            </div>
          </div>
        )}

        {tipo === 'unidad' && (
          <div>
            <Label className="text-caption uppercase tracking-wider text-muted-foreground">Cantidad</Label>
            <Input type="number" min="0" step="1" value={uniVal} autoFocus className="mt-1.5"
              onChange={(e) => setUniVal(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); confirmar() } }}
              placeholder="ej: 400" aria-label="Cantidad en unidades" />
          </div>
        )}

        {tipo === 'kg' && (
          <>
            {/* Empaque completo (la bolsa de cemento de 40 kg): un toque, a SU precio — no al del
                kilo multiplicado. Vuelve a tocarlo para sumar bolsas. */}
            {empaque && (
              <BotonKpi
                activo={porEmpaque}
                onClick={() => {
                  const n = porEmpaque ? (Number(kgVal) / empaque.factor) + 1 : 1
                  setPorEmpaque(true); setKgVal(String(n * empaque.factor))
                }}
                titulo={`${empaque.nombre} completa (${empaque.factor} kg)`}
                precio={empaque.precio}
              />
            )}
            <div className="grid grid-cols-3 gap-1.5">
              {atajosKg(prod).map(({ etiqueta, cantidad }) => (
                <BotonKpi key={etiqueta} activo={!porEmpaque && Number(kgVal) === cantidad}
                  onClick={() => { setPorEmpaque(false); setKgVal(String(cantidad)) }}
                  titulo={etiqueta} precio={previewMotor(prod, cantidad)} />
              ))}
            </div>
            <Input type="number" min="0" step="0.25" value={kgVal}
              onChange={(e) => { setPorEmpaque(false); setKgVal(e.target.value) }}
              placeholder="kg" aria-label="Cantidad en kilos" />
          </>
        )}

        {/* Total editable: se ve el precio calculado y el cajero puede sobreescribirlo (regatear). */}
        <div className="flex items-center justify-between border-t border-border pt-3">
          <span className="text-caption text-muted-foreground">{r.desc || '—'}</span>
          <div className="flex items-center gap-1">
            <span className="text-body font-semibold text-muted-foreground">$</span>
            <input type="number" min="0" step="any" value={totalMostrado}
              onChange={(e) => { const v = e.target.value; setPrecio(v); setTocado(v !== '') }}
              aria-label="Precio total (editable)"
              className="w-28 bg-transparent text-right text-xl font-bold tabular text-foreground outline-none border-b border-transparent focus:border-primary" />
          </div>
        </div>
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancelar}>Cancelar</Button>
        <Button type="button" disabled={!valido} onClick={confirmar}>Agregar al carrito</Button>
      </DialogFooter>
    </>
  )
}

// `pv` es el precio de UNA unidad de venta (un gramo, un kilo) y `empaque.precio` el del envase
// completo: son dos datos distintos desde 0072, no uno derivado del otro.
function subtitulo(tipo, prod, pv, empaque, paquete) {
  const envase = empaque ? ` · ${cop(empaque.precio)} la ${empaque.nombre} (${empaque.factor})` : ''
  if (tipo === 'fraccion' || tipo === 'unidad') return `Precio unidad: ${cop(pv)}`
  if (tipo === 'gramos') return `${cop(pv)}/g${envase || ` · caja de ${paquete} g`}`
  if (tipo === 'ml') return `${cop(pv)}/ml${envase || ` · tarro de ${paquete} ml`}`
  if (tipo === 'cm') return `${cop(pv)}/cm${envase}`
  if (tipo === 'kg') {
    const half = fraccionQueCasa(prod, 0.5)
    return `${cop(pv)}/kg${half ? ` · ½ kg ${cop(Number(half.precio_total))}` : ''}${envase}`
  }
  return ''
}
