# Handoff Cowork — Cierre 9 jun 2026 (Fable 5: onboarding mágico + POS electrónico)

> Léelo al iniciar la sesión nueva, junto con `docs/handoff-cowork-2026-06-09-fable5-innovacion.md`
> (el rol, el patrón de trabajo y el principio económico de Fable siguen vigentes tal cual ahí).
> **Rol Cowork:** senior que escribe ADRs/planes y prompts para Claude Code, REVISA cada diff
> leyendo el código real, no ejecuta git. **Regla aprendida hoy:** toda sesión paralela de Claude
> Code trabaja en **git worktree propio** (línea obligatoria en cada prompt) — una colisión de
> working dir compartido corrompió `.git/HEAD` y desvió un commit (ya reparado).

## Qué se hizo hoy (todo con ADR → prompts → revisión → CI verde)

1. **Estrategia decidida:** de las 5 palancas, Andrés eligió **#2 onboarding mágico** y **#3 POS
   electrónico DIAN + Bre-B** (oferta a clientes). Billing/metering (#1) queda para después.
2. **ADR 0011 — onboarding mágico** + `docs/plan-onboarding-magico.md`: pipeline
   map→normalize→reduce→validate; **v1 = extracción en Cowork (costo API $0, suscripción)**, v2 =
   worker con router por dificultad (≤$1-2/alta). **MEZCLADO (PR #5):** pack POS declarativo del
   manifiesto (`tools/manifest/packs/pos.py`, reconcilia fracciones), `--check` del provisionador,
   eval dorado (`tools/eval_extractor.py`), `docs/runbook-onboarding-cowork.md`.
3. **Skill `onboarding-magico`** (contrato anti-alucinación + `scripts/normalizar_precios.py`
   probado): instalado en Cowork de Andrés Y en `.claude/skills/onboarding-magico/` del repo —
   sirve con modelos livianos porque el criterio vive en scripts + `--check`, no en el modelo.
4. **Audit fiscal** (`docs/audit-pos-electronico.md`, mezclado): hoy solo se emite FE manual;
   reconciliación/histórico eran promesas de doc sin código. **Research MATIAS v3 (Cowork, web):**
   POS electrónico = `POST /invoice` con `type_document_id=20` (id interno) + objeto
   `point_of_sale` obligatorio; **API autoincremento** `/auto-increment/pos-documents` (MATIAS
   asigna consecutivo → sin huecos/colisiones); **webhooks reales** (HMAC-SHA256, 6 reintentos).
5. **ADR 0012 — POS electrónico** (11 decisiones) + **F2.1/F2.2 implementadas** en
   `feat/pos-electronico` → **PR #6, CI verde, OK de merge dado por Cowork**: histórico XML +
   webhook `POST /webhooks/matias/{token}` (token→firma→idempotencia, ejemplar) + reconciliación
   cron + `tools/set_config.py` + `tools/registrar_webhook_matias.py`; POS tipo 20 vía
   autoincremento, hook post-venta con **commit-antes-de-encolar** y puerto `CierrePos` cableado en
   router HTTP **y** canal bot (`ai/tools.py::_registrar_venta` — fix de auditoría: el bot ES el
   mostrador). Todo inerte tras el flag `pos_electronico` (apagado).
6. `main` al cierre: `8c4c6d7` (docs+skill) sobre `864b21f` (merge PR #5).

## Pendientes (en orden)

1. **Mergear PR #6** si no se hizo (OK ya dado) → pull en main, retirar worktree
   `ferrebot-saas-pos`, apagar contenedores.
2. **Desplegar migraciones** (EN-RED vía `railway ssh`): control `0007` (webhooks_matias) +
   `python -m tools.migrate_tenants` (tenant 0013/0014/0015). Redeploy API+worker.
3. **Switch-on del POS en Punto Rojo** — checklist al final de `docs/plan-pos-electronico-breb.md`:
   resolución DIAN → portal MATIAS → `set_config` (resolution/prefix/terminal/address/cashier_type)
   → `registrar_webhook_matias` → flag `pos_electronico` → venta de prueba (bot y dashboard).
   **Gate previo:** confirmar contra sandbox MATIAS los shapes asumidos (punto 9 del reporte F2:
   payload/respuesta tipo 20, `operation_type_id=1`, claves de URLs XML/PDF, formato de firma).
4. **Estrenar onboarding mágico v1**: fotos/Excel reales + skill `onboarding-magico` → YAML →
   `--check` → alta por panel. Primera corrida real = calibración del skill.
5. **F2.3** (dashboard: estado fiscal de la venta, flujo "cliente pide factura", alerta de
   antigüedad) y **F2.4** (sección fiscal POS en el manifiesto — cruza con pack POS de 0011).
6. **Frente B — Bre-B:** research de PSPs (Wompi/Bold, estado jun-2026) por Cowork → ADR 0013.
7. Deuda anotada: estado `anulada` dedicado (hoy `voided` solo se anota), estado `enviada` de
   `facturacion-dian.md` sin implementar (alinear doc o implementar), `_resumen` del provisionador
   no cuenta tablas POS, onboarding v2 (worker+router) cuando haya volumen.
8. Vigentes del handoff anterior: rate-limit `/auth/reset/solicitar` + no loguear token cuando haya
   email real; plantilla `recordatorio_cita` de Kapso; **el bottleneck sigue siendo el primer
   cliente** — el paquete de venta ya existe: onboarding mágico + "te pongo legal con la DIAN".

## Gotchas operativos (además de los del handoff anterior)

- **Worktree SIEMPRE** en sesiones paralelas de Claude Code (línea en el prompt). Si git se
  comporta raro en el dir principal: revisar `.git/HEAD` (hoy quedó con bytes NUL tras una
  colisión; fix: `printf 'ref: refs/heads/main\n' > .git/HEAD`) y borrar `index.lock` huérfano.
- Asignación de modelos que funcionó: **Fable 5** para audits/razonamiento abierto sobre mucho
  contexto y diseño de prompts; **Opus 4.8** para implementación pautada por ADR; Sonnet para
  trivial. Cowork (Fable) hace research web + extracciones del skill sin costo API.
- El skill del repo (`.claude/skills/onboarding-magico/`) y el instalado en Cowork son copias:
  si se afina uno, sincronizar el otro.
