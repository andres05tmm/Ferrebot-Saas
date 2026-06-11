"""Herramientas de agente del pack Cobranza (ADR 0015), de cara al DEUDOR en WhatsApp.

Capa fina sobre el motor (`modules/cobranza/service.py`): cada herramienta traduce los args del
modelo a una llamada al servicio y normaliza la salida al envelope común. NO reimplementa lógica de
cobranza (saldos, promesas, topes) — eso vive en el motor.

GUARDARRAÍL DE SEGURIDAD (no negociable): el **tenant** y el **teléfono del cliente** viajan en el
`Contexto` que inyecta el adaptador de canal (el número que escribe), **nunca** como args del modelo.
`mi_saldo`/`prometer_pago`/`reportar_pago`/`no_mas_recordatorios` operan SOLO sobre el cliente de ese
teléfono; el modelo no puede consultar ni tocar deudas ajenas. El agente jamás calcula el saldo ni
registra abonos (los abonos son del POS, con su movimiento de caja). Habeas Data: el agente solo
conoce nombre + teléfono + saldo del que escribe. Se exponen solo con el flag `pack_cobranza`.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from core.config.timezone import now_co
from core.llm.base import ToolCall, ToolSpec
from modules.cobranza.errors import ClienteNoIdentificado, FechaPromesaInvalida, SinDeuda
from modules.cobranza.service import CobranzaService
from modules.conversaciones.service import ConversacionService


@dataclass(frozen=True, slots=True)
class CobranzaDeps:
    """Dependencias del turno: el motor de cobranza + conversaciones (bandeja humana), por sesión del tenant."""

    cobranza: CobranzaService
    conversaciones: ConversacionService


# --- helpers ----------------------------------------------------------------
def _pesos(monto: Decimal) -> str:
    """Monto legible en pesos colombianos: $1.234.567 (separador de miles con punto)."""
    return "$" + f"{monto:,.0f}".replace(",", ".")


def _telefono(ctx: Contexto) -> str | None:
    """Teléfono del cliente del contexto del canal. None → el runtime no lo inyectó (falla cerrado)."""
    return ctx.cliente_telefono or None


_SIN_TELEFONO = ErrorTool(
    "contexto_invalido", "Falta el teléfono del cliente en el contexto del canal."
)

_NO_IDENTIFICADO = ErrorTool(
    "cliente_no_identificado",
    "Este número no está asociado a un cliente del negocio. Ofrece pasar con un asesor humano "
    "(escalar_humano) para revisar su caso; NO des información de saldos.",
    recuperable=False,
)


# --- args (lo único que provee el modelo; el teléfono NUNCA va aquí) ---------
class MiSaldoArgs(BaseModel):
    """Sin parámetros: SIEMPRE el saldo del teléfono del contexto."""


class PrometerPagoArgs(BaseModel):
    fecha: date                              # cuándo promete pagar (la dice el cliente)


class ReportarPagoArgs(BaseModel):
    detalle: str = Field(default="", max_length=500)  # cómo/cuándo pagó, según el cliente


class NoMasRecordatoriosArgs(BaseModel):
    """Sin parámetros: opt-out del teléfono del contexto (Habeas Data)."""


# --- handlers ---------------------------------------------------------------
async def _mi_saldo(args: MiSaldoArgs, ctx: Contexto, deps: CobranzaDeps) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        info = await deps.cobranza.saldo_de(telefono)
    except ClienteNoIdentificado:
        return _NO_IDENTIFICADO
    if info.saldo <= 0:
        return Resultado(
            data={"saldo": "0", "promesa_fecha": None},
            resumen=f"{info.nombre} está al día: no tiene saldo pendiente. 🎉",
        )
    promesa = f" Tiene promesa de pago para el {info.promesa_fecha}." if info.promesa_fecha else ""
    return Resultado(
        data={"saldo": str(info.saldo), "promesa_fecha": str(info.promesa_fecha or "")},
        resumen=f"Saldo pendiente de {info.nombre}: {_pesos(info.saldo)}.{promesa}",
    )


async def _prometer_pago(
    args: PrometerPagoArgs, ctx: Contexto, deps: CobranzaDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        promesa = await deps.cobranza.prometer_pago(telefono, args.fecha, hoy=now_co().date())
    except ClienteNoIdentificado:
        return _NO_IDENTIFICADO
    except SinDeuda:
        return Resultado(
            data={"saldo": "0"}, resumen="No tiene saldo pendiente: no hay nada que prometer. 🎉"
        )
    except FechaPromesaInvalida as exc:
        return ErrorTool("fecha_invalida", str(exc), recuperable=True)
    return Resultado(
        data={"promesa_id": promesa.id, "fecha": str(promesa.fecha_promesa)},
        resumen=(
            f"Promesa de pago registrada para el {promesa.fecha_promesa} ✅. "
            "Agradécele y dile que no le enviaremos más recordatorios hasta esa fecha."
        ),
        evento="promesa_registrada",
        idempotente="aplicada",
    )


async def _reportar_pago(
    args: ReportarPagoArgs, ctx: Contexto, deps: CobranzaDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        pago = await deps.cobranza.reportar_pago(telefono, nota=args.detalle or None)
    except ClienteNoIdentificado:
        return _NO_IDENTIFICADO
    # El reporte pasa a la bandeja humana: una persona verifica el comprobante y registra el abono
    # en el POS (el agente JAMÁS registra abonos ni cambia saldos).
    await deps.conversaciones.escalar(telefono, motivo="Cliente reporta un pago de su deuda")
    return Resultado(
        data={"pago_reportado_id": pago.id},
        resumen=(
            "Reporte de pago registrado ✅. Pídele que envíe el comprobante por este chat; "
            "un asesor lo verificará y actualizará su cuenta muy pronto."
        ),
        evento="pago_reportado",
        idempotente="aplicada",
    )


async def _no_mas_recordatorios(
    args: NoMasRecordatoriosArgs, ctx: Contexto, deps: CobranzaDeps
) -> Resultado | ErrorTool:
    telefono = _telefono(ctx)
    if telefono is None:
        return _SIN_TELEFONO
    try:
        await deps.cobranza.optar_fuera(telefono)
    except ClienteNoIdentificado:
        return _NO_IDENTIFICADO
    return Resultado(
        data={"opt_out": True},
        resumen=(
            "Listo: no le enviaremos más recordatorios por WhatsApp ✅. "
            "Acláralo con respeto y, si tiene saldo, recuérdale que puede pagar cuando guste."
        ),
        evento="cobranza_opt_out",
        idempotente="aplicada",
    )


# --- catálogo ---------------------------------------------------------------
Handler = Callable[[BaseModel, Contexto, CobranzaDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class CobranzaTool:
    """Herramienta del pack: lo que ve el modelo (spec) + su handler. Gated por `feature`."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    feature: str = "pack_cobranza"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_COBRANZA: tuple[CobranzaTool, ...] = (
    CobranzaTool(
        nombre="mi_saldo",
        descripcion=(
            "Consulta el saldo pendiente del cliente que escribe (y su promesa de pago vigente, si "
            "tiene). Úsala SIEMPRE que pregunte cuánto debe; nunca calcules ni inventes saldos. Solo lectura."
        ),
        args_model=MiSaldoArgs, handler=_mi_saldo,
    ),
    CobranzaTool(
        nombre="prometer_pago",
        descripcion=(
            "Registra la promesa de pago del cliente que escribe para una fecha futura (máximo 30 "
            "días). Mientras la promesa esté vigente no se le envían recordatorios."
        ),
        args_model=PrometerPagoArgs, handler=_prometer_pago,
    ),
    CobranzaTool(
        nombre="reportar_pago",
        descripcion=(
            "Registra que el cliente dice que YA pagó (con el detalle que dé) y pasa el caso a un "
            "asesor humano para verificar el comprobante. NUNCA confirmes tú que el pago quedó "
            "aplicado: solo el asesor lo verifica."
        ),
        args_model=ReportarPagoArgs, handler=_reportar_pago,
    ),
    CobranzaTool(
        nombre="no_mas_recordatorios",
        descripcion=(
            "El cliente pide que NO le enviemos más recordatorios de pago por WhatsApp (opt-out). "
            "Respeta su decisión de inmediato; su deuda no cambia."
        ),
        args_model=NoMasRecordatoriosArgs, handler=_no_mas_recordatorios,
    ),
)

POR_NOMBRE: dict[str, CobranzaTool] = {t.nombre: t for t in CATALOGO_COBRANZA}


def catalogo_visible(ctx: Contexto) -> list[CobranzaTool]:
    """Herramientas del pack visibles para la empresa (solo si tiene el flag `pack_cobranza`)."""
    return [t for t in CATALOGO_COBRANZA if ctx.tiene_capacidad(t.feature)]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo (filtradas por capacidad), listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(tool_call: ToolCall, ctx: Contexto, deps: CobranzaDeps) -> Resultado | ErrorTool:
    """Frontera de ejecución: valida los args del modelo (Pydantic) y corre el handler.

    Cualquier `cliente_telefono` que el modelo intente colar se ignora (no está en ningún args_model) —
    la identidad sale SIEMPRE del `Contexto`.
    """
    tool = POR_NOMBRE.get(tool_call.name)
    if tool is None:
        return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")
    try:
        args = tool.args_model(**tool_call.arguments)
    except ValidationError as exc:
        return ErrorTool("validacion", str(exc), recuperable=True)
    return await tool.handler(args, ctx, deps)
