# Handoff Cowork — Potenciar FerreBot SaaS con Claude Fable 5 (9 jun 2026)

> Léelo al iniciar la sesión nueva. **Rol:** Cowork = senior que diseña planes/ADRs y redacta prompts
> concisos para Claude Code; **Andrés** los pega en Claude Code, ejecuta, opera Railway/Kapso/Google/GitHub
> por navegador, y pega de vuelta los resultados. Cowork **no ejecuta git en el repo**: planea, **revisa
> cada diff** (lee el código real, no el resumen), y promptea la fase siguiente. Patrón de trabajo que
> funcionó: ADR → prompts por fase → revisión de cada diff → CI verde → merge; y **planear en paralelo
> mientras CI/suite corre** (no toca terminal → cero conflicto).

## Qué es FerreBot SaaS

Plataforma **multi-tenant de agentes de WhatsApp** para negocios de servicios (clínica/spa/beach club) +
**POS** para retail (ferretería Punto Rojo, tenant #1). Modelo: **runtime genérico + capability packs +
datos por tenant**. Aislamiento por **DB-per-tenant + control DB**. Transporte WhatsApp = **Kapso** (BSP).
LLM = Claude (Haiku worker / Sonnet orquestador). Mercado objetivo: **Cartagena** (87% de mipymes venden
por WhatsApp; competir vs Alegra/Siigo por el diferenciador "agente IA en WhatsApp", ver
`docs/saas-mercado-cartagena.md`).

## Estado actual — todo en `main` (= merge `a1f2e63`), con CI en verde

Construido en las últimas sesiones (cada uno con su ADR + PR mezclado):
- **Onboarding declarativo** (`docs/adr/0007`): `tools/provision_from_manifest.py` (manifiesto YAML →
  base→packs→wa_numero, idempotente). Esquema + validación en `tools/manifest/`.
- **Dashboard multi-vertical por packs** (`docs/adr/0008`): el POS dejó de ser "núcleo" → pasó al pack
  `pos`; cada tenant ve **solo sus packs** (una clínica no ve Inventario/Kárdex). Núcleo = {clientes,
  reportes}. Capa fiscal/DIAN **transversal** (no atada a `pos`).
- **Login real** (`docs/adr/0009`): email/contraseña en **link compartido**; directorio `identidades` en
  el control DB (argon2id); el tenant sale del usuario (claim `tenant` del JWT). Set-password/reset por
  token (Redis, single-use, TTL 1h). Telegram widget convive; `dev_token` solo dev.
- **Panel super-admin v1** (`docs/adr/0010`): auth de **plataforma** (super_admin, `empresa_id` nullable +
  CHECK, JWT `scope=platform` sin tenant, `/admin/*` exento de `TenantMiddleware` + `require_platform`);
  **job de provisioning en el worker** (slug estricto, lock por slug, estado en Redis, errores
  sanitizados — secretos jamás); endpoints `/admin/*`; frontend `/admin` (lista + crear tenant → encola →
  polling de estado; toggles; enlace set-password).
- **CI**: GitHub Actions (Postgres 18 + Redis + `uv run pytest`).

## Arquitectura (salud)

Monolito modular (`modules/` por dominio: router→service→repository) + **adaptadores hexagonales** (canal
desacoplado del cerebro; puertos `Protocol` inyectables) + layering tipo Clean. **Sana, sobre el
promedio**, amigable para trabajo paralelo (cortes verticales). A vigilar: **costo operacional del
DB-per-tenant** a gran volumen, y mantener el frontend tan disciplinado como el backend. (Pendiente un
**audit formal** — abajo.)

## 🎯 Misión de esta sesión: potenciar el producto con Claude Fable 5

**Fable 5** (lanzado 9 jun 2026, `claude-fable-5`): el modelo más capaz de uso general. 1M de contexto,
visión fuerte, estado del arte en ingeniería/ciencia/razonamiento. **Precio: $10/$50 por MTok** (2x Opus
4.8, 3x Sonnet). Consultas sensibles (ciber/bio/quím) se desvían a Opus 4.8 (no aplica a POS/agenda).

**Objetivo:** NO usar Fable solo para acelerar el grind ni para un audit-y-listo. Usarlo para **habilitar
una capacidad del producto que a un modelo más débil no le sale confiable**, que lleve el SaaS a otro
nivel y justifique el gasto.

**Principio económico (no negociable):** usa Fable como **"compilador" de baja frecuencia y alto valor**
(se corre una vez, la salida la sirve código barato) **o detrás de un router por dificultad** que solo lo
invoque en turnos genuinamente difíciles. **Nunca como el cerebro por-mensaje por defecto** (quemaría la
economía unitaria del agente, que vive bien en Haiku/Sonnet).

### Las tres posibilidades (de la sesión de exploración) — la #1 es la recomendada

1. **Onboarding mágico (RECOMENDADA).** El dueño manda una **foto de su lista de precios escrita a mano**,
   una **nota de voz** describiendo su negocio, o el **link de su Instagram**; Fable 5 (visión +
   razonamiento sobre lo desordenado) **extrae y estructura** todo en un **manifiesto válido** (servicios,
   precios, horarios, FAQ, persona) → el provisionador barato lo consume. Se corre **una vez por alta**
   (evento raro y valioso → costo acotado). Amplifica justo lo que ya construimos (manifiesto +
   provisionador + panel) y **ataca el cuello real: la fricción de onboardear clientes no-técnicos**. Es
   el "wow" que Alegra/Siigo no tienen.
2. **Agente que VENDE (no solo agenda), con router por tiers.** Cliente: "necesito arreglar una llave que
   gotea" → el agente razona, **arma la lista de materiales del catálogo** (con las fracciones/unidades —
   donde el modelo débil falla, p. ej. el bug de la lija de $400k), cotiza y crea el pedido. Bypass/Haiku
   para lo simple, Sonnet para lo medio, **Fable solo en el turno duro**. Mayor techo, más riesgo de costo.
