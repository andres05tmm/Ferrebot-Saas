# 14 — Telegram Bot

## Goal
Telegram bot reducing friction in two flows:
1. **Auto-register expenses** from Bancolombia transfer receipt captures (replaces the
   current WhatsApp-forwarding flow).
2. **Daily field reports** from supervisors/operators on site (machine hours, progress,
   incidents, photos).

## Stack
- Framework: `grammy` (TS) or `python-telegram-bot` (Python)
- Deployment: webhook to an API route (`/api/telegram-webhook`)
- Image processing: Anthropic Claude API with vision (structured extraction from
  captures)

## Setup
1. Create bot with @BotFather
2. Store token in `TELEGRAM_BOT_TOKEN`
3. Configure webhook to production URL
4. Each `Usuario` links their `telegramUserId` (`/vincular <code>`, code generated in
   the web panel).

## Commands & flows
### `/start`
Welcome + inline menu: 💰 Register expense | 📊 Daily project report | ⏱ Register
machine hours | 📷 Send progress photo | ❓ Help.

### Register expense (Bancolombia capture)
1. User sends photo or taps button
2. Bot: "Send the transfer receipt capture"
3. User sends image
4. Bot sends image to Claude Vision with structured prompt:
   ```
   Extract from this Bancolombia transfer receipt, respond ONLY with valid JSON:
   { "monto": number, "fecha": "YYYY-MM-DD", "hora": "HH:MM"|null,
     "destinatario": string, "numeroReferencia": string|null,
     "concepto": string|null, "confianza": number (0-1) }
   Null any field you can't read confidently.
   ```
5. Bot shows extracted data + buttons: ✅ Confirm | ✏️ Edit | ❌ Cancel
6. On confirm: ask category (buttons) + "for a project?" (list active projects)
7. Create `Gasto` with extracted data, `origenRegistro = TELEGRAM_BOT`,
   `telegramMessageId`, `telegramUserId`, image URL. If `confianza < 0.7` →
   `requiereRevision = true`
8. Confirm: "✅ Expense $X in category Y for project Z registered"

### Daily project report
1. Tap button → "Which project?" (active projects where user is assigned)
2. "Describe today's progress" → text
3. "m² or m³ done today?" (skippable)
4. "Any incidents?" (optional)
5. "Send photos, write `listo` when done"
6. Create `ReporteDiarioObra` with data + photo URLs → confirm

### Register machine hours
1. "Which machine?" (operator's assigned machines)
2. "Which project?" (prefill if single active)
3. "Hours worked today?" (validate)
4. "Notes?" (optional)
5. Create `RegistroHorasMaquina`, `origenRegistro = TELEGRAM_BOT` → confirm with calc:
   "6h logged. Min 5 covered. Revenue today: $900,000"

### Authorization
Only linked `telegramUserId` of active `Usuario` may use the bot. Unlinked → "Not
authorized. Ask admin for a linking code." Admin commands: ADMIN role only.

## Image storage
Receipts/photos stored in external bucket (R2, Cloudflare / Supabase Storage / S3), only
URL saved in DB. `[DEFINE provider — R2 is cheap and enough]`.

## Testing
`grammy`/python-telegram-bot with polling in dev, webhook in prod. Ngrok for local
webhook. Separate dev bot with distinct token.

## Acceptance criterion
User linking works. Bancolombia capture extracts amount/date/recipient correctly on ≥90%
of real test captures. Expenses enter with full data, low-confidence ones land in review
inbox. Daily reports saved with photos accessible from project dashboard. Machine-hours
registration feeds performance module.
