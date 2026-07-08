# RBAC del vertical construcción — mapeo de roles (PIM, decisión Fase 1)

> Decisión del plan `piped-hatching-sloth.md` §3/§55: *"`Usuario`/roles → RBAC existente (mapear
> CONTADOR/SUPERVISOR/OPERADOR a roles actuales o extender; decisión en Fase 1)."*
> Este documento fija ese mapeo para que las fases que gatean por rol lo referencien. Ver
> `docs/auth-rbac.md` (matriz de permisos) y `core/auth/rbac.py` (jerarquía).

## Decisión

**Se reusa el RBAC existente SIN crear roles nuevos.** El sistema hoy modela los roles como una
jerarquía lineal fija en código (`core/auth/rbac.py`), no como roles configurables por empresa:

```
vendedor (1) < admin (2) < super_admin (3)
```

`require_role(x)` es un gate por **rango** (`satisface`: el rol del usuario debe tener rango ≥ al
requerido). `super_admin` es el operador del SaaS (identidad de plataforma, `tenant_id=null`), no un
rol de negocio del tenant: los roles de negocio de una empresa son solo `admin` y `vendedor`.

La spec del cliente (`spec-cliente/01_MODELO_DATOS.md`) define cinco roles:

```prisma
enum RolUsuario { ADMIN CONTADOR SUPERVISOR OPERADOR SOLO_LECTURA }   // default OPERADOR
```

Como el ladder tiene dos peldaños de negocio, los cinco de la spec se **colapsan** sobre `admin` /
`vendedor`. No se introduce ningún rol nuevo en esta fase.

## Tabla de mapeo

| Rol de la spec (`RolUsuario`) | Rol existente | Justificación / caveat |
|---|---|---|
| `ADMIN` | `admin` | Directo: acceso total de la empresa (usuarios, precios, reportes financieros, anulación). |
| `CONTADOR` | `admin` | El ladder lineal no tiene un rol financiero ortogonal. El contador necesita reportes financieros, nómina y facturación electrónica, todos gateados a `admin` en la matriz. **Sobre-otorga** gestión de usuarios y anulación de ventas. Candidato #1 de la expansión (ver abajo). |
| `SUPERVISOR` | `vendedor` | Captura operativa de campo (reportes diarios de obra, horas de máquina, avance). No ve reportes financieros. `get_filtro_usuario` lo acota a lo suyo. |
| `OPERADOR` | `vendedor` | Default de la spec. Captura su propia data desde el bot/dashboard; ve solo lo suyo. |
| `SOLO_LECTURA` | `vendedor` | El ladder no tiene un piso *read-only* por debajo de `vendedor` (que sí puede escribir). Se mapea a `vendedor` como piso más cercano. **Caveat:** la restricción de solo-lectura NO es exigible hoy; queda como gap de la expansión. |

### Gating recomendado para las CRUD de la Fase 1

Con este mapeo, los recursos nuevos del contrato se gatean así (por `require_role`, encima del gate de
feature-flag que ya trae cada router):

| Recurso | Feature | Rol mínimo de escritura (POST/PATCH/DELETE) | Rol de lectura (GET) |
|---|---|---|---|
| `/obras` | `obras` | `vendedor` (supervisores/operadores registran avance) | `vendedor` |
| `/maquinas` | `maquinaria` | `admin` (catálogo/costo = dato administrativo) | `vendedor` |
| `/herramientas` | `herramientas` | `admin` | `vendedor` |
| `/trabajadores` | `nomina` | `admin` (dato de nómina, sensible) | `admin` |

> Nota: es una recomendación de referencia; cada CRUD la fija en su router. La regla general del repo
> (`auth-rbac.md`) sigue vigente: toda ruta de negocio lleva `get_current_user` + `require_feature`.

## Propuesta de expansión (NO se implementa en Fase 1 — reportada)

El sistema **no** soporta roles configurables por empresa: los roles son un `IntEnum` fijo en código
(`core/auth/rbac.py`) más el enum de BD `usuario_rol` (compartido por todos los tenants). El propio
`auth-rbac.md` ya reserva espacio: *"`cajero`/`supervisor` quedan como expansión del enum
`usuario_rol`."*

El colapso de arriba tiene dos costuras que una fase futura debería cerrar cuando el negocio lo pida:

1. **`CONTADOR` ortogonal.** Un rol financiero que vea reportes/nómina/FE **sin** poder gestionar
   usuarios ni anular ventas no cabe en un ladder lineal. Requiere pasar de "rango" a **capacidades**
   (gate por permiso, no por peldaño) o añadir un rol lateral.
2. **`SOLO_LECTURA` real.** Necesita un piso por debajo de `vendedor` que niegue toda escritura; hoy
   no existe y no es exigible.

**Opción sugerida (para ADR, no ahora):** extender `Rol` (`core/auth/rbac.py`) y el enum `usuario_rol`
con `contador` y `solo_lectura`, o —mejor a largo plazo— mover los gates críticos a capacidades
explícitas. Cualquiera de las dos toca el enum de BD compartido y el emisor de JWT, así que es una
decisión de arquitectura con su propia migración y pruebas; queda fuera del alcance de la Fase 1.
