# Gestión de secretos

> Detalle de cómo se guardan y usan las credenciales por empresa. Resumen en `/SECURITY.md`; tabla en `schema.md` (`secretos_empresa`).

## Qué es secreto

- **Plataforma (en `.env`, nunca en git):** `SECRET_KEY` (JWT), `SECRETS_MASTER_KEY`, `CONTROL_DATABASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`.
- **Por empresa (cifrado en control DB):** MATIAS (email, password, resolución, prefijo, consecutivos, DS-NO), Cloudinary (key/secret), token del bot de Telegram, y la URL de conexión de su app DB.

## Cifrado

- Algoritmo: **AEAD** (AES-256-GCM o `cryptography.Fernet`). Cada valor se cifra con un **nonce** único; se guardan `valor_cifrado` y `nonce` en `secretos_empresa`.
- **Envelope encryption (recomendado):** `SECRETS_MASTER_KEY` (KEK) cifra **claves de datos** (DEK) por empresa; las DEK cifran los secretos. Rotar la KEK no obliga a re-cifrar todos los secretos, solo las DEK.
- La `SECRETS_MASTER_KEY` vive en el entorno de la plataforma (o un KMS/Vault), nunca en la base ni en git.

## Acceso en runtime

- Los secretos se **descifran en memoria** al usarse (p. ej. al llamar a MATIAS), por empresa. No se escriben descifrados a disco ni a logs.
- Caché opcional en memoria del proceso con **TTL corto**; se borra al rotar o al cambiar el secreto.
- Los logs nunca incluyen secretos (filtrar/máscara).

## Rotación

- **Secreto de empresa** (p. ej. cambia la password de MATIAS): `PUT /admin/empresas/{id}/secretos` re-cifra y actualiza `actualizado_en`.
- **Master key:** generar nueva KEK → re-cifrar las DEK → activar. Procedimiento documentado en `runbook.md`.

## Evolución

- Empezar con cifrado propio + `SECRETS_MASTER_KEY`. Cuando crezca, migrar a un gestor (Doppler / Infisical / Vault / Railway secrets) sin cambiar el contrato (`secretos_empresa` sigue siendo la fuente lógica).
