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
