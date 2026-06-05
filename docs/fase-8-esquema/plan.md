# Fase 8 — Cierre de esquema del tenant · plan + prompts (REVISADO)

> **Re-scope mayor:** la migración `migrations/tenant/versions/0001_tenant_init.py` **ya crea las 35 tablas**
> de `schema.md`. El esquema físico del tenant **ya está cerrado**. Lo único partial son los **modelos ORM**
> (varias tablas existen en la base pero no tienen clase SQLAlchemy mapeada — consistente con la nota en
> `modules/inventario/models.py`: "solo las columnas que toca la Fase 1; el resto del esquema existe vía
> migración"). Por tanto Fase 8 NO es construir esquema, sino **paridad + cleanup + modelos a demanda**.

## Qué ya existe

- **Esquema físico completo** vía `0001` (35 `create_table`): incluye `aliases`, `proveedores`, `compras`,
  `compras_detalle`, `compras_fiscal`, `facturas_proveedores`, `facturas_abonos`, `bancolombia_transferencias`,
  `historico_ventas`, `documentos_soporte`, `notas_electronicas`, `eventos_dian`, `cuentas_cobro`,
  `iva_saldos_bimestrales`, `ventas_pendientes_voz`, `libro_iva`, etc. Cadena: `0001 → 0002 → 0003 → 0004`
  (head = `0004_memoria_entidades_uq`).
- **Modelos ORM** solo para lo que tocaron Fases 1-6 (catálogo/inventario/ventas/caja/fiados/clientes/
  facturación/memoria/usuarios). El resto de tablas existe en la base sin clase mapeada.

## Decisiones (cerradas con Andrés)

- **D1 — Alcance esquema:** moot. El esquema ya está completo en `0001`.
- **D2 — `config_empresa`:** vive en el **control DB**. (Nota: `0001` del tenant **también** crea una
  `config_empresa` → vestigial; se reconcilia en E3.)
- **D3 — Forma de Fase 8 (slim):** (1) test de paridad de esquema, (2) reconciliar/dropear el
  `config_empresa` del tenant, (3) modelo `Alias` ahora. **Los demás modelos ORM se agregan en su fase
  consumidora** (fiscal → Fase 12; `compras`/`proveedores`/`historico_ventas`/`bancolombia` → ETL Fase 15 /
  dashboard Fase 11). Sin trabajo muerto.

## Decisiones DIFERIDAS a momento-ETL (Fase 15)

G4 zona horaria de marcas naive · reconstrucción `gasto→caja` · derivación de `proveedores` desde texto ·
`subtotal`/`impuestos` de ventas históricas. No bloquean el esquema.

## Desglose (slim)

| E | Entregable | Qué |
|---|---|---|
| E1 | Modelo `Alias` | Clase SQLAlchemy `Alias` (tabla ya existe) + tests (existe en head con UNIQUE+FK; insert global y ligado). **SIN migración.** |
| E2 | Test de paridad de esquema | Guardarraíl pre-ETL: un tenant migrado a head tiene **todas** las tablas de `schema.md` (35). Introspección con el inspector de SQLAlchemy / `information_schema`. Falla si hay drift. |
| E3 | Reconciliar `config_empresa` | Confirmar que nada lee el `config_empresa` del **tenant** (el canónico es el del control DB). Si nadie lo lee → migración `0005` que lo **dropea** del tenant. Si algo lo lee → redirigir al control DB primero. |

**Criterio de cierre:** modelo `Alias` mapeado y probado; test de paridad verde (esquema = `schema.md`);
`config_empresa` con una sola fuente de verdad (control DB); suite verde; `upgrade`/`downgrade` limpio.

---

## E1 — Modelo `Alias` (en curso)

Claude Code lo está haciendo vía la opción **"Solo modelo + tests"** (sin migración, porque la tabla ya
existe desde `0001`). Spec en `schema.md` (sección "aliases"): `id, termino UNIQUE, reemplazo,
producto_id FK NULL, creado_en/actualizado_en`. **Qué reviso:** clase con `TenantBase`, sin `empresa_id`,
y tests que verifiquen tabla en head (UNIQUE+FK) + insert global y ligado a producto.

## E2 — Test de paridad de esquema (prompt RED para Claude Code)

```
Contexto: FerreBot SaaS. El esquema tenant lo crea migrations/tenant/0001. Quiero un guardarraíl de paridad
que falle si el esquema migrado se desvía de la lista de tablas esperada (pre-ETL de Punto Rojo).

Crea tests/test_schema_paridad.py:
- Usa el fixture `tenant` (conftest) → base efímera ya migrada a head.
- Con el inspector de SQLAlchemy (sqlalchemy.inspect / Inspector.get_table_names) sobre tenant.engine,
  obtén el set de tablas reales (excluye alembic_version).
- Afírmalo igual al set ESPERADO de tablas de negocio de docs/schema.md (App DB). Lista explícita en el test
  (las 35 de 0001 menos lo que decidamos), para que agregar/quitar una tabla sin actualizar el test falle.
- (Opcional, si es barato) afirma columnas clave de 2-3 tablas críticas para el ETL (facturas_electronicas,
  ventas, productos) con get_columns.

Reglas: async donde aplique, sin print, type hints 3.10+. NO toques migraciones ni modelos.
Corre: .venv/Scripts/python.exe -m pytest tests/test_schema_paridad.py -q
```

**Qué reviso:** que la lista esperada sea explícita (no derivada del propio metadata, o no detectaría drift),
y que excluya `alembic_version`.

## Orden: E3 antes que E2

E3 dropea `config_empresa` del tenant → cambia el esquema. Hacer E3 primero deja a E2 (paridad) fijando el
set final de 34 tablas de una sola vez (35 de `0001` − `config_empresa`).

## E3 — Reconciliar `config_empresa` (investigación HECHA → prompt RED)

**Confirmado:** la `config_empresa` del **tenant** (creada en `0001`: `clave TEXT PK, valor JSONB`) está
muerta para **lecturas**. Los lectores (`modules/facturacion/config.py`, `core/llm/stores.py`, `ai/ports.py`)
consultan `WHERE empresa_id = :e` — columna que solo existe en el `config_empresa` del **control DB**
(migración control `0002`).

> **Hallazgo (Claude Code):** hay un **escritor** — `tools/provision_tenant.py:_seed()` inserta
> `iva_incluido_en_precio='true'` en la `config_empresa` del tenant. Dropear la tabla sin tocar eso rompe el
> próximo provisioning (`UndefinedTable`), y **no hay test de provisioning** que lo atrape. Por eso E3 también
> quita ese INSERT muerto (`iva_incluido_en_precio` no lo lee nadie).
>
> **Intención preservada:** "IVA incluido en precio" es un setting real por empresa a futuro; cuando se
> cablee (con su lector), va al `config_empresa` del **control DB**, no al tenant.
>
> **Follow-up Fase 13:** `provision_tenant` tiene cero cobertura de tests → agregar smoke de provisioning
> como guardarraíl (mismo patrón que la deuda de smokes HTTP de Fase 7).

```
Contexto: FerreBot SaaS, esquema tenant. La migración 0001 creó en la app DB una tabla config_empresa
(clave TEXT PK, valor JSONB). La config no-secreta por empresa vive en el CONTROL DB (migración control
0002_config_empresa, con empresa_id). Confirmado que NADIE lee la config_empresa del tenant: los dos
lectores (modules/facturacion/config.py, core/llm/stores.py) consultan "WHERE empresa_id = :e", que solo
existe en la del control DB. La del tenant es vestigial.

TDD:
1) RED — test en tests/ (patrón test_migrations.py): sobre el fixture `tenant` (ya en head), afirma que la
   tabla config_empresa NO existe en la app DB (inspector get_table_names).
2) GREEN — migración migrations/tenant/0005_drop_config_empresa.py:
   - down_revision = head actual del árbol tenant (0004_memoria_uq; confírmalo).
   - upgrade(): op.drop_table("config_empresa").
   - downgrade(): recrea config_empresa (clave TEXT PK, valor JSONB) como en 0001.

Reglas: docstrings en español, sin print. No toques los lectores (ya usan el control DB).
Corre: .venv/Scripts/python.exe -m pytest tests/ -k "migrac or config or schema" -q
```

**Qué reviso:** down_revision correcto (encadena del head real), downgrade que recrea fiel, y que ningún
lector quede apuntando al tenant.
