import * as React from 'react'
import { cn } from '@/lib/utils'

const Input = React.forwardRef(({ className, type, ...props }, ref) => {
  return (
    <input
      type={type}
      ref={ref}
      className={cn(
        'flex h-9 w-full rounded-md border border-input bg-surface px-3 py-1 text-sm text-foreground placeholder:text-muted-foreground',
        // Foco según DESIGN.md §4: borde primario + anillo exterior del primario al 10% (sin offset duro).
        'focus-visible:outline-none focus-visible:border-primary focus-visible:ring-4 focus-visible:ring-primary/10',
        'disabled:cursor-not-allowed disabled:opacity-50 transition-colors duration-fast',
        className,
      )}
      {...props}
    />
  )
})
Input.displayName = 'Input'

export { Input }
