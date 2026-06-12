import { ArrowUpRight } from 'lucide-react'
import { ORDEN, VERTICALES, urlDemo } from '@/lib/verticales.js'
import { cn } from '@/lib/utils'

/*
 * Acordeón de verticales — rework de `interactive-image-accordion` (21st.dev, thanh):
 * mismas proporciones y easing, pero con nuestros verticales: tocar un panel
 * retematiza el acento de toda la página y cambia la conversación del teléfono;
 * el panel activo linkea a la demo en vivo de ese negocio.
 */

function Panel({ clave, activa, onElegir }) {
  const datos = VERTICALES[clave]
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={activa}
      onClick={() => onElegir(clave)}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onElegir(clave) } }}
      onMouseEnter={() => onElegir(clave)}
      className={cn(
        'relative h-[300px] cursor-pointer overflow-hidden rounded-2xl outline-none transition-all duration-700 ease-marca md:h-[440px]',
        'focus-visible:ring-2 focus-visible:ring-acento focus-visible:ring-offset-2 focus-visible:ring-offset-fondo',
        activa ? 'flex-[5] md:flex-[6]' : 'flex-1',
      )}
    >
      <img
        src={datos.foto}
        alt={datos.etiqueta}
        loading="lazy"
        className="absolute inset-0 h-full w-full object-cover"
      />
      <div
        className={cn(
          'absolute inset-0 transition-opacity duration-700',
          activa
            ? 'bg-gradient-to-t from-black/75 via-black/15 to-transparent opacity-100'
            : 'bg-black/45 opacity-100',
        )}
      />
      {/* etiqueta vertical cuando el panel está plegado */}
      <span
        className={cn(
          'absolute whitespace-nowrap font-semibold text-white transition-all duration-500 ease-marca',
          activa
            ? 'pointer-events-none bottom-auto left-5 top-5 rotate-0 text-sm uppercase tracking-widest opacity-80'
            : 'bottom-20 left-1/2 -translate-x-1/2 rotate-90 text-base',
        )}
      >
        {datos.etiqueta}
      </span>
      {/* contenido del panel activo */}
      <div
        className={cn(
          'absolute inset-x-5 bottom-5 transition-all delay-200 duration-500 ease-marca',
          activa ? 'translate-y-0 opacity-100' : 'pointer-events-none translate-y-3 opacity-0',
        )}
      >
        <p className="font-display text-2xl font-semibold text-white">{datos.nombre}</p>
        <p className="mt-0.5 text-sm text-white/75">{datos.pie}</p>
        <a
          href={urlDemo(clave)}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-white/95 px-4 py-2 text-sm font-semibold text-tinta transition-transform duration-300 hover:scale-[1.03]"
        >
          Entrar a la demo <ArrowUpRight className="size-4" />
        </a>
      </div>
    </div>
  )
}

export default function AcordeonVerticales({ vertical, onElegir }) {
  return (
    <div className="flex flex-row items-stretch gap-3 md:gap-4">
      {ORDEN.map((clave) => (
        <Panel key={clave} clave={clave} activa={vertical === clave} onElegir={onElegir} />
      ))}
    </div>
  )
}
