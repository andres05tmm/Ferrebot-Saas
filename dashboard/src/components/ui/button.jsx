import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors duration-fast ease-out-quad focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        default:     'bg-primary text-primary-foreground hover:bg-primary-hover',
        destructive: 'bg-destructive text-destructive-foreground hover:opacity-90',
        outline:     'border border-input bg-surface hover:bg-surface-2 text-foreground',
        secondary:   'bg-secondary text-secondary-foreground hover:bg-surface-2',
        ghost:       'hover:bg-surface-2 text-secondary-foreground',
        link:        'text-primary underline-offset-4 hover:underline',
      },
      // Alturas +4px en móvil (≤sm) para un target táctil más cómodo; en sm+ vuelven al tamaño denso
      // de escritorio. El bump es uniforme (default/icon) para no desalinear toolbars mixtas select+botón.
      size: {
        default: 'h-10 px-4 py-2 sm:h-9',
        sm:      'h-9 rounded-md px-3 text-xs sm:h-8',
        lg:      'h-11 rounded-md px-6 sm:h-10',
        icon:    'h-10 w-10 sm:h-9 sm:w-9',
        // Calca BTN_PRIMARY/BTN_OUTLINE de construcción (F2.0): target táctil ≥40px en móvil vía
        // min-height, y en sm+ se libera para que la altura densa la ponga el uso (h-7/h-8/h-9 por
        // className). Es la vía de migración para extinguir esas constantes.
        touch:   'min-h-10 px-3 sm:min-h-0',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

const Button = React.forwardRef(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size, className }))} {...props} />
  },
)
Button.displayName = 'Button'

export { Button, buttonVariants }
