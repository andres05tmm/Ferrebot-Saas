import { Toaster as Sonner } from 'sonner'

// Toaster global. Lee tema via data-theme del <html> en runtime.
const Toaster = ({ ...props }) => {
  const theme = (typeof document !== 'undefined' && document.documentElement.getAttribute('data-theme')) || 'system'
  return (
    <Sonner
      theme={theme}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast: 'group toast group-[.toaster]:bg-surface group-[.toaster]:text-foreground group-[.toaster]:border group-[.toaster]:border-border group-[.toaster]:shadow-md',
          description: 'group-[.toast]:text-muted-foreground',
          actionButton: 'group-[.toast]:bg-primary group-[.toast]:text-primary-foreground',
          cancelButton: 'group-[.toast]:bg-surface-2 group-[.toast]:text-muted-foreground',
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
