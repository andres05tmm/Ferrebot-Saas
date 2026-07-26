import { clsx } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

/*
 * tailwind-merge no conoce la escala semántica del proyecto (`text-caption`, `text-body-sm`…) y la
 * clasificaba como COLOR de texto: `cn('text-destructive', 'text-caption')` se quedaba solo con el
 * segundo y el badge perdía el rojo. Aquí se declaran como lo que son, tamaño de fuente.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['micro', 'caption', 'meta', 'body-sm'] }],
    },
  },
})

export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
