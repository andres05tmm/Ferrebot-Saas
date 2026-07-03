/*
 * schemas.ts — validación de formularios con zod (ADR 0029).
 *
 * Patrón para formularios NUEVOS: definir un schema zod aquí, tipar el form con `z.infer`, y pasar
 * `zodResolver(schema)` a `useForm` de react-hook-form. Los formularios existentes NO se migran.
 */
import { z } from 'zod'
import type { FieldErrors, FieldValues, Resolver } from 'react-hook-form'

// Adapta un schema zod al contrato de `resolver` de react-hook-form, sin depender de
// @hookform/resolvers (una dependencia menos). Válido para formularios planos (sin anidamiento
// profundo), que es lo que usamos. En caso de éxito devuelve los datos ya coaccionados por zod.
export function zodResolver<T extends FieldValues>(schema: z.ZodType<T>): Resolver<T> {
  return async (values) => {
    const result = schema.safeParse(values)
    if (result.success) {
      return { values: result.data, errors: {} }
    }
    const errors: FieldErrors<T> = {}
    for (const issue of result.error.issues) {
      const key = issue.path.map(String).join('.') || 'root'
      // Primer error por campo gana (como hace RHF por defecto).
      if (!(key in errors)) {
        ;(errors as Record<string, unknown>)[key] = { type: String(issue.code), message: issue.message }
      }
    }
    return { values: {}, errors }
  }
}

// Ejemplo/plantilla del patrón (ADR 0029): schema de una "venta varia" (descripción libre, sin
// catálogo). Modela el mini-form de TabVentasRapidas; queda listo para cablearse a react-hook-form
// cuando ese form se rehaga. `coerce` convierte los strings de los <input> a número antes de validar.
export const ventaVariaSchema = z.object({
  descripcion: z.string().trim().min(1, 'La descripción es obligatoria'),
  cantidad: z.coerce.number().positive('La cantidad debe ser mayor a 0'),
  precio_unitario: z.coerce.number().positive('El precio debe ser mayor a 0'),
})

export type VentaVaria = z.infer<typeof ventaVariaSchema>
