import { BlurFade } from '@/components/ui/blur-fade.jsx'

/* Tres pasos, un verbo por paso. Nada de párrafos. */
const PASOS = [
  {
    verbo: 'Escanea',
    detalle: 'Mándanos una foto de tu lista de precios o tu carta. Así esté a mano.',
    cuando: 'Hoy, 9:00 am',
  },
  {
    verbo: 'Atiende',
    detalle: 'Melquiadez responde tu WhatsApp con tus datos reales, a cualquier hora.',
    cuando: 'Hoy, en la tarde',
  },
  {
    verbo: 'Míralo',
    detalle: 'Cada conversación, cita y pedido aparece en tu dashboard al instante.',
    cuando: 'Esta noche',
  },
]

export default function ComoFunciona() {
  return (
    <section id="como-funciona" className="px-5 py-20 sm:px-10 md:py-32">
      <div className="mx-auto max-w-6xl">
        <BlurFade inView>
          <h2 className="max-w-[20ch] font-display text-4xl font-semibold leading-[1.05] tracking-tight md:text-5xl">
            Montado en <em className="not-italic text-acento transition-colors duration-500">un día</em>,
            <br />
            no en un proyecto.
          </h2>
        </BlurFade>
        <div className="mt-14 grid gap-8 md:grid-cols-3 md:gap-10">
          {PASOS.map((paso, i) => (
            <BlurFade key={paso.verbo} inView delay={0.15 * i} className={i === 1 ? 'md:mt-10' : i === 2 ? 'md:mt-20' : ''}>
              <article className="border-t-[3px] border-linea pt-6 transition-colors duration-500 hover:border-acento">
                <span className="text-sm font-semibold text-acento">{paso.cuando}</span>
                <h3 className="mt-2 font-display text-3xl font-semibold tracking-tight">{paso.verbo}.</h3>
                <p className="mt-2.5 max-w-[34ch] text-[15px] text-texto-2">{paso.detalle}</p>
              </article>
            </BlurFade>
          ))}
        </div>
      </div>
    </section>
  )
}
