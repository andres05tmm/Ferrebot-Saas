"""Cobro de una cita → venta (ADR 0022): el puente agenda → contabilidad.

Orquesta el repo de agenda y el servicio de ventas SOBRE LA MISMA SESIÓN (una sola transacción):
toma la cita con `FOR UPDATE`, crea la venta con línea VARIA (no toca stock por construcción) e
idempotency_key derivada de la cita, y vincula `citas.venta_id` (UNIQUE) antes del commit. NO postea
`caja_movimientos`: el arqueo híbrido cuadra por `ventas_efectivo` (guardrail de `caja/arqueo.py`).

El módulo es aparte de `service.py` para no engordar el motor de agendamiento: el cobro es un caso
de uso contable, no de agenda.
"""
from dataclasses import dataclass
from decimal import Decimal

from modules.agenda.errors import AgendaError, CitaInexistente
from modules.agenda.repository import AgendaRepo
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService

# Estados desde los que se puede cobrar. `cumplida` entra SOLO si aún no tiene venta vinculada
# (atendida primero, cobrada después); canceladas/no_show jamás (ADR 0022 §D4).
_ESTADOS_COBRABLES = ("pendiente", "confirmada", "cumplida")


class CitaNoCobrable(AgendaError):
    """La cita no admite cobro (estado terminal sin cobro, o falta el precio)."""

    def __init__(self, cita_id: int, motivo: str) -> None:
        super().__init__(f"La cita {cita_id} no se puede cobrar: {motivo}")
        self.cita_id = cita_id
        self.motivo = motivo


@dataclass(frozen=True, slots=True)
class ResultadoCobro:
    venta_id: int
    total: Decimal
    replay: bool  # True = la cita ya estaba cobrada (o la venta ya existía): misma venta, sin duplicar


def _idempotency_key(cita_id: int) -> str:
    return f"cita-cobro:{cita_id}"


async def cobrar_cita(
    cita_id: int,
    *,
    repo: AgendaRepo,
    ventas: VentaService,
    usuario_id: int,
    metodo_pago: str,
    precio_override: Decimal | None = None,
) -> ResultadoCobro:
    """Cobra la cita: crea la venta (o la reusa) y la vincula. Idempotente; ver ADR 0022.

    `repo` y `ventas` DEBEN compartir la sesión del tenant: la venta y el vínculo van en la misma
    transacción, y el `FOR UPDATE` de la cita serializa cobros concurrentes.
    """
    cita = await repo.cita_para_cobro(cita_id)
    if cita is None:
        raise CitaInexistente(cita_id)

    # Ya cobrada: devolver la MISMA venta (replay). No exige precio: reintentos de red inocuos.
    if cita.venta_id is not None:
        venta = await ventas.obtener_venta(cita.venta_id)
        if venta is not None:
            return ResultadoCobro(venta_id=venta.id, total=venta.total, replay=True)

    if cita.estado not in _ESTADOS_COBRABLES:
        raise CitaNoCobrable(cita_id, f"está '{cita.estado}'")

    servicio = await repo.servicio_por_id(cita.servicio_id)
    precio = precio_override if precio_override is not None else (servicio.precio if servicio else None)
    if precio is None or precio <= 0:
        raise CitaNoCobrable(cita_id, "el servicio no tiene precio; envía precio_override")

    nombre = servicio.nombre if servicio else "Servicio"
    datos = VentaCrear(
        metodo_pago=metodo_pago,
        origen="web",
        idempotency_key=_idempotency_key(cita.id),
        lineas=[
            VentaDetalleCrear(
                producto_id=None,                              # línea VARIA: no descuenta stock
                descripcion=f"{nombre} — cita #{cita.id}",
                cantidad=Decimal("1"),
                precio_unitario=precio,
                iva=0,
            )
        ],
    )
    # `registrar_venta` replaya por idempotency_key: si la venta ya existía (crash entre crear y
    # vincular, o carrera perdida tras el FOR UPDATE), devuelve la misma y aquí solo se re-vincula.
    resultado = await ventas.registrar_venta(datos, vendedor_id=usuario_id)
    await repo.vincular_venta(cita, resultado.venta.id)
    return ResultadoCobro(
        venta_id=resultado.venta.id, total=resultado.venta.total, replay=resultado.replay
    )
