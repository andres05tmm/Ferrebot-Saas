"""Pack transversal de conversación / handoff a humano (docs/whatsapp-agentes-arquitectura.md).

No es un capability pack de dominio (como agenda): es una capacidad TRANSVERSAL del runtime de cara
al cliente. Cualquier agente puede escalar a un humano; mientras la conversación esté en `humano`, el
runtime no corre el agente. El negocio resuelve (devuelve al bot) desde el dashboard.
"""
