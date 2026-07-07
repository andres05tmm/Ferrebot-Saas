"""Cartera de alquiler: cupos de crédito, traza idempotente de cargos y config de colitas (Fase 5,
tenant 0049). Módulo NUESTRO (no está en la spec del cliente).

El SALDO consumido NO vive aquí: la fuente de verdad sigue siendo el ledger de `modules.fiados`
(Σ cargos − Σ abonos) y el contador `clientes.saldo_fiado` (diseño §1.2). Estas tablas aportan el TOPE
de crédito (`Cupo`), la TRAZA idempotente de cada cargo que un registro de horas asienta en el ledger
(`CargoAlquiler`, cuyo `UNIQUE(registro_horas_id)` es el ancla dura del invariante «un registro de horas
no genera dos cargos») y la CONFIG de detección de colitas (`CarteraConfig`).

Siguiendo el patrón del repo (ver `modules.maquinaria`/`modules.obra`), las FKs viven en la migración y
el ORM mapea los ids como `BigInteger` sin `relationship` —no se acopla un módulo a otro por una columna.
Tablas de negocio del tenant (sin `empresa_id`: la base ES la frontera). Dinero: el `cupo` en MONEY4
(18,4, vertical construcción); el `monto` del cargo en MONEY (12,2), ya cuantizado al ledger de fiados
(el cruce de la frontera de precisión 18,4 → 12,2 lo hace el service de cartera, diseño §2).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY, MONEY4


class Cupo(TenantBase):
    """Tope de crédito de alquiler de un cliente (tabla `cupos_alquiler`).

    Invariante «un cupo ACTIVO por cliente»: índice único parcial `uq_cupos_alquiler_cliente_activo`
    WHERE activo (migración 0049). Cambiar de cupo = desactivar el vigente y crear otro; el histórico
    queda por `vigente_desde/hasta`. El consumo NO se guarda aquí (lo aporta el ledger de fiados):
    `disponible = cupo − clientes.saldo_fiado`."""

    __tablename__ = "cupos_alquiler"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(BigInteger, nullable=False)   # FK clientes.id (migración)
    cupo: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    vigente_desde: Mapped[date] = mapped_column(Date, nullable=False)
    vigente_hasta: Mapped[date | None] = mapped_column(Date)   # NULL = sin vencimiento
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())
    notas: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CargoAlquiler(TenantBase):
    """Traza de un cargo de alquiler: enlaza un `RegistroHorasMaquina` con el `Fiado` que asentó (tabla
    `cargos_alquiler`).

    `registro_horas_id` es UNIQUE (migración 0049): un registro de horas NO genera dos cargos en cartera
    —el ancla DURA (a nivel de base) del invariante de idempotencia, defensa en profundidad sobre el lock
    de cliente de `FiadosService.crear`. Mapea obra→fiados para la vista de cartera por obra y el abono
    FIFO (diseño §1.3/§3). `monto` ya viene cuantizado al ledger (MONEY 12,2)."""

    __tablename__ = "cargos_alquiler"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    registro_horas_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    fiado_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    obra_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    maquina_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    asignacion_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CarteraConfig(TenantBase):
    """Config de una fila (get-or-create) de la cartera de alquiler (tabla `cartera_config`).

    Gobierna la detección de «colita» estancada (cliente con saldo, sin abonar hace mucho, con la obra
    ya finalizada/liquidada): `dias_colita` = N días sin abono para marcarla; `cadencia_aviso_dias` =
    no re-avisar la misma colita antes de N días (dedup, patrón `cobranza_config`/`pagar_config`)."""

    __tablename__ = "cartera_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())
    dias_colita: Mapped[int] = mapped_column(Integer, nullable=False, server_default="15")
    cadencia_aviso_dias: Mapped[int] = mapped_column(Integer, nullable=False, server_default="7")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
