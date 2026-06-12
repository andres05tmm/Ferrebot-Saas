import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import Nav from '@/components/Nav.jsx'
import Telefono from '@/components/Telefono.jsx'
import AcordeonVerticales from '@/components/AcordeonVerticales.jsx'
import ComoFunciona from '@/components/ComoFunciona.jsx'
import SeccionDashboard from '@/components/SeccionDashboard.jsx'
import CierreYPie from '@/components/CierreYPie.jsx'
import { TextRotate } from '@/components/ui/text-rotate.jsx'
import { BlurFade } from '@/components/ui/blur-fade.jsx'
import { useTema } from '@/hooks/useTema.js'
import {
  ORDEN,
  PALABRAS,
  ROTACION_MS,
  aplicarVertical,
  siguienteIndice,
} from '@/lib/verticales.js'

// El shader va en chunk aparte: el hero pinta primero con el fondo plano del tema.
const AuroraOro = lazy(() => import('@/components/AuroraOro.jsx'))

export default function Landing() {
  const [indice, setIndice] = useState(0)
  const [auto, setAuto] = useState(true)
  const rotador = useRef(null)
  const tema = useTema()
  const vertical = ORDEN[indice]

  // El vertical activo retematiza la página entera (acento via CSS).
  useEffect(() => {
    aplicarVertical(vertical)
    rotador.current?.jumpTo(indice)
  }, [vertical, indice])

  // Rotación automática hasta que el usuario elija un vertical a mano.
  useEffect(() => {
    if (!auto || matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined
    const id = setInterval(() => setIndice(siguienteIndice), ROTACION_MS)
    return () => clearInterval(id)
  }, [auto])

  const elegir = useCallback((clave) => {
    setIndice(ORDEN.indexOf(clave))
    setAuto(false)
  }, [])

  return (
    <>
      <Nav />
      {/* ───── hero: titular con palabra rotante + teléfono + UN shader sutil ───── */}
      <header className="relative overflow-hidden px-5 pb-10 pt-32 sm:px-10 md:pt-40">
        <Suspense fallback={null}>
          <AuroraOro
            tema={tema}
            intensidad={tema === 'oscuro' ? 0.5 : 0.35}
            className="[mask-image:linear-gradient(to_bottom,black,black_55%,transparent_95%)]"
          />
        </Suspense>
        <div className="relative z-10 mx-auto grid max-w-6xl items-center gap-12 md:grid-cols-[1.05fr_.95fr] md:gap-16">
          <div>
            <span className="mb-5 inline-flex items-center gap-2.5 text-sm font-semibold text-acento transition-colors duration-500">
              <i className="size-2 animate-pulse rounded-full bg-acento shadow-[0_0_12px_2px_var(--acento)]" />
              Atendiendo negocios ahora mismo
            </span>
            <h1 className="font-display text-5xl font-semibold leading-[1.02] tracking-tight sm:text-6xl md:text-7xl">
              Un empleado que no duerme, para tu{' '}
              <TextRotate
                ref={rotador}
                texts={PALABRAS}
                auto={false}
                splitBy="characters"
                staggerDuration={0.02}
                mainClassName="inline-flex overflow-hidden align-bottom text-acento border-b-4 transition-colors duration-500"
                style={{ borderColor: 'color-mix(in oklch, var(--acento) 38%, transparent)' }}
                transition={{ type: 'spring', damping: 28, stiffness: 350 }}
              />
            </h1>
            <p className="mt-6 max-w-[44ch] text-lg text-texto-2">
              Vive en tu WhatsApp. Agenda citas, toma pedidos, reserva habitaciones y cobra
              — con tus precios reales, a cualquier hora.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-5">
              <Link
                to="/demo"
                className="rounded-2xl bg-acento px-8 py-4 text-lg font-semibold text-acento-sobre shadow-marca transition-all duration-300 ease-marca hover:-translate-y-0.5"
              >
                Ver una demo
              </Link>
              <a
                href="#oficios"
                className="border-b border-linea pb-0.5 text-[15px] text-texto-2 transition-colors hover:text-texto"
              >
                Qué sabe hacer ↓
              </a>
            </div>
          </div>
          <div className="flex min-h-[540px] items-center justify-center">
            <Telefono vertical={vertical} />
          </div>
        </div>
      </header>

      {/* ───── verticales: el acordeón retematiza la página y linkea a las demos ───── */}
      <section id="oficios" className="px-5 py-20 sm:px-10 md:py-28">
        <div className="mx-auto max-w-6xl">
          <BlurFade inView>
            <h2 className="max-w-[20ch] font-display text-4xl font-semibold leading-[1.05] tracking-tight md:text-5xl">
              El mismo empleado.
              <br />
              <em className="not-italic text-acento transition-colors duration-500">Tu oficio.</em>
            </h2>
          </BlurFade>
          <BlurFade inView delay={0.1}>
            <p className="mt-4 max-w-[52ch] text-[17px] text-texto-2">
              Toca un negocio: cambia el acento, cambia la conversación. Cada demo es un
              negocio de verdad funcionando — entra y úsala.
            </p>
          </BlurFade>
          <BlurFade inView delay={0.18} className="mt-10">
            <AcordeonVerticales vertical={vertical} onElegir={elegir} />
          </BlurFade>
        </div>
      </section>

      <ComoFunciona />
      <SeccionDashboard />
      <CierreYPie />
    </>
  )
}
