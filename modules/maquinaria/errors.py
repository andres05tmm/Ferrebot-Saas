"""Errores de dominio de maquinaria (el router los mapea a HTTP).

Calca `modules/inventario/errors.py`: excepciones de dominio propias que el router traduce a
código HTTP (404 / 409). Así el servicio no conoce FastAPI y se puede testear aislado.
"""


class MaquinariaError(Exception):
    """Base de errores de maquinaria."""


class MaquinaInexistente(MaquinariaError):
    def __init__(self, maquina_id: int) -> None:
        super().__init__(f"La máquina {maquina_id} no existe")
        self.maquina_id = maquina_id


class CodigoMaquinaDuplicado(MaquinariaError):
    """Otra máquina ya usa ese código (columna UNIQUE) → 409."""

    def __init__(self, codigo: str) -> None:
        super().__init__(f"Ya existe una máquina con el código {codigo!r}")
        self.codigo = codigo


class MantenimientoInexistente(MaquinariaError):
    """No hay mantenimiento con ese id para la máquina indicada → 404.

    Se acota SIEMPRE por `(maquina_id, mantenimiento_id)`: un mantenimiento de otra máquina se trata como
    inexistente para ésta (no se filtra por la ruta de una máquina ajena)."""

    def __init__(self, mantenimiento_id: int) -> None:
        super().__init__(f"El mantenimiento {mantenimiento_id} no existe")
        self.mantenimiento_id = mantenimiento_id


class SinAsignacionActiva(MaquinariaError):
    """No hay asignación ACTIVA de la máquina a la obra que cubra la fecha del parte → 409.

    Sin asignación no existen el precio ni el mínimo PACTADOS para esa obra, así que no se puede facturar
    la hora. El caller debe crear (o reactivar) la asignación máquina→obra antes de registrar horas.
    """

    def __init__(self, maquina_id: int, obra_id: int, fecha: object) -> None:
        super().__init__(
            f"La máquina {maquina_id} no tiene asignación activa a la obra {obra_id} que cubra {fecha}"
        )
        self.maquina_id = maquina_id
        self.obra_id = obra_id
        self.fecha = fecha


class AsignacionSolapada(MaquinariaError):
    """Ya hay una asignación ACTIVA de la máquina cuyo rango se cruza con el nuevo → 409.

    Una máquina no puede estar en dos obras el mismo día: el rango [fecha_inicio, fecha_fin] (fecha_fin
    NULL = abierto/infinito) del alta o edición no puede solaparse con el de otra asignación activa.
    """

    def __init__(self, maquina_id: int, fecha_inicio: object, fecha_fin: object) -> None:
        super().__init__(
            f"La máquina {maquina_id} ya tiene una asignación activa que se solapa con "
            f"[{fecha_inicio}, {fecha_fin}]"
        )
        self.maquina_id = maquina_id
        self.fecha_inicio = fecha_inicio
        self.fecha_fin = fecha_fin


class AsignacionInexistente(MaquinariaError):
    """No hay asignación con ese id para la máquina indicada → 404.

    Se acota SIEMPRE por `(maquina_id, asignacion_id)`: una asignación de otra máquina se trata como
    inexistente para ésta (no se toca por la ruta de una máquina ajena)."""

    def __init__(self, asignacion_id: int) -> None:
        super().__init__(f"La asignación {asignacion_id} no existe")
        self.asignacion_id = asignacion_id


class ObraNoAsignable(MaquinariaError):
    """La obra no admite una asignación nueva. `motivo` distingue el mapeo HTTP del router:

    - `"inexistente"` (no existe o soft-deleted) → 404;
    - `"liquidada"` (obra en estado LIQUIDADA, ya cerrada) → 409.
    """

    def __init__(self, obra_id: int, motivo: str) -> None:
        super().__init__(f"La obra {obra_id} no admite asignación ({motivo})")
        self.obra_id = obra_id
        self.motivo = motivo


class OperadorInexistente(MaquinariaError):
    """El operador indicado no existe como trabajador activo → 404."""

    def __init__(self, operador_id: int) -> None:
        super().__init__(f"El operador {operador_id} no existe")
        self.operador_id = operador_id
