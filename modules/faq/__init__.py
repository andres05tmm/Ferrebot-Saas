"""Pack FAQ / conocimiento del negocio — capacidad TRANSVERSAL de cara al cliente.

Conocimiento no estructurado por tenant (ubicación, horarios, precios, formas de pago, parqueo,
políticas…) que cualquier agente puede consultar con la herramienta `responder_faq`. La recuperación
v1 es por palabras clave detrás de un puerto (`retrieval.Recuperador`), para migrar a embeddings/RAG
sin tocar el agente. Vive en la base del propio tenant (aislamiento por construcción).
"""
