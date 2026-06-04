# Alta de una empresa (onboarding)

1. **Registro:** el super-admin crea la empresa en el panel (nombre, NIT, slug/subdominio, plan).
2. **Base de datos:** el sistema crea la app DB de la empresa y corre `migrations/tenant` (upgrade head).
3. **Semilla:** datos base (categorías, métodos de pago, config inicial).
4. **Secretos:** cargar cifrados MATIAS (email/password/resolución/prefijo/consecutivos, DS-NO), Cloudinary y el token del bot de Telegram.
5. **Branding:** logo, color, nombre comercial, dominio.
6. **Admin:** crear el usuario administrador de la empresa.
7. **Bot:** registrar el webhook `/tg/{empresa}` con el token de su bot.
8. **Verificación:** smoke test (una venta de prueba, una emisión de factura de prueba).
9. **Suscripción:** marcar `estado = activa` (cobro manual por ahora).

> Para Punto Rojo (tenant #1), tras los pasos 1-3 se **copian** los datos desde FerreBot (ver `architecture.md` §17).
