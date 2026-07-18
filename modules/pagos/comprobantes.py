"""Registro del comprobante de pago que el cliente manda por foto (plan demo Sirius §4).

Visión (`ai/vision/recibo.py`) ya leyó la foto y entregó un `ReciboExtraido`. Aquí se hace UNA
cosa: guardar SIEMPRE la fila de auditoría en `comprobantes_pago` y, si se puede, ASOCIARLA al
cobro `pendiente` de pedido del mismo cliente. El comprobante **JAMÁS marca un cobro como pagado**
—una captura es falsificable—: solo asocia. El `pagado` lo pone el conciliador cuando llega la
transferencia real (o el cierre manual). El comprobante asociado sirve de DESEMPATE cuando varios
cobros comparten monto (ver `conciliar_transferencia`).

El texto de `mensaje_cliente` viene listo para mandárselo al cliente por su canal (el frente A del
canal Telegram lo consume tal cual). La sesión es la del tenant (frontera de aislamiento).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ai.vision.recibo import ReciboExtraido
from core.config.timezone import now_co
from core.logging import get_logger
from modules.pagos.conciliador_transferencias import VENTANA
from modules.pagos.models import Cobro, Comprobante
from modules.pagos.repository import SqlPagosRepository

log = get_logger("pagos.comprobantes")

# Debajo de este umbral la lectura de Visión no es confiable → el comprobante entra ilegible.
UMBRAL_LEGIBLE = Decimal("0.5")


@dataclass(frozen=True)
class ResultadoComprobante:
    estado: str            # "asociado" | "sin_match" | "ambiguo" | "ilegible"
    cobro: Cobro | None
    mensaje_cliente: str   # texto listo para mandarle al cliente por su canal


def _pesos(monto: Decimal) -> str:
    return f"${monto:,.0f}"


async def registrar_comprobante(
    session: AsyncSession,
    *,
    cliente_telefono: str,
    datos: ReciboExtraido,
    imagen_ref: str | None = None,
) -> ResultadoComprobante:
    """Guarda el comprobante (auditoría) y lo asocia al cobro pendiente del cliente si aplica.

    Nunca marca pagado. Devuelve el estado del matching y el mensaje listo para el cliente. La fila
    en `comprobantes_pago` se inserta SIEMPRE (con `cobro_id` del cobro asociado, o NULL)."""
    repo = SqlPagosRepository(session)

    estado, cobro, mensaje = await _resolver(repo, cliente_telefono, datos)

    await repo.crear_comprobante(Comprobante(
        cliente_telefono=cliente_telefono,
        cobro_id=cobro.id if cobro is not None else None,
        monto=datos.valor,
        fecha=datos.fecha,
        referencia=datos.referencia,
        origen=datos.entidad_o_producto_origen,
        destino=datos.destino,
        banco_tipo=datos.tipo_transaccion,
        confianza=datos.confianza,
        imagen_ref=imagen_ref,
    ))
    log.info("comprobante_registrado", cliente=cliente_telefono, estado=estado,
             cobro_id=cobro.id if cobro is not None else None,
             valor=str(datos.valor) if datos.valor is not None else None)
    return ResultadoComprobante(estado=estado, cobro=cobro, mensaje_cliente=mensaje)


async def _resolver(
    repo: SqlPagosRepository, cliente_telefono: str, datos: ReciboExtraido
) -> tuple[str, Cobro | None, str]:
    """Decide estado/cobro/mensaje. NO persiste (el caller inserta la fila con este resultado)."""
    if datos.valor is None or datos.confianza < UMBRAL_LEGIBLE:
        return ("ilegible", None,
                "Recibí tu comprobante 🙏 pero no pude leerlo bien; apenas confirmemos el pago te "
                "aviso.")

    desde = now_co() - VENTANA
    cobros = await repo.cobros_pedido_pendientes_de_cliente(cliente_telefono, desde=desde)

    if not cobros:
        return ("sin_match", None,
                "Recibí tu comprobante 🙏 pero no encuentro un pedido pendiente tuyo; ¿ya "
                "confirmaste tu pedido?")

    coincidencias = [c for c in cobros if c.monto == datos.valor]

    if len(coincidencias) == 1:
        cobro = coincidencias[0]
        return ("asociado", cobro,
                f"¡Comprobante recibido! 🧾 Estamos confirmando tu pago de {_pesos(datos.valor)} "
                "con el banco; en cuanto entre te aviso y tu pedido pasa a cocina.")

    # Un solo pedido pendiente pero el monto no calza: se asocia igual y se aclara el valor.
    if len(cobros) == 1 and not coincidencias:
        cobro = cobros[0]
        return ("asociado", cobro,
                f"Recibí tu comprobante 🧾 pero veo {_pesos(datos.valor)} y tu pedido es "
                f"{_pesos(cobro.monto)}; si transferiste otro valor escríbenos.")

    # ≥2 pedidos: si el monto calza con exactamente uno, ese; si no (0 o varios) → ambiguo.
    return ("ambiguo", None,
            "Recibí tu comprobante 🙏 pero tienes varios pedidos pendientes; respóndeme el número "
            "de tu pedido para confirmarlo.")


if __name__ == "__main__":   # pragma: no cover — self-check de la lógica de matching (sin BD)
    def _r(valor, conf, montos_cliente):
        d = ReciboExtraido(valor=valor, confianza=conf)
        cobros = [Cobro(id=i, origen="pedido", estado="pendiente", monto=Decimal(m), referencia=f"r{i}")
                  for i, m in enumerate(montos_cliente, 1)]
        # emula _resolver sin repo
        if d.valor is None or d.confianza < UMBRAL_LEGIBLE:
            return "ilegible"
        if not cobros:
            return "sin_match"
        coin = [c for c in cobros if c.monto == d.valor]
        if len(coin) == 1:
            return "asociado"
        if len(cobros) == 1 and not coin:
            return "asociado"
        return "ambiguo"

    assert _r(Decimal("25000"), Decimal("0.9"), ["25000"]) == "asociado"
    assert _r(None, Decimal("0.9"), ["25000"]) == "ilegible"
    assert _r(Decimal("25000"), Decimal("0.4"), ["25000"]) == "ilegible"
    assert _r(Decimal("25000"), Decimal("0.9"), []) == "sin_match"
    assert _r(Decimal("18000"), Decimal("0.9"), ["25000"]) == "asociado"   # 1 pedido, monto distinto
    assert _r(Decimal("25000"), Decimal("0.9"), ["25000", "18000"]) == "asociado"  # discrimina
    assert _r(Decimal("99000"), Decimal("0.9"), ["25000", "18000"]) == "ambiguo"   # ninguno
    assert _r(Decimal("25000"), Decimal("0.9"), ["25000", "25000"]) == "ambiguo"   # varios
    print("ok")
