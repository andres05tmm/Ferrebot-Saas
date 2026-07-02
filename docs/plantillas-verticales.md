# Plantillas de manifiesto por vertical

> Dar de alta un negocio nuevo = **copiar la plantilla de su vertical, llenar sus datos y provisionar**.
> Las plantillas viven en `tools/onboarding/*.manifest.example.yaml` y son a la vez los tenants demo:
> se provisionan por el mismo camino que un cliente pagado (ADR 0007).

## Flujo de alta (minutos, no semanas)

1. **Copiar la plantilla** del vertical más parecido (tabla abajo) y ajustar `identidad` (slug, nombre,
   NIT, `rubro`), `plan.nombre` (los planes se comparten por NOMBRE: cada cliente con set propio
   necesita su propio nombre de plan), branding y canal.
2. **Extraer el catálogo real** con el skill `onboarding-magico` (fotos de listas de precios, Excel,
   Instagram → secciones `packs.pos` / `packs.agenda` del YAML, contrato anti-alucinación ADR 0011).
3. **Provisionar:** `python -m tools.provision_from_manifest --from <archivo>.yaml` (idempotente:
   crea base → migra → siembra packs → mapea WhatsApp → smoke).
4. Ajustes posteriores sin re-provisionar: `tools/set_feature` (capacidades), `tools/set_config`
   (p. ej. `rubro`), panel super-admin.

## Plantillas

| Vertical | Plantilla (slug) | Features del plan | Preset | Rubro | Home |
|---|---|---|---|---|---|
| Ferretería / retail duro | `ferreteria-demo` | `pos` (meta-pack completo) | rojo PR | ferretería | `/hoy` |
| Peluquería / salón / spa | `peluqueria-demo` | `pack_agenda, pack_faq, canal_whatsapp, caja, ventas` | `lienzo` | peluquería | `/inicio` |
| Barbería | `barberia-demo` | `pack_agenda, pack_faq, canal_whatsapp, caja, ventas` | `navaja` | barbería | `/inicio` |
| Clínica / consultorio | `clinica-demo` | `pack_agenda, pack_faq, canal_whatsapp` | `aurora` | clínica | `/inicio` |
| Restaurante | `restaurante-demo` | `pos, pack_pedidos, pack_faq, canal_whatsapp` | `brasa` | restaurante | `/pedidos` |
| Hotel / hospedaje | `hotel-demo` | `pack_agenda, pack_reservas, pack_faq, canal_whatsapp` | `brisa` | hotel | `/inicio` |

## Cómo se decide el "software contable" de cada vertical (ADR 0021/0022)

- **Retail afín a ferretería** → meta-pack `pos`: todo el POS (ventas+catálogo, caja+gastos,
  inventario+compras+proveedores) y el cockpit `/hoy`.
- **Servicios con cobro en el local** (peluquería, barbería, spa) → features finas `caja` + `ventas`
  (sin `inventario`): ven Caja/Gastos/Ventas junto a su Agenda, **cobran la cita con un clic**
  (venta con línea varia + arqueo cuadrado, ADR 0022) y venden productos de mostrador sin kárdex.
- **Servicios sin mostrador** (clínica) → solo sus packs; la contabilidad se activa después con
  `tools/set_feature <slug> caja` / `ventas` cuando el negocio la pida.
- **Facturación DIAN** es transversal y opt-in en cualquier vertical: `facturacion_electronica`
  (+ `pos_electronico` si cierra ventas de mostrador) + credenciales MATIAS en `secretos`.

## Reglas al crear una plantilla nueva

- `identidad.rubro` SIEMPRE: parametriza la persona del bot de operación (sin él, cae al prompt
  ferretero histórico).
- NIT único (el control DB lo exige UNIQUE) — revisar que no choque con otra plantilla.
- Identidad demo no-admin (`demo+<slug>@melquiadez.com`, rol `vendedor`) para prospectos.
- Features finas en vez de `pos` salvo que el negocio sea retail completo (el meta-pack no se puede
  restar por override: para subconjuntos van las finas, ADR 0021 §D5).
- Validar con `pytest tests/test_manifests_demo.py` (el paramétrico valida todas las plantillas).
