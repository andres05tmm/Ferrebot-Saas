import { Link } from 'react-router-dom'
import { BlurFade } from '@/components/ui/blur-fade.jsx'

const WA_URL =
  'https://wa.me/573206213221?text=Hola%2C%20tengo%20un%20negocio%20y%20quiero%20ver%20a%20Melquiadez%20trabajando'

/* CTA final "drenched" en el acento del vertical activo + footer mínimo. */
export default function CierreYPie() {
  return (
    <>
      <section className="bg-acento px-5 py-24 text-acento-sobre transition-colors duration-500 sm:px-10 md:py-32">
        <div className="mx-auto flex max-w-4xl flex-col items-center gap-7 text-center">
          <BlurFade inView>
            <h2 className="max-w-[16ch] font-display text-5xl font-semibold leading-none tracking-tight md:text-7xl">
              Ponlo a trabajar esta semana.
            </h2>
          </BlurFade>
          <BlurFade inView delay={0.1}>
            <p className="max-w-[44ch] text-lg opacity-85">
              Míralo atender un negocio como el tuyo, o escríbenos y te lo montamos con tus
              precios reales.
            </p>
          </BlurFade>
          <BlurFade inView delay={0.2} className="flex flex-wrap items-center justify-center gap-4">
            <Link
              to="/demo"
              className="rounded-2xl bg-fondo px-9 py-4 text-lg font-bold text-texto transition-transform duration-300 ease-marca hover:-translate-y-0.5 hover:scale-[1.02]"
            >
              Ver una demo
            </Link>
            <a
              href={WA_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-2xl border border-current/40 px-9 py-4 text-lg font-semibold transition-colors hover:bg-white/10"
            >
              Escríbenos
            </a>
          </BlurFade>
        </div>
      </section>
      <footer className="flex flex-wrap items-center justify-between gap-4 border-t border-linea px-5 py-7 text-[13px] text-texto-3 sm:px-10">
        <span>© 2026 Melquiadez · Cartagena, Colombia</span>
        <span className="flex gap-5">
          <Link to="/demo" className="hover:text-texto">Demos</Link>
          <Link to="/login" className="hover:text-texto">Entrar</Link>
          <a href={WA_URL} target="_blank" rel="noopener noreferrer" className="hover:text-texto">WhatsApp</a>
        </span>
      </footer>
    </>
  )
}
