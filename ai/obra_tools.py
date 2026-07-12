"""Herramientas de agente del Bot PIM (Fase 6, spec 14): partes de campo + gasto por foto de recibo.

Capa fina sobre los servicios de dominio del vertical construcción (`modules/maquinaria`,
`modules/obra`, `modules/caja`): cada herramienta traduce los args del modelo a una llamada al MISMO
servicio que usa la API REST y normaliza la salida al envelope común (`ai/envelope.py`). NO
reimplementa lógica (mínimo facturable, idempotencia de horas, movimiento de caja): eso vive en los
servicios. Espeja el patrón de `ai/cobranza_tools.py` y `ai/pedidos_tools.py` (pack autocontenido),
sumando el gateo por `rol_min` del bot interno (como `ai/tools.py`).

GUARDARRAÍL DE SEGURIDAD (no negociable): la identidad NUNCA llega por los args del modelo.
  - El **tenant** viaja en el `Contexto` (lo resuelve el canal por el `telegramUserId` vinculado del
    `Usuario`; ver spec 14 §Authorization) y la sesión ya apunta a la base de esa empresa.
  - La identidad de Telegram (`telegram_user_id`/`telegram_message_id`) y la **imagen** del recibo
    (binaria) viajan en el `ContextoTelegram` que inyecta el adaptador de canal, no en el `Contexto`
    compartido (ese es de `ai/envelope.py`, ajeno a este pack). El modelo solo decide QUÉ registrar
    (máquina/obra/horas/avance/categoría); jamás el tenant, el usuario ni la imagen.
  - Los `args_model` usan `extra="forbid"`: un intento del modelo de colar `tenant_id`,
    `telegram_user_id`, `usuario_id`, etc. se rechaza como `validacion` (no se ignora en silencio).

VISIÓN (cierra el cleanup pendiente de `ai/vision/recibo.py`): `registrar_gasto_recibo` resuelve
(proveedor + modelo) por el factory de `core/llm` —inyectado como `resolver_vision`— y lo pasa a
`extraer_recibo(image, provider, modelo=...)`, en lugar del modelo placeholder por defecto de esa
capa. Los recibos de baja confianza (`ReciboExtraido.requiere_revision`, confianza < 0.7) entran con
`requiere_revision=True` a la bandeja de revisión de Fase 3.

IDEMPOTENCIA (invariante del carve-out): `registrar_horas_maquina` es idempotente por la CLAVE
NATURAL (máquina, obra, fecha) del servicio —un parte por máquina por día—: un reintento del bot
devuelve `replay=True` y NO crea un segundo parte, así el cargo a cartera de Fase 5 se asienta una
sola vez. `registrar_gasto_recibo` ancla su idempotencia en el `telegram_message_id` (una foto = un
mensaje = un gasto): un reintento del mismo mensaje devuelve el gasto previo sin duplicar el egreso.
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.vision.recibo import ReciboExtraido, extraer_recibo
from core.auth.rbac import satisface
from core.config.timezone import today_co
from core.llm.base import ImageBlock, ToolCall, ToolSpec
from core.llm.factory import LLMResuelto
from modules.caja.errors import CajaNoAbierta, ObraNoImputable
from modules.caja.schemas import CategoriaGastoVertical
from modules.caja.service import CajaService
from modules.maquinaria.errors import MaquinaInexistente, ObraNoAsignable, SinAsignacionActiva
from modules.maquinaria.models import Maquina
from modules.maquinaria.schemas import RegistroHorasCrear
from modules.maquinaria.service import MaquinariaService
from modules.obra.errors import ObraInexistente
from modules.obra.models import Obra
from modules.obra.schemas import ReporteDiarioCrear
from modules.obra.service import ObrasService

# Categoría POS del gasto del bot. La columna POS `categoria` es un ENUM FIJO NOT NULL
# (`gasto_categoria`: transporte/papeleria/servicios/nomina/mantenimiento/otros) y la del vertical
# (`categoria_gasto`, enum `CategoriaGastoVertical`) es OTRA taxonomía. Un recibo del vertical
# construcción se clasifica por la taxonomía VERTICAL + la bandeja de revisión; la POS —que no aplica al
# gremio— cae a "otros" (valor VÁLIDO del enum). Antes se ponía texto libre ("por_clasificar"/la del
# usuario) que el enum de Postgres rechaza: el smoke de integración contra la BD real lo destapó.
_CATEGORIA_POS_BOT = "otros"

# Valores válidos de la taxonomía VERTICAL (enum `categoria_gasto`). El modelo manda texto libre: se
# normaliza a mayúsculas y, si no cae en el enum, se deja en None para que el humano lo clasifique en la
# bandeja de revisión (nunca se cuela un valor inválido al enum de la BD).
_CATEGORIAS_VERTICAL: frozenset[str] = frozenset(get_args(CategoriaGastoVertical))


def _categoria_vertical(valor: str | None) -> str | None:
    """Normaliza la categoría del vertical del modelo a su enum (`COMBUSTIBLE`…), o None si no se reconoce.

    Defensa contra el enum de Postgres: el modelo puede decir «combustible», «Repuestos» o algo que no
    existe. Mayúsculas + verificación de pertenencia; lo no reconocido queda None (lo fija el humano)."""
    if not valor:
        return None
    candidato = valor.strip().upper()
    return candidato if candidato in _CATEGORIAS_VERTICAL else None


# Tipos de transacción de un comprobante Bancolombia (transferencia, pago, QR, PSE, consignación, envío a
# Nequi…): el dinero SIEMPRE sale de una cuenta Bancolombia → método de pago `TRANSFERENCIA_BANCOLOMBIA`
# (valor VÁLIDO del enum `metodo_pago_gasto`). El campo `tipo_transaccion` del recibo es texto libre del
# modelo y NO es un método de pago del enum: mapearlo directo reventaba el INSERT (lo destapó el smoke).
def _metodo_pago(tipo_transaccion: str | None) -> str | None:
    """Método de pago del enum para un gasto capturado por foto: siempre una transferencia Bancolombia
    (es un comprobante de Bancolombia). Sin tipo legible → None (lo fija el humano en revisión)."""
    if not tipo_transaccion or not tipo_transaccion.strip():
        return None
    return "TRANSFERENCIA_BANCOLOMBIA"

# Tope de cordura de horas de un parte diario: una máquina no trabaja más de 24 h en un día. Defensa
# adicional al saneamiento del despachador; el mínimo facturable lo aplica el servicio, no este tope.
_MAX_HORAS_DIA = Decimal("24")

# Resolutor de (proveedor + modelo) con VISIÓN para el tenant (lo cablea el composition root al factory
# de `core/llm`). Recibe el `tenant_id` del `Contexto`; devuelve el proveedor instanciado + su modelo.
# Debe resolver un modelo con visión (Turno.ORQUESTADOR o el modelo de visión configurado del tenant).
ResolverVision = Callable[[int], Awaitable[LLMResuelto]]


@dataclass(frozen=True, slots=True)
class ContextoTelegram:
    """Identidad y adjuntos del canal Telegram para ESTE turno, inyectados por el adaptador de canal.

    Cumple el mismo rol que el `Contexto` de `ai/envelope.py` para lo específico de Telegram/binario
    que no cabe en ese envelope compartido (ese archivo es de otra frontera). El modelo NUNCA fija
    estos campos (no están en ningún `args_model`): la imagen es binaria y la identidad es del canal.

    - `imagen`: foto del recibo Bancolombia ya descargada a `ImageBlock` (base64 o URL) — la construye
      el adaptador desde lo que entrega Telegram.
    - `telegram_user_id` / `telegram_message_id`: identidad del remitente y del mensaje (traza + ancla
      de idempotencia del gasto).
    - `comprobante_url`: URL del recibo ya subido al bucket externo (spec 14 §Image storage); se guarda
      en el gasto. Puede coincidir con `imagen.url` si la visión lee desde esa misma URL.
    """

    imagen: ImageBlock | None = None
    telegram_user_id: str | None = None
    telegram_message_id: str | None = None
    comprobante_url: str | None = None


@dataclass(frozen=True, slots=True)
class ObraDeps:
    """Dependencias del turno del Bot PIM (servicios atados a la sesión del tenant + payload del canal).

    Se construye FRESCO por turno en el composition root (como `RuntimeDeps` en `apps/wa/agent.py`):
    los servicios cuelgan de la sesión de ESA empresa; `resolver_vision` del factory de `core/llm`; y
    `canal` trae la identidad/imagen de Telegram de este mensaje. `canal` por defecto vacío: las tools
    que necesitan imagen fallan-cerradas con un error recuperable si el canal no la inyectó.
    """

    maquinaria: MaquinariaService
    obras: ObrasService
    caja: CajaService
    resolver_vision: ResolverVision
    canal: ContextoTelegram = ContextoTelegram()


# --- helpers ----------------------------------------------------------------
def _pesos(monto) -> str:
    """Monto legible en pesos colombianos: $1.234.567 (separador de miles con punto)."""
    return "$" + f"{Decimal(monto):,.0f}".replace(",", ".")


def _num(valor: Decimal) -> str:
    """Cantidad legible sin ceros de más (6 → '6', 6.5 → '6.5')."""
    return f"{valor:g}"


def _elegir(
    codigo_error: str, etiqueta: str, ref: str, matches: list, nombre_de
) -> object | ErrorTool | Resultado:
    """Resuelve una referencia difusa a UNA entidad: 0 → error recuperable; >1 → pregunta; 1 → la entidad.

    Espeja `_consultar_producto` de `ai/tools.py` (0/1/N candidatos): no adivina cuando hay ambigüedad
    —imputar horas/gastos a la máquina u obra equivocada es un error de plata—, sino que devuelve los
    candidatos con su id para que el modelo re-pregunte."""
    if not matches:
        return ErrorTool(
            codigo_error, f"No encontré ninguna {etiqueta} para «{ref}».", recuperable=True
        )
    if len(matches) > 1:
        lista = ", ".join(f"{nombre_de(m)} (id {m.id})" for m in matches)
        return Resultado(
            data={"candidatos": [{"id": m.id, "nombre": nombre_de(m)} for m in matches]},
            resumen=f"Hay varias coincidencias de {etiqueta} para «{ref}»: {lista}. ¿Cuál es?",
        )
    return matches[0]


async def _resolver_maquina(deps: ObraDeps, ref: str) -> Maquina | ErrorTool | Resultado:
    """Máquina por id (si `ref` es numérico) o por búsqueda de nombre/código (`listar(q=…)`)."""
    ref = ref.strip()
    if ref.isdigit():
        try:
            return await deps.maquinaria.obtener(int(ref))
        except MaquinaInexistente:
            pass  # no era un id válido → cae a búsqueda por texto
    matches = await deps.maquinaria.listar(q=ref)
    return _elegir("maquina_no_encontrada", "máquina", ref, matches, lambda m: m.nombre)


async def _resolver_obra(deps: ObraDeps, ref: str) -> Obra | ErrorTool | Resultado:
    """Obra por id (si `ref` es numérico) o por coincidencia de nombre (filtro en memoria: pocas obras)."""
    ref = ref.strip()
    if ref.isdigit():
        try:
            return await deps.obras.obtener(int(ref))
        except ObraInexistente:
            pass
    aguja = ref.lower()
    matches = [o for o in await deps.obras.listar() if aguja in o.nombre.lower()]
    return _elegir("obra_no_encontrada", "obra", ref, matches, lambda o: o.nombre)


def _concepto_recibo(recibo: ReciboExtraido) -> str:
    """Concepto legible del gasto a partir de lo extraído (destino/descripción/referencia)."""
    partes: list[str] = []
    if recibo.destino:
        partes.append(f"a {recibo.destino}")
    if recibo.descripcion:
        partes.append(recibo.descripcion)
    base = "Pago Bancolombia" + (" " + " · ".join(partes) if partes else "")
    if recibo.referencia:
        base += f" (ref {recibo.referencia})"
    return base[:300]


def _key_recibo(canal: ContextoTelegram) -> str | None:
    """Ancla de idempotencia del gasto: una foto = un mensaje de Telegram = un gasto.

    Sin `telegram_message_id` no hay ancla (None → cada llamada crea un gasto): el adaptador de canal
    DEBE inyectarlo para que un reintento del mismo mensaje no duplique el egreso.
    """
    if canal.telegram_message_id:
        return f"telegram:gasto:{canal.telegram_message_id}"
    return None


# --- args (lo ÚNICO que provee el modelo; identidad e imagen NUNCA van aquí) --
class _ArgsObra(BaseModel):
    """Base estricta: rechaza campos no declarados (`extra="forbid"` → `additionalProperties:false`).

    Es el guardarraíl duro: si el modelo intenta colar `tenant_id`/`telegram_user_id`/`usuario_id`, la
    validación lo corta como `validacion` en vez de dejarlo pasar."""

    model_config = ConfigDict(extra="forbid")


class RegistrarHorasMaquinaArgs(_ArgsObra):
    # `maquina`/`obra`: como los nombra el operador (nombre, código o id). Los resuelve el pack, no el
    # modelo. `horas`: horas trabajadas HOY (la fecha la fija el pack = hoy Colombia; ancla la idempotencia).
    maquina: str = Field(min_length=1, max_length=120)
    obra: str = Field(min_length=1, max_length=120)
    horas: Decimal = Field(ge=0, le=_MAX_HORAS_DIA)
    observaciones: str | None = Field(default=None, max_length=500)


class ReporteDiarioObraArgs(_ArgsObra):
    obra: str = Field(min_length=1, max_length=120)
    avance: str = Field(min_length=1, max_length=2000)      # avance_descripcion del día
    m2: Decimal | None = Field(default=None, ge=0)          # m² ejecutados hoy (opcional)
    m3: Decimal | None = Field(default=None, ge=0)          # m³ ejecutados hoy (opcional)
    incidentes: str | None = Field(default=None, max_length=2000)


class RegistrarGastoReciboArgs(_ArgsObra):
    # La imagen NO es un arg: llega por el canal (`ObraDeps.canal.imagen`). El modelo solo aporta la
    # clasificación opcional que el usuario elija (spec 14 §Register expense, paso 6).
    categoria_gasto: str | None = Field(default=None, max_length=80)   # categoría del vertical
    obra: str | None = Field(default=None, max_length=120)             # imputar a una obra (opcional)
    concepto: str | None = Field(default=None, max_length=300)


# --- handlers ---------------------------------------------------------------
async def _registrar_horas_maquina(
    args: RegistrarHorasMaquinaArgs, ctx: Contexto, deps: ObraDeps
) -> Resultado | ErrorTool:
    maquina = await _resolver_maquina(deps, args.maquina)
    if isinstance(maquina, (ErrorTool, Resultado)):
        return maquina
    obra = await _resolver_obra(deps, args.obra)
    if isinstance(obra, (ErrorTool, Resultado)):
        return obra

    datos = RegistroHorasCrear(
        obra_id=obra.id,
        fecha=today_co(),                       # el parte es de HOY (ancla de la clave natural)
        horas_trabajadas=args.horas,
        operador_id=ctx.usuario_id or None,     # el operador es el Usuario vinculado del bot
        observaciones=args.observaciones,
        origen_registro="TELEGRAM_BOT",
        idempotency_key=ctx.idempotency_key,    # contrato del bot; la idempotencia real es por clave natural
    )
    try:
        res = await deps.maquinaria.registrar_horas(maquina.id, datos)
    except MaquinaInexistente as exc:
        return ErrorTool("maquina_no_encontrada", str(exc), recuperable=True)
    except ObraNoAsignable as exc:
        # Obra liquidada (snapshot congelado) o borrada: el parte no procede.
        return ErrorTool("obra_no_asignable", str(exc), recuperable=True)
    except SinAsignacionActiva as exc:
        # Sin asignación activa no hay precio ni mínimo pactados: no se puede facturar la hora.
        return ErrorTool("sin_asignacion", str(exc), recuperable=True)

    minimo_txt = "cubierto" if res.minimo_cubierto else "NO cubierto"
    verbo = "Ese parte ya estaba registrado" if res.replay else "Registré el parte"
    resumen = (
        f"{verbo}: {_num(res.horas_trabajadas)}h en «{maquina.nombre}» "
        f"({_num(res.horas_facturables)}h facturables, mínimo {minimo_txt}). "
        f"Ingreso del día: {_pesos(res.ingreso)}."
    )
    return Resultado(
        data={
            "registro_id": res.registro_id,
            "maquina_id": res.maquina_id,
            "obra_id": res.obra_id,
            "fecha": str(res.fecha),
            "horas_trabajadas": str(res.horas_trabajadas),
            "horas_facturables": str(res.horas_facturables),
            "minimo_cubierto": res.minimo_cubierto,
            "precio_hora": str(res.precio_hora),
            "ingreso": str(res.ingreso),
        },
        resumen=resumen,
        evento="horas_registradas",
        idempotente="duplicada" if res.replay else "aplicada",
    )


async def _reporte_diario_obra(
    args: ReporteDiarioObraArgs, ctx: Contexto, deps: ObraDeps
) -> Resultado | ErrorTool:
    obra = await _resolver_obra(deps, args.obra)
    if isinstance(obra, (ErrorTool, Resultado)):
        return obra

    datos = ReporteDiarioCrear(
        fecha=None,                                     # el servicio lo fija a hoy Colombia
        telegram_user_id=deps.canal.telegram_user_id,  # identidad del canal, NUNCA del modelo
        avance_descripcion=args.avance,
        m2_ejecutados=args.m2,
        m3_ejecutados=args.m3,
        incidentes=args.incidentes,
        origen_registro="TELEGRAM_BOT",
    )
    try:
        reporte = await deps.obras.crear_reporte(obra.id, datos)
    except ObraInexistente as exc:
        return ErrorTool("obra_no_encontrada", str(exc), recuperable=True)

    medidas: list[str] = []
    if reporte.m2_ejecutados is not None:
        medidas.append(f"{_num(reporte.m2_ejecutados)} m²")
    if reporte.m3_ejecutados is not None:
        medidas.append(f"{_num(reporte.m3_ejecutados)} m³")
    detalle = f" ({', '.join(medidas)})" if medidas else ""
    return Resultado(
        data={
            "reporte_id": reporte.id,
            "obra_id": reporte.obra_id,
            "fecha": str(reporte.fecha),
            "m2_ejecutados": str(reporte.m2_ejecutados) if reporte.m2_ejecutados is not None else None,
            "m3_ejecutados": str(reporte.m3_ejecutados) if reporte.m3_ejecutados is not None else None,
        },
        resumen=f"Reporte diario de «{obra.nombre}» guardado{detalle}. ✅",
        evento="reporte_diario_creado",
        idempotente="aplicada",
    )


async def _registrar_gasto_recibo(
    args: RegistrarGastoReciboArgs, ctx: Contexto, deps: ObraDeps
) -> Resultado | ErrorTool:
    imagen = deps.canal.imagen
    if imagen is None:
        # El modelo no puede aportar la imagen (es binaria, del canal): si no llegó, se pide.
        return ErrorTool(
            "sin_imagen",
            "No recibí la foto del comprobante. Pídele al usuario que la envíe por Telegram.",
            recuperable=True,
        )

    obra_id: int | None = None
    if args.obra:
        obra = await _resolver_obra(deps, args.obra)
        if isinstance(obra, (ErrorTool, Resultado)):
            return obra
        obra_id = obra.id

    # Visión: (proveedor + modelo) del factory → extraer_recibo. Cierra el cleanup del default
    # placeholder de `ai/vision/recibo.py`: el modelo lo resuelve el factory por tenant, no la capa.
    llm = await deps.resolver_vision(ctx.tenant_id)
    recibo = await extraer_recibo(imagen, llm.provider, modelo=llm.model)
    if recibo.valor is None:
        # Sin monto legible no se crea un gasto fantasma en $0: se pide una foto mejor.
        return ErrorTool(
            "recibo_ilegible",
            "No pude leer el monto del comprobante. Reenvía una foto más nítida o dime el monto.",
            recuperable=True,
        )

    # Dos taxonomías distintas, cada una su enum: la POS cae a un valor fijo válido ("otros"); la del
    # vertical se normaliza al enum o queda None (la fija el humano en revisión). Nunca texto libre.
    categoria_vertical = _categoria_vertical(args.categoria_gasto)
    try:
        res = await deps.caja.registrar_gasto(
            usuario_id=ctx.usuario_id,
            categoria=_CATEGORIA_POS_BOT,
            monto=recibo.valor,
            concepto=args.concepto or _concepto_recibo(recibo),
            idempotency_key=_key_recibo(deps.canal),
            obra_id=obra_id,
            categoria_gasto=categoria_vertical,
            metodo_pago=_metodo_pago(recibo.tipo_transaccion),
            numero_referencia=recibo.referencia,
            comprobante_url=deps.canal.comprobante_url,
            origen_registro="TELEGRAM_BOT",
            telegram_user_id=deps.canal.telegram_user_id,
            telegram_message_id=deps.canal.telegram_message_id,
            requiere_revision=recibo.requiere_revision,   # confianza < 0.7 → bandeja de revisión
        )
    except CajaNoAbierta as exc:
        return ErrorTool("caja_cerrada", str(exc), recuperable=True)
    except ObraNoImputable as exc:
        # Obra liquidada (snapshot congelado) o borrada entre la resolución y el insert.
        return ErrorTool("obra_no_imputable", str(exc), recuperable=True)

    g = res.gasto
    if recibo.requiere_revision:
        resumen = (
            f"Guardé un gasto de {_pesos(recibo.valor)} del comprobante, pero la lectura quedó con "
            "BAJA confianza: lo dejé en la bandeja de REVISIÓN para que alguien lo confirme. 🔎"
        )
    else:
        resumen = f"Gasto de {_pesos(recibo.valor)} registrado desde el comprobante. ✅"
    return Resultado(
        data={
            "gasto_id": g.id,
            "monto": str(recibo.valor),
            "fecha": str(recibo.fecha) if recibo.fecha else None,
            "referencia": recibo.referencia,
            "confianza": str(recibo.confianza),
            "requiere_revision": recibo.requiere_revision,
            "obra_id": obra_id,
        },
        resumen=resumen,
        evento="gasto_registrado",
        idempotente="duplicada" if res.replay else "aplicada",
    )


# --- catálogo ---------------------------------------------------------------
Handler = Callable[[BaseModel, Contexto, ObraDeps], Awaitable[Resultado | ErrorTool]]


@dataclass(frozen=True, slots=True)
class ObraTool:
    """Herramienta del pack PIM: lo que ve el modelo (spec) + su handler + su gateo (rol_min + feature).

    A diferencia de los packs de cara al cliente (cobranza/pedidos), estas son del bot INTERNO de
    operación: llevan `rol_min` (RBAC) como las de `ai/tools.py`, además del `feature` (capacidad)."""

    nombre: str
    descripcion: str
    args_model: type[BaseModel]
    handler: Handler
    rol_min: str
    feature: str

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.nombre,
            description=self.descripcion,
            parameters=self.args_model.model_json_schema(),
        )


CATALOGO_OBRA: tuple[ObraTool, ...] = (
    ObraTool(
        nombre="registrar_horas_maquina",
        descripcion=(
            "Registra las horas que una máquina trabajó HOY en una obra. Aplica el mínimo facturable "
            "pactado y calcula el ingreso del día. Es idempotente: un parte por máquina por día (si ya "
            "existe, lo devuelve sin duplicar). Indica la máquina (nombre/código) y la obra."
        ),
        args_model=RegistrarHorasMaquinaArgs, handler=_registrar_horas_maquina,
        rol_min="vendedor", feature="maquinaria",
    ),
    ObraTool(
        nombre="reporte_diario_obra",
        descripcion=(
            "Crea el reporte diario de avance de una obra: descripción del avance y, opcional, m² o m³ "
            "ejecutados hoy e incidentes. Indica la obra por su nombre."
        ),
        args_model=ReporteDiarioObraArgs, handler=_reporte_diario_obra,
        rol_min="vendedor", feature="obras",
    ),
    ObraTool(
        nombre="registrar_gasto_recibo",
        descripcion=(
            "Registra un gasto a partir de la FOTO de un comprobante de Bancolombia que el usuario "
            "envió por Telegram (la imagen ya viaja por el canal; NO la pidas como dato). Extrae el "
            "monto y la referencia con visión y crea el gasto; si la lectura es dudosa, lo deja en "
            "revisión. Puedes pasar la categoría y la obra a la que se imputa."
        ),
        args_model=RegistrarGastoReciboArgs, handler=_registrar_gasto_recibo,
        rol_min="vendedor", feature="obras",
    ),
)

POR_NOMBRE: dict[str, ObraTool] = {t.nombre: t for t in CATALOGO_OBRA}


def catalogo_visible(ctx: Contexto) -> list[ObraTool]:
    """Herramientas que el rol alcanza y la empresa tiene habilitadas (filtro de exposición)."""
    return [
        t for t in CATALOGO_OBRA
        if satisface(ctx.rol, t.rol_min) and ctx.tiene_capacidad(t.feature)
    ]


def exponer_catalogo(ctx: Contexto) -> list[ToolSpec]:
    """Specs que ve el modelo (filtradas por rol + capacidad), listas para el runtime del agente."""
    return [t.spec for t in catalogo_visible(ctx)]


async def ejecutar(tool_call: ToolCall, ctx: Contexto, deps: ObraDeps) -> Resultado | ErrorTool:
    """Frontera de ejecución del pack: RBAC → capacidad → validación de args (Pydantic) → handler.

    Defensa en profundidad (además del filtrado de `catalogo_visible`): re-chequea `rol_min` y
    `feature`, y valida los args ESTRICTOS (`extra="forbid"`). Cualquier identidad que el modelo
    intente colar por args (tenant/telegram_user_id/usuario) NO está en ningún `args_model` → se
    rechaza como `validacion`; la identidad sale SIEMPRE del `Contexto`/`ContextoTelegram`.
    """
    tool = POR_NOMBRE.get(tool_call.name)
    if tool is None:
        return ErrorTool("error_interno", f"Herramienta desconocida: {tool_call.name}")
    if not satisface(ctx.rol, tool.rol_min):
        return ErrorTool("permiso_denegado", f"{tool.nombre} requiere rol {tool.rol_min}")
    if not ctx.tiene_capacidad(tool.feature):
        return ErrorTool("capacidad_no_habilitada", f"{tool.nombre} no está habilitada")
    try:
        args = tool.args_model(**tool_call.arguments)
    except ValidationError:
        return ErrorTool("validacion", "Argumentos inválidos para la herramienta.", recuperable=True)
    return await tool.handler(args, ctx, deps)
