"""Canal Telegram público del agente de clientes (espejo de `apps/wa/`).

Un bot de Telegram por empresa (token de BotFather, cifrado por tenant) cuyo "cerebro" es el runtime
del agente de clientes que ya existe (`apps.wa.agent.AgenteWa`): no reimplementa lógica de
pedidos/menú/FAQ/handoff, solo cambia el tubo de entrada (webhook) y salida (Bot API). Ver
`docs/plan-demo-sirius.md` §3.
"""
