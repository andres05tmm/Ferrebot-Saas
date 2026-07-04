"""Motor de conciliación bancaria (ADR 0028): determinista, igual para todos los tenants.

Ciclo de un movimiento del extracto: no_conciliado → sugerido → conciliado.

- INGESTA: idempotente por `referencia_bancaria` (el repo hace ON CONFLICT DO NOTHING).
- SUGERIR (semi-automático): por cada `no_conciliado`, busca candidatos internos por monto+fecha
  (acotado por naturaleza). SOLO si hay EXACTAMENTE UNO de alta confianza lo marca `sugerido`.
  REGLA DURA: montos ambiguos (0 o ≥2 candidatos) JAMÁS se auto-concilian — quedan `no_conciliado`
  y sus candidatos se listan para resolverlos a mano.
- CONFIRMAR (explícito): valida que el enlace elegido sea un candidato real y lo marca `conciliado`.

Conciliar SOLO escribe el estado/enlace en la fila bancaria: nunca toca caja, fiados ni CxP.
"""
from __future__ import annotations

from datetime import datetime

from core.logging import get_logger
from modules.bancos.errors import ConciliacionInvalida, MovimientoBancarioInexistente
from modules.bancos.repository import Candidato, SqlBancosRepository
from modules.bancos.schemas import (
    CandidatoInterno,
    IngestaResultado,
    MovimientoBancarioIngesta,
    MovimientoBancarioLeer,
    MovimientoConCandidatos,
)

log = get_logger("bancos")


def _a_candidato(c: Candidato) -> CandidatoInterno:
    return CandidatoInterno(tipo=c.tipo, id=c.id, monto=c.monto, fecha=c.fecha, descripcion=c.descripcion)


class BancosService:
    def __init__(self, repo: SqlBancosRepository) -> None:
        self._repo = repo

    async def ingestar(self, movimientos: list[MovimientoBancarioIngesta]) -> IngestaResultado:
        """Ingiere las líneas del extracto; idempotente por referencia (reprocesar no duplica)."""
        insertados = 0
        for m in movimientos:
            if await self._repo.ingestar_uno(
                referencia_bancaria=m.referencia_bancaria, fecha=m.fecha, monto=m.monto,
                naturaleza=m.naturaleza, descripcion=m.descripcion, remitente=m.remitente,
            ):
                insertados += 1
        duplicados = len(movimientos) - insertados
        log.info("banco_ingesta", insertados=insertados, duplicados=duplicados)
        return IngestaResultado(insertados=insertados, duplicados=duplicados)

    async def sugerir_pendientes(self) -> int:
        """Corre el match sobre los `no_conciliado`; marca `sugerido` SOLO los de candidato único.

        Devuelve cuántos quedaron `sugerido`. Los ambiguos (≥2) y los sin candidato (0) NO se tocan.
        """
        sugeridos = 0
        for mov in await self._repo.listar(estado="no_conciliado"):
            candidatos = await self._repo.candidatos(
                monto=mov.monto, fecha=mov.fecha, naturaleza=mov.naturaleza, excluir_mov_id=mov.id
            )
            if len(candidatos) == 1:                     # match único de alta confianza
                unico = candidatos[0]
                await self._repo.marcar_sugerido(mov, tipo=unico.tipo, id_interno=unico.id)
                sugeridos += 1
            # 0 o ≥2 → regla dura: nunca auto-conciliar; se deja no_conciliado
        log.info("banco_sugerencias", sugeridos=sugeridos)
        return sugeridos

    async def listar(self, *, estado: str | None) -> list[MovimientoConCandidatos]:
        """Movimientos del extracto (opcionalmente por estado) con sus candidatos internos vigentes."""
        salida: list[MovimientoConCandidatos] = []
        for mov in await self._repo.listar(estado=estado):
            candidatos = await self._repo.candidatos(
                monto=mov.monto, fecha=mov.fecha, naturaleza=mov.naturaleza, excluir_mov_id=mov.id
            )
            salida.append(
                MovimientoConCandidatos(
                    movimiento=MovimientoBancarioLeer.model_validate(mov),
                    candidatos=[_a_candidato(c) for c in candidatos],
                )
            )
        return salida

    async def confirmar(
        self, mov_id: int, *, tipo: str, id_interno: int, ahora: datetime
    ) -> MovimientoBancarioLeer:
        """Confirma EXPLÍCITAMENTE el enlace elegido (→ conciliado). Solo enlaza; no toca saldos.

        404 si el movimiento no existe; 422 si el enlace no es un candidato real (monto/fecha/naturaleza)
        o si el interno ya fue tomado por otro movimiento (no aparece entre los candidatos vigentes).
        """
        mov = await self._repo.obtener(mov_id)
        if mov is None:
            raise MovimientoBancarioInexistente(mov_id)
        candidatos = await self._repo.candidatos(
            monto=mov.monto, fecha=mov.fecha, naturaleza=mov.naturaleza
        )
        # El ya-sugerido de ESTA fila también es válido (el repo lo excluye por estar enlazado a sí misma):
        ya_suyo = mov.conciliado_con_tipo == tipo and mov.conciliado_con_id == id_interno
        if not ya_suyo and not any(c.tipo == tipo and c.id == id_interno for c in candidatos):
            raise ConciliacionInvalida(
                f"El enlace {tipo}:{id_interno} no calza con el movimiento {mov_id}"
            )
        await self._repo.confirmar(mov, tipo=tipo, id_interno=id_interno, cuando=ahora)
        log.info("banco_conciliado", mov_id=mov_id, tipo=tipo, id_interno=id_interno)
        return MovimientoBancarioLeer.model_validate(mov)