3. **Consultor de negocio para el dueño.** Reporte (baja frecuencia) donde Fable razona sobre la data del
   tenant (ventas/citas/no-shows/conversaciones) y entrega **insights accionables** ("tus martes están
   vacíos, ofrece X"). Lo más barato de probar; muy vendible. Se monta como tarea programada/routine.

**Arranque sugerido:** aterrizar la #1 como **ADR + plan por fases** (esquema de "input natural → manifiesto",
qué herramienta de visión/extracción, cómo valida contra `tools/manifest`, dónde corre Fable, cómo cae en
el provisionador), y luego prompts por fase para Claude Code. Decidir con Andrés antes de codear.

## Pendientes / follow-ups vigentes

- **A1 (login):** rate-limit en `/auth/reset/solicitar`; quitar el log del token cuando exista envío de
  email real (hoy el token se loguea — por eso el TTL es 1h).
- **Panel:** verificar que el fix del `422 detail` (que `CrearTenantForm` muestre el motivo del rechazo,
  no "[object Object]") quedó incluido en el commit de B4.
- **Audit de arquitectura** (tarea anotada): grafo de dependencias, hotspots de merge (`catalogo.py`,
  `routes.jsx`, cadena Alembic), costo del DB-per-tenant. Buen primer caso para medir Fable vs Opus.
- **Remates del piloto WhatsApp:** aprobar la plantilla `recordatorio_cita` + var
  `KAPSO_TEMPLATE_RECORDATORIO` (cierra anti-no-show); pulir slot-filling/captura de nombres del agente.
- **El bottleneck NO es técnico:** conseguir el **primer cliente real** (trabajo de Andrés). El producto
  ya es entregable; la #1 (onboarding mágico) baja la fricción de ese paso.

## Mapa rápido

```
docs/adr/0007..0010                         — manifiesto/provisionador, packs, login, panel
docs/plan-*.md                              — planes por fase (provisionador, login, panel)
docs/roadmap-superficies-web.md             — A (login+dashboard) → B (panel) → C (landing+billing)
docs/saas-mercado-cartagena.md              — estrategia de mercado (Cartagena)
tools/provision_from_manifest.py            — provisionador (provision_from_manifest_obj para el job)
tools/manifest/{schema,loader,validacion,packs/}  — manifiesto + validación + loaders de packs
core/auth/{passwords,deps,jwt}.py           — argon2id, Principal/scope, JWT
core/tenancy/{identidades_repo,middleware,catalogo,resolver}.py
modules/admin/router.py · apps/worker/jobs.py (provisionar_tenant)  — panel + job
dashboard/src/pages/admin/ · components/PlatformRoute.jsx           — frontend del panel
```

## Gotchas operativos

- **Provisioning en prod = EN-RED vía `railway ssh`** (no desde local), para guardar el host privado.
- Secretos **cifrados** en el control DB; manifiestos reales **gitignored** (`tools/onboarding/*.yaml`).
- El **worker** necesita `ADMIN_DATABASE_URL` + `SECRETS_MASTER_KEY` (corre el job de provisioning).
- Local: Docker `ferrebot-pg`/`ferrebot-redis` arriba para correr el backend (necesita Postgres real).
- **No cambiar `SECRETS_MASTER_KEY`** en Railway (descifra los secretos).

## Cómo retomar

Usa el prompt de arranque que acompaña este handoff. Primer paso de la sesión: **leer este archivo + el
ADR de la dirección elegida**, confirmar con Andrés la posibilidad (#1 recomendada), y aterrizarla como
ADR + plan por fases antes de codear.
