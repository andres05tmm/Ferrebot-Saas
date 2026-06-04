# PRD — FerreBot SaaS

## Problema
Las ferreterías necesitan un POS completo (ventas, inventario, caja, facturación DIAN) operable por chat/voz y por web, confiable incluso sin internet. Hoy existe FerreBot para una sola tienda; se quiere ofrecer como producto a varias.

## Usuarios
- Vendedor: registra ventas (web, Telegram, voz), consulta stock.
- Admin de empresa: ve todo, gestiona usuarios, caja, facturación.
- Super-admin (operador SaaS): da de alta empresas, planes, cobro.

## Alcance v1
Paridad con FerreBot + multi-tenant (base por empresa) + PWA offline + provisioning de empresas. Lanza operando solo Punto Rojo.

## Fuera de alcance v1
Hardware de POS, WhatsApp, billing automatizado, autoservicio de registro, Habeas Data formal (anotados para futuro).

## Métricas de éxito
Punto Rojo operando 100% en el nuevo sistema; alta de una segunda empresa sin cambios de código; ventas no se pierden ante cortes de internet.
