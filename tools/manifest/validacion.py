"""Validación semántica del manifiesto (ADR 0007 §D5.1). Falla cerrado, sin IO ni BD.

Reúne TODOS los errores y los lanza juntos en un `ErrorManifiesto` (subclase de ValueError) para que
el operador arregle el YAML de una sola pasada. Reutiliza la lógica ya probada en vez de duplicarla:

- features del catálogo y sus dependencias → `core/tenancy/catalogo` (es_feature_valida,
  validar_dependencias, capacidades_completas).
- set EFECTIVO de features (plan ± overrides) → `tools.provision_tenant._features_efectivas`.
- enum de tipo de recurso → `modules.agenda.models.recurso_tipo` (misma fuente que la columna).

Además chequea COHERENCIA flag↔datos: no declarar datos de un pack cuya feature no esté activa
(la inversa —flag activo sin datos— es válida, el negocio nutre su data después).
"""
from __future__ import annotations

import re

from core.tenancy.catalogo import (
    capacidades_completas,
    es_feature_valida,
    validar_dependencias,
)
from modules.agenda.models import recurso_tipo
from tools.manifest.schema import Manifiesto, normalizar_nombre
from tools.provision_tenant import _features_efectivas

# "HH:MM-HH:MM" con horas 00..23 y minutos 00..59.
_FRANJA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")

# Tipos válidos de recurso: misma fuente que la columna (enum recurso_tipo del esquema).
_TIPOS_RECURSO: frozenset[str] = frozenset(recurso_tipo.enums)

# IVA permitido en Colombia (ADR 0011 §D3): exento, tarifa reducida, tarifa general.
_IVA_VALIDO: frozenset[int] = frozenset({0, 5, 19})


class ErrorManifiesto(ValueError):
    """Manifiesto inválido: agrupa todos los errores encontrados (uno por línea)."""


def _efectivas(manifiesto: Manifiesto) -> frozenset[str]:
    """Set EFECTIVO de capacidades: NÚCLEO ∪ (plan ± overrides). PURO (reúsa provision_tenant)."""
    plan_features = list(manifiesto.plan.features) if manifiesto.plan else []
    return capacidades_completas(_features_efectivas(plan_features, manifiesto.features_override))


def _errores_features(manifiesto: Manifiesto, efectivas: frozenset[str]) -> list[str]:
    """Features del plan + overrides: que existan y que el set EFECTIVO cumpla dependencias."""
    errores: list[str] = []
    plan_features = list(manifiesto.plan.features) if manifiesto.plan else []

    for feature in [*plan_features, *manifiesto.features_override.keys()]:
        if not es_feature_valida(feature):
            errores.append(f"feature desconocida: '{feature}'")

    for err in validar_dependencias(efectivas):
        errores.append(f"dependencia no satisfecha: {err}")
    return errores


def _errores_coherencia(manifiesto: Manifiesto, efectivas: frozenset[str]) -> list[str]:
    """Coherencia flag↔datos: no se pueden declarar datos de un pack cuya feature no esté activa.

    Solo se chequea la dirección "datos sin flag" (cargar data muerta es un error de configuración);
    la inversa —flag activo sin datos— es válida: el negocio puede nutrir su data después.
    """
    errores: list[str] = []
    agenda = manifiesto.packs.agenda
    if agenda is not None and (agenda.servicios or agenda.recursos) and "pack_agenda" not in efectivas:
        errores.append("packs.agenda declarado pero la feature pack_agenda no está activa")
    faq = manifiesto.packs.faq
    if faq is not None and faq.entradas and "pack_faq" not in efectivas:
        errores.append("packs.faq declarado pero la feature pack_faq no está activa")
    pos = manifiesto.packs.pos
    if pos is not None and (pos.productos or pos.aliases) and "pos" not in efectivas:
        errores.append("packs.pos declarado pero la feature pos no está activa")
    if manifiesto.canal.whatsapp is not None and "canal_whatsapp" not in efectivas:
        errores.append("canal.whatsapp declarado pero la feature canal_whatsapp no está activa")
    return errores


def _errores_agenda(manifiesto: Manifiesto) -> list[str]:
    """Recursos: tipo en el enum, días en 0..6, franjas "HH:MM-HH:MM", `presta` -> servicio declarado."""
    agenda = manifiesto.packs.agenda
    if agenda is None:
        return []

    errores: list[str] = []
    servicios_declarados = {s.nombre for s in agenda.servicios}

    for recurso in agenda.recursos:
        rotulo = f"recurso '{recurso.nombre}'"
        if recurso.tipo not in _TIPOS_RECURSO:
            opciones = "|".join(sorted(_TIPOS_RECURSO))
            errores.append(f"{rotulo}: tipo inválido '{recurso.tipo}' (esperado: {opciones})")
        for servicio in recurso.presta:
            if servicio not in servicios_declarados:
                errores.append(
                    f"{rotulo}: presta el servicio '{servicio}', que no está declarado en packs.agenda.servicios"
                )
        for disp in recurso.disponibilidad:
            for dia in disp.dias:
                if not 0 <= dia <= 6:
                    errores.append(f"{rotulo}: día fuera de rango {dia} (esperado 0..6, 0=lunes)")
            for franja in disp.franjas:
                if not _FRANJA.match(franja):
                    errores.append(f"{rotulo}: franja mal formada '{franja}' (esperado \"HH:MM-HH:MM\")")
    return errores


