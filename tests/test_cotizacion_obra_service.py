"""Dominio del cotizador AIU: totales por la función pura, ciclo de vida de estados, editabilidad,
conversión a obra e IDEMPOTENCIA de la conversión (test-primero del invariante).

Todo contra repos FALSOS (sin BD): el núcleo de negocio se prueba aparte del wiring HTTP y del SQL.
La idempotencia de la conversión (una cotización ya convertida no crea una segunda obra) es un
invariante crítico del contrato Ola A: se prueba aquí sobre `ObrasService.crear_desde_cotizacion`
(donde vive la lógica) y, contra Postgres real (UNIQUE), en la integración.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from modules.cotizacion_obra.errors import (
    CotizacionInexistente,
    CotizacionNoEditable,
    CotizacionNoGanada,
    TransicionEstadoInvalida,
)
from modules.cotizacion_obra.schemas import (
    CotizacionObraActualizar,
    CotizacionObraCrear,
    ItemCotizacionObraCrear,
)
from modules.cotizacion_obra.service import CotizacionObraService
from modules.obra.service import ObrasService


def _cot(cid=1, estado="BORRADOR", **pct):
    """Namespace con la forma de CotizacionObra para el servicio (pcts + estado)."""
    return SimpleNamespace(
        id=cid,
        estado=estado,
        cliente_id=7,
        nombre_obra="Vía",
        ubicacion=None,
        administracion_pct=Decimal(str(pct.get("a", "0"))),
        imprevistos_pct=Decimal(str(pct.get("i", "0"))),
        utilidad_pct=Decimal(str(pct.get("u", "0"))),
        iva_sobre_utilidad_pct=Decimal(str(pct.get("iva", "0.19"))),
    )


def _item(cantidad, valor):
    return SimpleNamespace(cantidad=Decimal(str(cantidad)), valor_unitario=Decimal(str(valor)))


class _FakeCotizRepo:
    """Repo en memoria del cotizador (implementa el puerto que usa el servicio)."""

    def __init__(self, cotizaciones=None, items=None) -> None:
        self._cot = cotizaciones or {}
        self._items = items or {}
        self._contador = 0

    async def siguiente_numero(self, *, anio: int) -> str:
        self._contador += 1
        return f"PIM-{self._contador:03d}-{anio}"

    async def obtener(self, cotizacion_id):
        return self._cot.get(cotizacion_id)

    async def items_de(self, cotizacion_id):
        return self._items.get(cotizacion_id, [])

    async def listar(self, *, estado=None, cliente_id=None):
        return [(c, self._items.get(c.id, [])) for c in self._cot.values()]

    async def crear(self, datos, *, numero):
        cid = len(self._cot) + 1
        cot = _cot(cid, a=datos.administracion_pct, i=datos.imprevistos_pct,
                   u=datos.utilidad_pct, iva=datos.iva_sobre_utilidad_pct)
        cot.numero = numero
        self._cot[cid] = cot
        self._items[cid] = [_item(it.cantidad, it.valor_unitario) for it in datos.items]
        return cot

    async def actualizar_cabecera(self, cotizacion, cambios):
        for k, v in cambios.items():
            setattr(cotizacion, k, v)
        return cotizacion

    async def reemplazar_items(self, cotizacion_id, items):
        self._items[cotizacion_id] = [_item(it.cantidad, it.valor_unitario) for it in items]

    async def cambiar_estado(self, cotizacion, nuevo_estado):
        cotizacion.estado = nuevo_estado
        return cotizacion


class _FakeObrasConversion:
    """Puerto de conversión: registra la cotización recibida y devuelve una obra."""

    def __init__(self) -> None:
        self.llamadas = []

    async def crear_desde_cotizacion(self, cotizacion):
        self.llamadas.append(cotizacion.id)
        return SimpleNamespace(id=100 + cotizacion.id, cotizacion_id=cotizacion.id, estado="PLANIFICADA")


def _servicio(cotizaciones=None, items=None):
    return CotizacionObraService(_FakeCotizRepo(cotizaciones, items), _FakeObrasConversion())


# ── Totales por la función pura ──────────────────────────────────────────────────────────────
async def test_crear_calcula_totales_por_la_funcion_pura():
    servicio = _servicio()
    datos = CotizacionObraCrear(
        cliente_id=7, nombre_obra="Vía La Paz",
        administracion_pct=Decimal("0.05"), imprevistos_pct=Decimal("0.03"),
        utilidad_pct=Decimal("0.04"), iva_sobre_utilidad_pct=Decimal("0.19"),
        items=[ItemCotizacionObraCrear(orden=1, descripcion="Base", unidad="m3",
                                       cantidad=Decimal("1000"), valor_unitario=Decimal("10000"))],
    )
    armada = await servicio.crear(datos)
    # Caso de aceptación del plan: sub 10.000.000, A5/I3/U4 → total 11.276.000.
    assert armada.totales.subtotal == Decimal("10000000.00")
    assert armada.totales.administracion == Decimal("500000.00")
    assert armada.totales.utilidad == Decimal("400000.00")
    assert armada.totales.iva_utilidad == Decimal("76000.00")   # IVA SÓLO sobre la utilidad
    assert armada.totales.total == Decimal("11276000.00")
    assert armada.cotizacion.numero == "PIM-001-2026"           # consecutivo autogenerado


async def test_crear_respeta_numero_explicito_sin_autogenerar():
    servicio = _servicio()
    datos = CotizacionObraCrear(numero="PIM-042-2026", cliente_id=7, nombre_obra="Vía")
    armada = await servicio.crear(datos)
    assert armada.cotizacion.numero == "PIM-042-2026"


# ── Ciclo de vida de estados ─────────────────────────────────────────────────────────────────
_VALIDAS = [
    ("BORRADOR", "ENVIADA"), ("BORRADOR", "PERDIDA"),
    ("ENVIADA", "GANADA"), ("ENVIADA", "PERDIDA"), ("ENVIADA", "VENCIDA"),
    ("VENCIDA", "ENVIADA"),
]
_INVALIDAS = [
    ("BORRADOR", "GANADA"),   # no se gana un borrador sin enviarlo
    ("BORRADOR", "VENCIDA"),
    ("ENVIADA", "BORRADOR"),  # no se vuelve a borrador
    ("GANADA", "PERDIDA"),    # GANADA es terminal
    ("GANADA", "ENVIADA"),
    ("PERDIDA", "ENVIADA"),   # PERDIDA es terminal
    ("ENVIADA", "ENVIADA"),   # no-op no es transición
]


@pytest.mark.parametrize("actual,destino", _VALIDAS)
async def test_transicion_valida_se_aplica(actual, destino):
    servicio = _servicio({1: _cot(1, estado=actual)})
    armada = await servicio.cambiar_estado(1, destino)
    assert armada.cotizacion.estado == destino


@pytest.mark.parametrize("actual,destino", _INVALIDAS)
async def test_transicion_invalida_se_rechaza(actual, destino):
    servicio = _servicio({1: _cot(1, estado=actual)})
    with pytest.raises(TransicionEstadoInvalida):
        await servicio.cambiar_estado(1, destino)


async def test_cambiar_estado_inexistente_404():
    servicio = _servicio({})
    with pytest.raises(CotizacionInexistente):
        await servicio.cambiar_estado(999, "ENVIADA")


# ── Editabilidad del builder ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("estado", ["BORRADOR", "ENVIADA"])
async def test_editar_permitido_en_estados_vivos(estado):
    servicio = _servicio({1: _cot(1, estado=estado)})
    armada = await servicio.actualizar(1, CotizacionObraActualizar(nombre_obra="Nuevo nombre"))
    assert armada.cotizacion.nombre_obra == "Nuevo nombre"


@pytest.mark.parametrize("estado", ["GANADA", "PERDIDA", "VENCIDA"])
async def test_editar_bloqueado_en_estados_cerrados(estado):
    servicio = _servicio({1: _cot(1, estado=estado)})
    with pytest.raises(CotizacionNoEditable):
        await servicio.actualizar(1, CotizacionObraActualizar(nombre_obra="X"))


# ── Conversión a obra: precondición ──────────────────────────────────────────────────────────
async def test_convertir_no_ganada_rechaza():
    servicio = _servicio({1: _cot(1, estado="ENVIADA")})
    with pytest.raises(CotizacionNoGanada):
        await servicio.convertir_a_obra(1)


async def test_convertir_inexistente_404():
    servicio = _servicio({})
    with pytest.raises(CotizacionInexistente):
        await servicio.convertir_a_obra(999)


async def test_convertir_ganada_delega_y_devuelve_obra():
    conv = _FakeObrasConversion()
    servicio = CotizacionObraService(_FakeCotizRepo({1: _cot(1, estado="GANADA")}), conv)
    obra = await servicio.convertir_a_obra(1)
    assert obra.cotizacion_id == 1 and obra.estado == "PLANIFICADA"
    assert conv.llamadas == [1]


# ── IDEMPOTENCIA de la conversión (test-primero, invariante) ─────────────────────────────────
class _FakeObrasRepo:
    """Repo de obras en memoria: cuenta inserciones y respeta la unicidad por `cotizacion_id`."""

    def __init__(self) -> None:
        self._por_cotizacion = {}
        self.inserciones = 0

    async def obtener_por_cotizacion(self, cotizacion_id):
        return self._por_cotizacion.get(cotizacion_id)

    async def crear_desde_cotizacion(self, cotizacion):
        self.inserciones += 1
        obra = SimpleNamespace(id=500, cotizacion_id=cotizacion.id, estado="PLANIFICADA")
        self._por_cotizacion[cotizacion.id] = obra
        return obra


async def test_convertir_dos_veces_no_duplica_obra():
    """Doble conversión de la MISMA cotización → una sola obra (idempotencia)."""
    repo = _FakeObrasRepo()
    obras = ObrasService(repo)
    cot = _cot(1, estado="GANADA")

    primera = await obras.crear_desde_cotizacion(cot)
    segunda = await obras.crear_desde_cotizacion(cot)

    assert primera is segunda                # misma obra
    assert repo.inserciones == 1             # sólo se insertó una vez
