import selloRaw from '../../marca/sello.svg?raw'
import { cn } from '@/lib/utils'

// El sello M inline: la tinta usa currentColor, así sigue al tema de la página
// (el <style> interno del archivo es para uso standalone — favicon — y aquí se quita).
const markup = selloRaw
  .replace(/<style>[\s\S]*?<\/style>/, '')
  .replace('<svg ', '<svg width="100%" height="100%" role="img" aria-label="Melquiadez" ')

export default function Sello({ className }) {
  return (
    <span
      className={cn('inline-block text-texto', className)}
      dangerouslySetInnerHTML={{ __html: markup }}
    />
  )
}
