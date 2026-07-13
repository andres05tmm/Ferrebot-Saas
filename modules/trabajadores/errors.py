"""Errores de dominio de trabajadores (el router los mapea a HTTP)."""


class TrabajadoresError(Exception):
    """Base de errores de trabajadores."""


class TrabajadorInexistente(TrabajadoresError):
    def __init__(self, trabajador_id: int) -> None:
        super().__init__(f"El trabajador {trabajador_id} no existe")
        self.trabajador_id = trabajador_id


class TrabajadorDuplicado(TrabajadoresError):
    """Ya hay un trabajador con ese documento (la columna es UNIQUE en la base)."""

    def __init__(self, documento: str) -> None:
        super().__init__(f"Ya existe un trabajador con documento {documento!r}")
        self.documento = documento


class TrabajadorInactivo(TrabajadoresError):
    """El trabajador está INACTIVO (baja laboral reversible) → 409 al asignarlo a obra.

    Un inactivo no debe aparecer "en obra" en el calendario ni liquidarse; se reactiva primero."""

    def __init__(self, trabajador_id: int) -> None:
        super().__init__(f"El trabajador {trabajador_id} está inactivo: reactívalo antes de asignarlo")
        self.trabajador_id = trabajador_id


class AsignacionSolapada(TrabajadoresError):
    """Ya hay una asignación ACTIVA del trabajador cuyo rango se cruza con el nuevo → 409.

    Un trabajador no puede estar en dos obras el mismo día: el rango [fecha_inicio, fecha_fin] (fecha_fin
    NULL = abierto/infinito) no puede solaparse con el de otra asignación activa."""

    def __init__(self, trabajador_id: int, fecha_inicio: object, fecha_fin: object) -> None:
        super().__init__(
            f"El trabajador {trabajador_id} ya tiene una asignación activa que se solapa con "
            f"[{fecha_inicio}, {fecha_fin}]"
        )
        self.trabajador_id = trabajador_id
        self.fecha_inicio = fecha_inicio
        self.fecha_fin = fecha_fin


class RangoAsignacionInvalido(TrabajadoresError):
    """El parche dejaría `fecha_fin < fecha_inicio` → 422 (mismo guard que maquinaria: el CREATE valida
    en el schema, el PATCH aquí — un rango invertido desaparece de toda consulta)."""

    def __init__(self, fecha_inicio: object, fecha_fin: object) -> None:
        super().__init__(
            f"La fecha fin ({fecha_fin}) no puede ser anterior al inicio ({fecha_inicio}) de la asignación"
        )
        self.fecha_inicio = fecha_inicio
        self.fecha_fin = fecha_fin


class AsignacionInexistente(TrabajadoresError):
    """No hay asignación con ese id para el trabajador indicado → 404 (acotada a su trabajador)."""

    def __init__(self, asignacion_id: int) -> None:
        super().__init__(f"La asignación {asignacion_id} no existe")
        self.asignacion_id = asignacion_id


class ObraNoAsignable(TrabajadoresError):
    """La obra no admite una asignación nueva. `motivo` distingue el mapeo HTTP:

    - `"inexistente"` (no existe o soft-deleted) → 404;
    - `"liquidada"` (obra LIQUIDADA, ya cerrada) → 409.
    """

    def __init__(self, obra_id: int, motivo: str) -> None:
        super().__init__(f"La obra {obra_id} no admite asignación ({motivo})")
        self.obra_id = obra_id
        self.motivo = motivo
