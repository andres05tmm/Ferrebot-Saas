# Fase 8 — Cierre de esquema del tenant · plan + prompts

> Re-scopeada contra el código real. **Sorpresa:** `schema.md` ya tomó casi todas las decisiones del §8 de
> `migracion-puntorojo.md`, y varias tablas ya están implementadas. Fase 8 es sobre todo **implementación**
> (igualar `schema.md`), no deliberación. Cowork redacta; Andrés ejecuta en Claude Code.

## Estado real (implementado vs. faltante)

**Ya implementado** (modelos + migración tenant 0001-0004): `productos` (con pricing escalonado **y**
mayorista), `productos_fracciones`, `inventario`, `movimientos_inventario`, `ventas`, `ventas_detalle`,
`clientes`, `caja`, `caja_movimientos`, `gastos` (con `caja_id`), `fiados`, `fiados_movimientos`,
`facturas_electronicas`, `usuarios`, `conversaciones_bot`, `memoria_entidades`, `api_costo_diario`,
`audio_logs`. `config_empresa` existe en el **control DB**.

**Faltante** (en `schema.md` pero sin tabla): `aliases`, `proveedores`, `compras` + `compras_detalle`,
`facturas_proveedores`, `facturas_abonos`, `bancolombia_transferencias`, `historico_ventas`, `compras_fiscal`,
`documentos_soporte`, `notas_electronicas`, `eventos_dian`, `cuentas_cobro`, `iva_saldos_bimestrales`,
`ventas_pendientes_voz`.

## Decisiones (cerradas con Andrés)

- **D1 — Alcance:** construir **TODO** el esquema restante en Fase 8 (incl. las tablas fiscales **vacías**).
  La lógica fiscal sigue en Fase 12; aquí solo nacen las tablas. Razón: `feature-flags.md` ("el esquema NO
  cambia entre empresas; una tabla vacía no cuesta nada") + el ETL de Punto Rojo necesita destinos fiscales.
- **D2 — `config_empresa`:** vive en el **control DB** (ya implementado); se borró el duplicado app-DB de
  `schema.md`. Se carga una vez en el contexto cacheado del tenant (como las capacidades) para no pegarle al
  control DB en hot paths (bypass).

## Decisiones DIFERIDAS a momento-ETL (Fase 15, no ahora)

No bloquean el esquema; se deciden al cargar datos reales de Punto Rojo:

- **G4 — zona horaria** de las marcas naive de FerreBot (¿el server escribía en UTC o en hora Colombia?).
  La de mayor riesgo; confirmar antes de cargar.
- **`gasto → caja`:** FerreBot no tiene `caja_id` en gastos; cómo reconstruir el vínculo.
- **`proveedores` desde texto libre:** derivar la tabla del texto de compras/facturas, o mantener texto.
- **`subtotal`/`impuestos`** de ventas históricas: FerreBot no los separa; derivar del detalle o `impuestos=0`.

## Nota de modelado menor (sigo `schema.md`, sin elevar a decisión)

`compras.proveedor_id` es FK a `proveedores`, pero `facturas_proveedores.proveedor` es **TEXT** libre (espejo
de FerreBot, 1 fila). Lo dejo así por ahora (normalizar facturas_proveedores a FK es trivial y se puede hacer
después). Si prefieres FK desde ya, dilo.

## Desglose (cada E = migración + modelos + tests; cadena Alembic 0005→00xx)

| E | Dominio | Tablas |
|---|---|---|
| E1 | Búsqueda | `aliases` (+ índice de apoyo si aplica) |
| E2 | Compras | `proveedores`, `compras`, `compras_detalle` |
| E3 | Cuentas por pagar + banca | `facturas_proveedores`, `facturas_abonos`, `bancolombia_transferencias` |
| E4 | Reportes + voz | `historico_ventas`, `ventas_pendientes_voz` |
| E5 | Fiscal (schema-only) | `compras_fiscal`, `documentos_soporte`, `notas_electronicas`, `eventos_dian`, `cuentas_cobro`, `iva_saldos_bimestrales` + SEQUENCEs de consecutivo (factura por tipo, DS) + enums nuevos |
| E6 | Verificación | test de paridad (todas las tablas de `schema.md` existen en un tenant fresco), `upgrade`/`downgrade` limpio, test de aislamiento sigue verde |

**Criterio de cierre de fase:** un tenant nuevo migrado a head tiene **todas** las tablas de `schema.md`;
`upgrade`/`downgrade` corren limpio; suite verde. Modelos importables con un insert/select básico por tabla
clave. (Repos/servicios de cada dominio llegan en su fase de feature; aquí solo esquema + modelos.)

---

## E1 — `aliases` (prompt RED para Claude Code)

```
Contexto: FerreBot SaaS, esquema tenant. Falta la tabla `aliases` (variantes/typos → producto; alimenta
búsqueda y bypass). Especificación en docs/schema.md (sección "aliases"):
  id BIGSERIAL PK; termino TEXT NOT NULL; reemplazo TEXT NOT NULL; producto_id BIGINT FK productos(id) NULL
  (alias global si NULL); creado_en/actualizado_en TIMESTAMPTZ; UNIQUE(termino).

TDD, siguiendo el patrón de las migraciones tenant existentes (migrations/tenant/0001-0004) y de los modelos
(modules/inventario/models.py usa TenantBase):
1) RED — test de migración en tests/ (patrón de test_migrations.py / test_migracion_tenant_0004.py):
   - crea un tenant efímero con el fixture `tenant` (conftest), upgrade a head, y verifica que la tabla
     `aliases` existe con sus columnas, el UNIQUE(termino) y la FK a productos.
   - test de modelo: insertar un alias global (producto_id NULL) y uno ligado a un producto sembrado
     (seed_producto), y leerlos.
2) GREEN —
   - nueva migración migrations/tenant/0005_aliases.py (down_revision = la última de tenant; revisa cuál es
     el head actual con la cadena 0001→0004). upgrade crea la tabla; downgrade la dropea.
   - modelo Alias en modules/inventario/models.py (o un modules/busqueda si encaja mejor), con TenantBase,
     type hints 3.10+, Mapped/mapped_column como el resto.

Reglas del repo: NUMERIC/TIMESTAMPTZ según convenciones de schema.md, sin empresa_id (la base es la frontera),
docstrings en español, sin print. Acceso a datos solo por repos cuando toque (aquí basta el modelo).
Corre: .venv/Scripts/python.exe -m pytest tests/ -k "alias or migrac" -q
```

**Qué reviso yo:** que la migración encadene bien del head actual de tenant, UNIQUE(termino) y FK presentes,
modelo con `TenantBase` y sin `empresa_id`, y que el ETL futuro de FerreBot (`aliases` + `productos.aliases[]`
aplanado) tenga destino limpio.

> Los prompts de E2-E6 los entrego uno a uno al avanzar (mismo patrón: spec de schema.md → migración → modelos
> → tests), para revisar cada eslabón antes del siguiente.
