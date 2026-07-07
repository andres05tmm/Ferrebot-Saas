# Prospecto PIM — Documentación organizada

Todo lo del posible cliente **Construcciones PIM S.A.S.** (ERP de obra / asfalto), que se construirá como **tenant nuevo** sobre nuestra plataforma FerreBot SaaS.

## Contenido de esta carpeta

- `spec-cliente/` — la especificación del cliente (16 módulos, 00–16), **renombrada según su contenido real**. Los archivos originales venían con los nombres cruzados.
- `skills-cliente/` — las 3 reglas/skills que venían con la spec (`colombia-compliance`, `money-safe`, `caveman`).
- `Brief-Prospecto-PIM.docx` — nuestro análisis fit-gap (qué ya tenemos vs. qué falta) + resumen de una página para compartir con el cliente.
- `PROMPT-FABLE-PLANNING.md` — prompt listo para dárselo a **Fable 5** y que arranque el planning de implementación.

## ⚠️ Los archivos originales venían con los nombres cruzados

El contenido no correspondía al nombre del archivo (p. ej. `react.md` era en realidad la arquitectura, `README.md` era el modelo de datos). Aquí ya quedaron ordenados por su contenido real. Mapa completo:

| Archivo original | Contenido real | Nombre nuevo |
|---|---|---|
| `react.md` | 00 · Arquitectura del sistema | `spec-cliente/00_ARQUITECTURA_SISTEMA.md` |
| `README.md` | 01 · Modelo de datos | `spec-cliente/01_MODELO_DATOS.md` |
| `python.md` | 02 · Clientes (CRM) | `spec-cliente/02_MODULO_CLIENTES.md` |
| `02_MODULE_CLIENTS.md` | 03 · Cotizaciones | `spec-cliente/03_MODULO_COTIZACIONES.md` |
| `09_MODULE_EXPENSES_PETTY_CASH.md` | 04 · Obras (presupuesto vs. real) | `spec-cliente/04_MODULO_OBRAS.md` |
| `15_EINVOICING_DIAN.md` | 05 · Inventario / Máquinas | `spec-cliente/05_MODULO_INVENTARIO_MAQUINAS.md` |
| `01_DATA_MODEL.md` | 06 · Inventario / Herramientas | `spec-cliente/06_MODULO_INVENTARIO_HERRAMIENTAS.md` |
| `HANDOFF_ES.md` | 07 · Inventario / Empleados | `spec-cliente/07_MODULO_EMPLEADOS.md` |
| `12_MODULE_PERFORMANCE_CALCULATOR.md` | 08 · Nómina (Colombia 2026 + patacalientes) | `spec-cliente/08_MODULO_NOMINA.md` |
| `07_MODULE_EMPLOYEES.md` | 09 · Gastos y caja menor | `spec-cliente/09_MODULO_GASTOS_CAJA_MENOR.md` |
| `14_TELEGRAM_BOT.md` | 10 · Proveedores | `spec-cliente/10_MODULO_PROVEEDORES.md` |
| `08_MODULE_PAYROLL.md` | 11 · Compras y márgenes (resbalos) | `spec-cliente/11_MODULO_COMPRAS_MARGENES.md` |
| `typescript.md` | 13 · Dashboard principal | `spec-cliente/13_MODULO_DASHBOARD.md` |
| `13_MODULE_DASHBOARD.md` | 14 · Bot de Telegram | `spec-cliente/14_BOT_TELEGRAM.md` |
| `00_ARCHITECTURE.md` | 15 · Facturación DIAN (MATIAS) | `spec-cliente/15_FACTURACION_DIAN.md` |
| `03_MODULE_QUOTES.md` | 16 · Orden de construcción | `spec-cliente/16_ORDEN_DE_CONSTRUCCION.md` |
| `05_MODULE_INVENTORY_MACHINES.md` | skill · colombia-compliance | `skills-cliente/skill_colombia-compliance.md` |
| `11_MODULE_PURCHASES_MARGINS.md` | skill · money-safe | `skills-cliente/skill_money-safe.md` |
| `10_MODULE_SUPPLIERS.md` | skill · caveman | `skills-cliente/skill_caveman.md` |

## Faltan 2 insumos — pedírselos a tu amigo

1. **Módulo 12 — Calculadora de rendimiento de máquinas.** Se referencia en el índice (módulo 00) y en el orden de construcción (módulo 16), pero el archivo con su contenido no llegó (la secuencia salta del 11 al 13).
2. **Reglas de stack** (`rules/react.md`, `rules/typescript.md`, `rules/python.md`). El índice las menciona, pero esos nombres de archivo traían contenido de módulos, no las reglas. No nos bloquean: usamos las reglas de nuestro propio repo (`CLAUDE.md`, `.claude/rules/`).

## Cómo seguir

Abre `PROMPT-FABLE-PLANNING.md`, cópialo completo y dáselo a **Fable 5** dentro de este repo. Producirá el plan de implementación por fases, empezando por lo que ya se puede construir sin depender del cliente.