def _errores_producto_pos(p, rotulo: str) -> list[str]:
    """Reglas de UN producto POS: precios > 0, IVA válido, fracciones/escalonado coherentes."""
    errores: list[str] = []
    if p.precio_venta <= 0:
        errores.append(f"{rotulo}: precio_venta debe ser > 0 (es {p.precio_venta})")
    if p.precio_compra is not None and p.precio_compra <= 0:
        errores.append(f"{rotulo}: precio_compra debe ser > 0 si se declara (es {p.precio_compra})")
    if p.iva not in _IVA_VALIDO:
        errores.append(f"{rotulo}: iva inválido {p.iva} (esperado: 0, 5 o 19)")
    if p.fracciones and not p.permite_fraccion:
        errores.append(f"{rotulo}: tiene fracciones pero permite_fraccion es false")
    for f in p.fracciones:
        if f.precio_total <= 0:
            errores.append(f"{rotulo}: fracción '{f.fraccion}' precio_total debe ser > 0")
        if f.precio_unitario is not None and f.precio_unitario <= 0:
            errores.append(f"{rotulo}: fracción '{f.fraccion}' precio_unitario debe ser > 0 si se declara")
        if f.decimal is not None and f.decimal <= 0:
            errores.append(f"{rotulo}: fracción '{f.fraccion}' decimal debe ser > 0 si se declara")
        # Coherencia aritmética solo cuando ambos existan: decimal × precio_unitario ≈ precio_total
        # (tolerancia 1 peso). Mata el dato incoherente antes de que el bot cotice mal.
        if f.decimal is not None and f.precio_unitario is not None:
            esperado = f.decimal * f.precio_unitario
            if abs(esperado - f.precio_total) > 1:
                errores.append(
                    f"{rotulo}: fracción '{f.fraccion}' incoherente: decimal×precio_unitario="
                    f"{esperado:g} ≠ precio_total={f.precio_total} (tolerancia 1 peso)"
                )
    esc = p.escalonado
    if esc is not None:
        if esc.umbral <= 0:
            errores.append(f"{rotulo}: escalonado.umbral debe ser > 0 (es {esc.umbral})")
        if esc.bajo <= 0:
            errores.append(f"{rotulo}: escalonado.bajo debe ser > 0 (es {esc.bajo})")
        if esc.sobre <= 0:
            errores.append(f"{rotulo}: escalonado.sobre debe ser > 0 (es {esc.sobre})")
    return errores


def _errores_pos(manifiesto: Manifiesto) -> list[str]:
    """Pack POS: reglas por producto + unicidad de clave natural (codigo/nombre) + alias→producto."""
    pos = manifiesto.packs.pos
    if pos is None:
        return []

    errores: list[str] = []
    nombres_norm: dict[str, int] = {}
    codigos: dict[str, int] = {}
    for p in pos.productos:
        rotulo = f"producto '{p.nombre}'"
        errores.extend(_errores_producto_pos(p, rotulo))
        clave = normalizar_nombre(p.nombre)
        nombres_norm[clave] = nombres_norm.get(clave, 0) + 1
        if p.codigo is not None:
            codigos[p.codigo] = codigos.get(p.codigo, 0) + 1

    for clave, n in nombres_norm.items():
        if n > 1:
            errores.append(f"nombre de producto duplicado (normalizado): '{clave}' aparece {n} veces")
    for codigo, n in codigos.items():
        if n > 1:
            errores.append(f"codigo de producto duplicado: '{codigo}' aparece {n} veces")

    # alias.producto → debe referir a un producto declarado (por nombre normalizado).
    declarados = set(nombres_norm)
    for a in pos.aliases:
        if a.producto is not None and normalizar_nombre(a.producto) not in declarados:
            errores.append(
                f"alias '{a.termino}': referencia el producto '{a.producto}', "
                f"que no está declarado en packs.pos.productos"
            )
    return errores


def validar(manifiesto: Manifiesto) -> None:
    """Valida el manifiesto completo. No devuelve nada si es válido; si no, lanza `ErrorManifiesto`.

    Falla cerrado: corre antes de cualquier escritura, así que un manifiesto inválido no toca la BD.
    """
    efectivas = _efectivas(manifiesto)
    errores = [
        *_errores_features(manifiesto, efectivas),
        *_errores_coherencia(manifiesto, efectivas),
        *_errores_agenda(manifiesto),
        *_errores_pos(manifiesto),
    ]
    if errores:
        raise ErrorManifiesto(
            "manifiesto inválido (" + str(len(errores)) + " error(es)):\n  - " + "\n  - ".join(errores)
        )
