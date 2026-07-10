/*
 * Checkout del POS: método de pago de UN TOQUE (botones grandes, patrón del FerreBot viejo),
 * recibido/cambio prominente, cobro dividido (mixto, F5), documento fiscal, total y Registrar.
 * TODO el estado vive en el tab (este componente es presentación pura por props) — así el guard de
 * caja, la Idempotency-Key y el flujo mixto no se duplican. Alt+1..5 setea el mismo estado.
 */
import { Banknote, CreditCard, HandCoins, Landmark, SplitSquareHorizontal } from 'lucide-react'
import { cop } from '@/components/shared.jsx'
import { Input } from '@/components/ui/input.jsx'
import { Button } from '@/components/ui/button.jsx'
import { METODOS, METODOS_MIXTO_RESTO, Seg } from './piezas.jsx'

const ICONO_METODO = {
  efectivo: Banknote, transferencia: Landmark, datafono: CreditCard,
  fiado: HandCoins, mixto: SplitSquareHorizontal,
}

export default function Checkout({
  metodoPago, setMetodoPago,
  recibido, setRecibido, cambio,
  efectivoMixto, setEfectivoMixto, metodoResto, setMetodoResto, restanteMixto, mixtoValido,
  mostrarDocumento, opcionesDocumento, documento, setDocumento,
  total, enviando, carritoVacio, onRegistrar,
}) {
  return (
    <>
      <label className="text-caption uppercase tracking-wider text-muted-foreground mt-3 mb-1">Método de pago</label>
      <div className="grid grid-cols-5 gap-1" role="group" aria-label="Método de pago">
        {METODOS.map(m => {
          const Icono = ICONO_METODO[m]
          return (
            <button key={m} type="button" onClick={() => setMetodoPago(m)}
              aria-label={`Pago ${m}`} aria-pressed={metodoPago === m}
              className={`flex flex-col items-center gap-0.5 py-2 rounded-md border text-[10px] capitalize transition-colors ${
                metodoPago === m ? 'border-primary bg-primary/10 text-primary font-semibold'
                  : 'border-border bg-surface text-muted-foreground hover:bg-surface-2'}`}>
              <Icono className="size-4" aria-hidden="true" />
              {m}
            </button>
          )
        })}
      </div>

      {metodoPago === 'efectivo' && (
        <div className="mt-2">
          <Input type="number" min="0" step="any" value={recibido} onChange={(e) => setRecibido(e.target.value)}
            placeholder="Recibido" aria-label="Efectivo recibido" className="h-9" />
          {cambio != null && (
            <p className="mt-1.5 text-right tabular">
              <span className="text-caption uppercase tracking-wider text-muted-foreground mr-2">Cambio</span>
              <span className="text-2xl font-semibold text-success">{cop(cambio)}</span>
            </p>
          )}
        </div>
      )}

      {metodoPago === 'mixto' && (
        <div className="mt-2 space-y-1.5">
          <div className="flex items-center gap-2">
            <Input type="number" min="0" step="any" value={efectivoMixto}
              onChange={(e) => setEfectivoMixto(e.target.value)}
              placeholder="Efectivo" aria-label="Parte en efectivo" className="h-9 flex-1" />
            <div className="flex items-center gap-1" role="group" aria-label="Método del resto">
              {METODOS_MIXTO_RESTO.map(m => (
                <Seg key={m} activo={metodoResto === m} onClick={() => setMetodoResto(m)}
                  aria-label={`Resto por ${m}`}>{m}</Seg>
              ))}
            </div>
          </div>
          <p className={`text-caption tabular ${mixtoValido ? 'text-muted-foreground' : 'text-destructive'}`}>
            {mixtoValido
              ? <>Resto por {metodoResto}: <span className="font-semibold">{cop(restanteMixto)}</span></>
              : 'El efectivo debe ser mayor que 0 y menor que el total'}
          </p>
        </div>
      )}

      {mostrarDocumento && (
        <>
          <label className="text-caption uppercase tracking-wider text-muted-foreground mt-3 mb-1">Documento</label>
          {opcionesDocumento.length > 1 ? (
            <div className="flex items-center gap-1 flex-wrap" role="group" aria-label="Documento fiscal">
              {opcionesDocumento.map(({ v, label }) => (
                <Seg key={v} activo={documento === v} onClick={() => setDocumento(v)}
                  aria-label={`Documento ${label}`}>{label}</Seg>
              ))}
            </div>
          ) : (
            <div className="text-body-sm text-muted-foreground">{opcionesDocumento[0]?.label}</div>
          )}
        </>
      )}

      <div className="flex items-center justify-between mt-3 mb-2">
        <span className="text-caption uppercase tracking-wider text-muted-foreground">Total</span>
        <span className="text-xl font-semibold tabular">{cop(total)}</span>
      </div>
      <Button onClick={onRegistrar}
        disabled={enviando || carritoVacio || (metodoPago === 'mixto' && !mixtoValido)}
        className="w-full h-10">
        {enviando ? 'Registrando…' : 'Registrar venta'} <span className="ml-1.5 opacity-70 text-caption">F9</span>
      </Button>
    </>
  )
}
