# ADR 0021 — Partición del pack `pos` en features finas (`ventas` / `caja` / `inventario`)

- **Estado:** Aceptado
- **Fecha:** 2026-07-01
- **Relacionados:** ADR 0008 (retail → pack `pos`), ADR 0018 (dos familias de dashboard), ADR 0007 (manifiesto/provisionador)

## Contexto

El ADR 0008 sacó el retail del núcleo y lo agrupó en un solo pack grueso `pos` (ventas, inventario,
caja, gastos, compras, proveedores), anticipando que "se podrá partir luego sin reescribir". Ese
momento llegó: para ofrecer el software contable a verticales de servicios (peluquería, spa, hotel),
un negocio debe poder activar **solo la contabilidad que necesita** — caja y gastos, ventas de
mostrador con catálogo, facturación — sin cargar con kárdex, compras y proveedores de ferretería.
Con el pack grueso la opción era todo o nada.

## Decisión

### D1 — Tres features finas

| Feature | Cubre | Racional |
|---|---|---|
| `ventas` | registrar/consultar ventas + **catálogo de productos** + top-productos | El catálogo va con ventas, no con inventario: una peluquería vende shampoo sin llevar stock; `pack_pedidos`/`pack_ventas` solo necesitan catálogo. |
| `caja` | caja (apertura/cierre/arqueo) + gastos | El arqueo híbrido lee `ventas_efectivo` de la tabla `ventas` y degrada a 0 si no hay ventas → `caja` **no** depende de `ventas`. |
| `inventario` | stock, kárdex, ajustes/conteo + compras + proveedores | Compras y proveedores mutan stock (movimientos ENTRADA) → van juntos. Depende de `ventas` (el stock es DE productos del catálogo). |

### D2 — `pos` sobrevive como meta-pack que expande

`META_PACKS = {"pos": {"ventas", "caja", "inventario"}}` en `core/tenancy/catalogo.py`. La expansión
(`expandir_metapacks`) es pura, idempotente y **conserva el flag meta** en el set: el gating de
familia del dashboard (ADR 0018) y cualquier check legado que lea `pos` siguen funcionando.

**No hay migración de flags.** Los tenants existentes (Punto Rojo por `0004_grandfather_pos`, los
demos por sus planes) conservan `pos` y ven exactamente lo mismo que antes.

### D3 — Dos cuellos de expansión, ninguno más

1. **Runtime:** `ControlCapacidades.efectivas()` (`core/tenancy/capacidades.py`) devuelve el set
   expandido → API (`core/auth/features.py`), bot, worker, WA y superadmin ven las finas.
2. **Caminos puros/síncronos:** `capacidades_completas()` (`core/tenancy/catalogo.py`) expande →
   provisionador, `set_feature`, `switch_demo`, `seed_demo_transaccional` y la validación de
   manifiestos quedan cubiertos sin tocarlos.

Además `validar_dependencias()` expande internamente (fail-safe si un llamador pasa el set crudo).

### D4 — Dependencias apuntan a las finas

`fiados→{ventas}`, `mayorista→{ventas}`, `pack_pedidos→{ventas}`, `pack_ventas→{ventas}`,
`inventario→{ventas}`, `pack_pagar→{inventario}`. Como la validación corre sobre el set expandido,
un tenant con solo `pos` las satisface todas.

### D5 — Semántica del meta-pack (sin restas)

El meta-pack **siempre implica sus finas**: un override que apague una fina bajo `pos` activo no
surte efecto (la expansión la re-añade). Para activar un subconjunto, el plan usa las finas
directamente en lugar de `pos`. Esto mantiene la expansión idempotente y sin estados ambiguos.

### D6 — Regla de supresión del dashboard (refina ADR 0018)

Una ruta contable es visible si su feature fina está activa **y no** se trata de un tenant de
atención-a-cliente cuyo retail llegó por arrastre del meta-pack:
`finaActiva && !(esAtencionCliente && features.includes('pos'))`. Resultado:

- Punto Rojo / ferreteria-demo (`pos`, sin packs de servicio): sin cambio, ven todo el retail.
- Demos de servicios (`pos` por arrastre + pack de servicio): sin cambio, retail oculto.
- Peluquería nueva (`pack_agenda + caja + ventas`, **sin** `pos`): ve Caja/Gastos/Ventas junto a
  Agenda — el carril contable de servicios que motiva este ADR.

## Consecuencias

- Alta de un vertical de servicios con contabilidad = manifiesto con features finas; cero código.
- El cockpit retail (`/hoy`) sigue siendo del meta-pack `pos` (experiencia integrada de ferretería).
- Las tools del bot y los routers del API se remapean de `pos` a su feature fina (fases 2 y 4 del
  carril); el loader `pos` del provisionador pasa a colgar de `ventas` con sección YAML `packs.pos`
  (compat con manifiestos existentes).
- Deuda saldada: el "se podrá partir luego" del ADR 0008 §D1 queda ejecutado.
