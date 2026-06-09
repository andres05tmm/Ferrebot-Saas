# Roadmap — superficies web (dashboard, onboarding, comercial)

> Plan de producto (8 jun 2026). Cierra "¿qué nos falta para desplegar fácil a un cliente real?" del
> lado **web**. Acompaña a los ADR **0008** (dashboard multi-vertical por packs) y **0009** (login real).
> No es código: es el mapa y el orden. Construir = ir fase por fase con prompts para Claude Code.

## Dos audiencias, dos embudos

El producto sirve a **dos** tipos de usuario, y casi todo lo que falta cae en uno de los dos:

| Audiencia | Quién | Qué superficie usa |
|---|---|---|
| **Operador / super-admin** | Andrés (tú) | Panel de onboarding (das de alta clientes) |
| **Cliente** (dueño del negocio) | clínica, spa, ferretería | Su **dashboard**, al que entra por **login** |
| **Prospecto** (aún no cliente) | negocios de Cartagena | **Landing** pública (vende), **billing** (cobra) |

El aislamiento ya está resuelto: **mismo link para todos**, cada quien ve lo suyo porque su **JWT lleva su empresa** en el claim y el resolver apunta a su base (ver ADR 0009). El subdominio por empresa es pulido opcional, no requisito.

## Las superficies (y lo que faltaba)

1. **Dashboard cliente** — existe, pero mezcla POS y servicios. Hay que separarlo (ADR 0008) y pulir diseño.
2. **Login real** — hoy `dev_token` en consola. Greenfield: no hay infra de password (ADR 0009).
3. **Panel super-admin (onboarding)** — no existe. Es un **formulario que arma el manifiesto y llama a
   `provision_from_manifest`**. El backend ya está listo (ADR 0007), así que es "solo la piel".
4. **Landing / web de venta** — no existe. Capta prospectos, muestra planes.
5. **🔑 Billing / planes / cobro** — *(faltaba en la lista; lo más crítico del lado comercial)*. Sin esto,
   "tenemos producto pero no negocio". Una landing que vende sin forma de cobrar es medio embudo.
6. **Embedded signup del número de WhatsApp** — el cliente conecta SU número (Kapso/Meta) él mismo. Hoy
   lo haces tú a mano. Alcance futuro del panel/onboarding self-serve.
7. **Página de estado/uptime** — pública, da confianza; cierra el pendiente del monitor de `/health`.

## Secuencia recomendada (por dependencia, no por gusto)

**Fase A — Hacer el dashboard entregable a un cliente real (camino crítico).**
- **A1. Login real** (ADR 0009) — la puerta. Sin esto ningún cliente entra a nada.
- **A2. Separación de packs en el dashboard** (ADR 0008) — para que la clínica vea un dashboard limpio
  (solo agenda/conversaciones/conocimiento), no Inventario ni Kárdex.
- *Cierra:* un cliente de servicios entra a `app.tudominio.com`, hace login, y ve **su** dashboard limpio.
- *Diseño:* aquí entra `design:design-system` (tokens, consistencia) para no acumular deuda visual.

**Fase B — Tu panel de onboarding (super-admin).**
- **B1. Panel super-admin** = formulario → manifiesto → `provision_from_manifest` (backend ya hecho).
  Te quita el `railway ssh` para dar de alta. Empieza simple: un form que genera el YAML y lo aplica.
- *Cierra:* das de alta un tenant sin tocar terminal.

**Fase C — Embudo comercial (cuando vayas a captar/cobrar).**
- **C1. Landing** — pública, planes, captación. Usa `design:ux-copy` para los textos.
- **C2. Billing/planes** — define 2-3 planes (precio ≤ Alegra), estado de suscripción, medición de uso.
  Mes 1 puede ser **cobro manual documentado**; PSP (Wompi/Bold) cuando el volumen lo pida.
- *Cierra:* un negocio nuevo te descubre, se da de alta y te paga.

**Más adelante:** embedded signup del número, página de estado, analítica para el dueño (citas,
conversión, no-shows) como razón para que entre a diario.

## Por qué este orden

- **A antes que todo:** es el camino crítico para poner el producto frente a un cliente real. B y C asumen
  un dashboard cliente que funcione y al que se entre con login.
- **B es barato:** el backend del onboarding ya existe (ADR 0007); el panel es la piel encima.
- **C va cuando vendas en serio:** landing sin billing es medio embudo; mejor montar ambas juntas.
- Construir el panel o la landing **antes** del login + dashboard limpio sería al revés.

## Estado de arranque

- **A1/A2** se especifican en los ADR **0009** (login) y **0008** (packs). De ahí salen los prompts por fase
  para Claude Code, como hicimos con el provisionador.
- Mantener el patrón: ADR → prompts por fase → Cowork revisa cada diff → CI verde → merge.

## Checklist de fases

- [ ] A1 — Login real (ADR 0009): directorio global en control DB + password + form, reemplaza dev_token.
- [ ] A2 — Dashboard por packs (ADR 0008): POS deja de ser núcleo; clínica ve dashboard limpio.
- [ ] B1 — Panel super-admin: form → manifiesto → provisionador.
- [ ] C1 — Landing pública.
- [ ] C2 — Billing/planes (manual mes 1 → PSP después).
