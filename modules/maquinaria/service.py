"""Servicio de maquinaria: validación de dominio sobre el repositorio (sin SQL).

Calca `modules/inventario/service.py`: el código de máquina es único (409); la edición es PARCIAL
(solo los campos enviados en el PATCH). El SQL vive en `SqlMaquinasRepository`; aquí solo la lógica.

El WRITE de horas (`registrar_horas`, Fase 3) aplica el MÍNIMO facturable con la función pura
`services.calculations.maquinas.horas_facturables` y deja el SEAM de la cartera de alquiler (Fase 5).
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from services.calculations.maquinas import horas_facturables

from modules.maquinaria.errors import (
    CodigoMaquinaDuplicado,
    MaquinaInexistente,
    SinAsignacionActiva,
)
from modules.maquinaria.models import (
    AsignacionMaquinaObra,
    Maquina,
    RegistroHorasMaquina,
)
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import MaquinaActualizar, MaquinaCrear, RegistroHorasCrear


@dataclass(frozen=True, slots=True)
class ResultadoRegistroHoras:
    """Resumen de un parte de horas (lo que ve el caller/bot). `replay=True` = el parte de ese día ya
    existía (idempotencia por clave natural máquina/obra/fecha); no se creó un segundo registro."""

    registro_id: int
    maquina_id: int
    obra_id: int
    fecha: date
    horas_trabajadas: Decimal
    horas_facturables: Decimal
    minimo_cubierto: bool
    precio_hora: Decimal
    ingreso: Decimal
    origen_registro: str
    replay: bool


class MaquinariaService:
    def __init__(self, repo: SqlMaquinasRepository) -> None:
        self._repo = repo

    async def listar(self, *, estado: str | None = None, q: str | None = None) -> list[Maquina]:
        return await self._repo.listar(estado=estado, q=q)

    async def obtener(self, maquina_id: int) -> Maquina:
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)
        return maquina

    async def crear(self, datos: MaquinaCrear) -> Maquina:
        """Da de alta la máquina. 409 si el código ya lo usa otra (incluida una eliminada: el UNIQUE
        de la BD no distingue soft delete)."""
        if await self._repo.codigo_existe(datos.codigo):
            raise CodigoMaquinaDuplicado(datos.codigo)
        return await self._repo.crear(datos)

    async def actualizar(self, maquina_id: int, datos: MaquinaActualizar) -> Maquina:
        """Edición parcial: solo los campos presentes en el PATCH. 404 si no existe; 409 si el nuevo
        código lo usa otra máquina."""
        cambios = datos.model_dump(exclude_unset=True)
        codigo = cambios.get("codigo")
        if codigo is not None and await self._repo.codigo_existe(codigo, excluir_id=maquina_id):
            raise CodigoMaquinaDuplicado(codigo)
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)
        return await self._repo.actualizar(maquina, cambios)

    async def eliminar(self, maquina_id: int) -> None:
        """Soft delete (`eliminado_en`). 404 si no existe o ya estaba eliminada."""
        if not await self._repo.soft_delete(maquina_id):
            raise MaquinaInexistente(maquina_id)

    # ---- Lecturas de operación (solo lectura) -------------------------------
    async def listar_asignaciones(self, maquina_id: int) -> list[AsignacionMaquinaObra]:
        return await self._repo.listar_asignaciones(maquina_id)

    async def listar_horas(
        self, maquina_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[RegistroHorasMaquina]:
        return await self._repo.listar_horas(maquina_id, limite=limite, offset=offset)

    # ---- Registro de horas (WRITE, Fase 3) ----------------------------------
    async def registrar_horas(
        self, maquina_id: int, datos: RegistroHorasCrear
    ) -> ResultadoRegistroHoras:
        """Registra el parte de horas del día de una máquina en una obra, aplicando el MÍNIMO facturable.

        Resuelve la asignación ACTIVA de (máquina, obra) que cubre `datos.fecha` para tomar el precio y el
        mínimo PACTADOS (pueden diferir del default de la máquina). Las horas a facturar salen de la
        función pura `horas_facturables(horas, mínimo) = max(...)`: si trabajó menos que el mínimo se cobra
        el mínimo (piso de movilización); si trabajó más, lo trabajado.

        IDEMPOTENCIA (invariante del carve-out): la identidad de un parte es la CLAVE NATURAL
        `(maquina, obra, fecha)` —la spec define un parte POR MÁQUINA POR DÍA—. Reintentar el mismo día
        (p. ej. el bot de Fase 6 tras un timeout) devuelve el registro existente con `replay=True` y NO
        inserta un segundo → el cargo a cartera de Fase 5 se asienta una sola vez. `datos.idempotency_key`
        se acepta para el contrato del bot, pero hoy la idempotencia se ancla en la clave natural: no hay
        columna dedicada (models/migraciones son de otro agente). Hardening futuro (cuando se puedan tocar
        esos árboles): columna `idempotency_key` + índice único parcial, espejando el de `modules/fiados`.

        Para SERIALIZAR partes concurrentes del mismo día se bloquea la asignación (`FOR UPDATE`) antes del
        pre-check, igual que el lock-de-ancla de fiados. 404 si la máquina no existe; `SinAsignacionActiva`
        (→409) si no hay asignación activa que cubra la fecha.
        """
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)

        # Lock del ancla (asignación) → sección crítica del pre-check de idempotencia.
        asignacion = await self._repo.asignacion_activa(
            maquina_id, datos.obra_id, datos.fecha, bloquear=True
        )
        if asignacion is None:
            raise SinAsignacionActiva(maquina_id, datos.obra_id, datos.fecha)

        # Idempotencia por clave natural: ¿ya hay parte de esta (máquina, obra, fecha)? → replay.
        existente = await self._repo.registro_del_dia(maquina_id, datos.obra_id, datos.fecha)
        if existente is not None:
            return self._resumen(existente, asignacion, replay=True)

        facturables = horas_facturables(datos.horas_trabajadas, Decimal(asignacion.minimo_horas))
        registro = await self._repo.crear_registro_horas(
            maquina_id=maquina_id,
            obra_id=datos.obra_id,
            fecha=datos.fecha,
            horas_trabajadas=datos.horas_trabajadas,
            horas_facturables=facturables,
            operador_id=datos.operador_id,
            observaciones=datos.observaciones,
            origen_registro=datos.origen_registro,
        )

        # ── SEAM Fase 5 (cartera de alquiler) — EN LA MISMA TRANSACCIÓN que el registro ────────────────
        await self._asentar_consumo_cartera(registro, asignacion)
        # ──────────────────────────────────────────────────────────────────────────────────────────────

        return self._resumen(registro, asignacion, replay=False)

    def _resumen(
        self,
        registro: RegistroHorasMaquina,
        asignacion: AsignacionMaquinaObra,
        *,
        replay: bool,
    ) -> ResultadoRegistroHoras:
        """Arma el resumen de salida: si se cubrió el mínimo e ingreso = horas_facturables × precio pactado."""
        minimo = Decimal(asignacion.minimo_horas)
        return ResultadoRegistroHoras(
            registro_id=registro.id,
            maquina_id=registro.maquina_id,
            obra_id=registro.obra_id,
            fecha=registro.fecha,
            horas_trabajadas=registro.horas_trabajadas,
            horas_facturables=registro.horas_facturables,
            minimo_cubierto=registro.horas_trabajadas >= minimo,
            precio_hora=asignacion.precio_hora,
            ingreso=registro.horas_facturables * asignacion.precio_hora,
            origen_registro=registro.origen_registro,
            replay=replay,
        )

    async def _asentar_consumo_cartera(
        self, registro: RegistroHorasMaquina, asignacion: AsignacionMaquinaObra
    ) -> None:
        """SEAM Fase 5 (HOY NO-OP). Punto de extensión donde la cartera de alquiler asentará el consumo de
        horas en el ledger de fiados, EN LA MISMA TRANSACCIÓN que el registro (invariante «nada mueve
        cartera sin registro de horas»). El registro ya tiene `id` por el flush del repositorio.

        Cuando arranque la Fase 5, este service recibirá un `CarteraAlquilerService` opcional por el
        `__init__` (para no acoplar maquinaria↔cartera hoy) y aquí se llamará —solo si el tenant tiene la
        capacidad `cartera_alquiler` activa— el contrato:

            TODO(Fase5): await self._cartera.asentar_consumo_horas(
                registro_horas_id=registro.id,          # ancla de idempotencia (UNIQUE en cargos_alquiler)
                obra_id=registro.obra_id,               # el service resuelve cliente_id por la obra
                maquina_id=registro.maquina_id,
                asignacion_id=asignacion.id,            # precio/mínimo aplicados
                horas_facturables=registro.horas_facturables,
                precio_hora=asignacion.precio_hora,
            )

        `asentar_consumo_horas` es idempotente por `registro.id` y reusa
        `FiadosService.crear(idempotency_key=f"alquiler:horas:{registro.id}")`. Diseño completo en
        `docs/research/pim-fase5-cartera-diseno.md` §2. Se deja vacío a propósito: mantiene el contrato
        sin implementar la cartera.
        """
        return None
