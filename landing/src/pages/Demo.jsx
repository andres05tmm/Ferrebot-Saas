import { useEffect } from 'react'
import { ArrowUpRight } from 'lucide-react'
import Nav from '@/components/Nav.jsx'
import CierreYPie from '@/components/CierreYPie.jsx'
import { BlurFade } from '@/components/ui/blur-fade.jsx'
import { ORDEN, VERTICALES, urlDemo } from '@/lib/verticales.js'

/* /demo — selector de las 4 demos en vivo: cada una es un tenant real en su subdominio. */
export default function Demo() {
  useEffect(() => {
    document.documentElement.dataset.vertical = 'neutro'
  }, [])

  return (
    <>
      <Nav />
      <main className="px-5 pb-20 pt-32 sm:px-10 md:pt-40">
        <div className="mx-auto max-w-6xl">
          <BlurFade>
            <h1 className="max-w-[18ch] font-display text-4xl font-semibold leading-[1.05] tracking-tight md:text-6xl">
              Elige un negocio y <em className="not-italic text-acento">úsalo</em>.
            </h1>
          </BlurFade>
          <BlurFade delay={0.1}>
            <p className="mt-4 max-w-[52ch] text-[17px] text-texto-2">
              Cada demo es un negocio ficticio funcionando de verdad: agenda, pedidos y
              conversaciones del agente. Tócala, nada se daña.
            </p>
          </BlurFade>
          <div className="mt-12 grid gap-5 sm:grid-cols-2">
            {ORDEN.map((clave, i) => {
              const datos = VERTICALES[clave]
              return (
                <BlurFade key={clave} delay={0.12 + i * 0.08}>
                  <a
                    href={urlDemo(clave)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group relative block h-60 overflow-hidden rounded-2xl border border-linea shadow-marca transition-transform duration-300 ease-marca hover:-translate-y-1"
                  >
                    <img
                      src={datos.foto}
                      alt=""
                      loading="lazy"
                      className="absolute inset-0 h-full w-full object-cover transition-transform duration-700 ease-marca group-hover:scale-105"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/25 to-transparent" />
                    <div className="absolute inset-x-6 bottom-5 flex items-end justify-between gap-3">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-widest text-white/70">
                          {datos.etiqueta}
                        </p>
                        <p className="mt-1 font-display text-2xl font-semibold text-white">
                          {datos.nombre}
                        </p>
                        <p className="text-sm text-white/75">{datos.pie}</p>
                      </div>
                      <span className="grid size-11 shrink-0 place-items-center rounded-full bg-white/95 text-tinta transition-transform duration-300 group-hover:rotate-12">
                        <ArrowUpRight className="size-5" />
                      </span>
                    </div>
                  </a>
                </BlurFade>
              )
            })}
          </div>
          <p className="mt-8 text-sm text-texto-3">
            ¿Quieres verlo por WhatsApp? Escríbenos y te mostramos el agente en la misma
            conversación.
          </p>
        </div>
      </main>
      <CierreYPie />
    </>
  )
}
