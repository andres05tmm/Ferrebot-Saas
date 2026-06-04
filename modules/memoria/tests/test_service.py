"""Entregable 4 — servicio de memoria conversacional (unitario, repo FAKE en memoria, sin PG).

Pin de las decisiones E4:
  - cargar_historial: últimos N=8 ASC como Message(role, content), ignorando roles no {user,assistant};
    [] si no hay nada o si el repo falla (best-effort).
  - guardar_turno: persiste user + assistant; trunca contenido > 20_000; si el repo lanza, no propaga.
  - recordar_entidad / leer_entidades: upsert por (tipo, clave=str(chat_id)) y lectura del último
    cliente/producto; valor JSONB round-trip; {} si falla.
El service NO toca Postgres: todo pasa por el puerto MemoriaRepo, falseado aquí.
"""
from core.llm.base import Message
from modules.memoria.schemas import EntidadGuardada, MensajeGuardado
from modules.memoria.service import (
    MAX_CONTENIDO,
    TIPO_ULTIMO_CLIENTE,
    TIPO_ULTIMO_PRODUCTO,
    MemoriaService,
)


# --------------------------------- fake -----------------------------------

class FakeMemoriaRepo:
    """Repo en memoria que implementa el Protocol MemoriaRepo. `falla` fuerza errores del repo."""

    def __init__(self, *, falla: bool = False) -> None:
        self._mensajes: dict[int, list[MensajeGuardado]] = {}
        self._entidades: dict[tuple[str, str], dict] = {}   # (tipo, clave) -> valor
        self.guardados: list[tuple[int, str, str]] = []
        self.upserts: list[tuple[str, str, dict]] = []
        self.falla = falla

    def sembrar(self, chat_id: int, mensajes: list[MensajeGuardado]) -> None:
        self._mensajes[chat_id] = list(mensajes)

    async def ultimos_mensajes(self, chat_id: int, limite: int) -> list[MensajeGuardado]:
        if self.falla:
            raise RuntimeError("fallo de lectura del repo")
        # El repo entrega ya la ventana ASC (últimos `limite`, más antiguo primero).
        return self._mensajes.get(chat_id, [])[-limite:]

    async def guardar_mensaje(self, chat_id: int, rol: str, contenido: str) -> None:
        if self.falla:
            raise RuntimeError("fallo de escritura del repo")
        self.guardados.append((chat_id, rol, contenido))

    async def upsert_entidad(self, tipo: str, clave: str, valor: dict) -> None:
        if self.falla:
            raise RuntimeError("fallo de upsert del repo")
        self.upserts.append((tipo, clave, valor))
        self._entidades[(tipo, clave)] = valor

    async def entidades_por_clave(self, clave: str) -> list[EntidadGuardada]:
        if self.falla:
            raise RuntimeError("fallo de lectura del repo")
        return [
            EntidadGuardada(tipo=t, valor=v)
            for (t, k), v in self._entidades.items()
            if k == clave
        ]


# ------------------------------ cargar_historial --------------------------

async def test_cargar_historial_ultimos_8_asc():
    repo = FakeMemoriaRepo()
    seq = []
    for i in range(10):
        rol = "user" if i % 2 == 0 else "assistant"
        seq.append(MensajeGuardado(rol=rol, contenido=f"m{i}"))
    repo.sembrar(555, seq)

    hist = await MemoriaService(repo).cargar_historial(555)

    assert len(hist) == 8                                  # tope N=8
    assert [m.content for m in hist] == [f"m{i}" for i in range(2, 10)]   # ASC, últimos 8
    assert all(isinstance(m, Message) for m in hist)
    assert hist[0].role == "user" and hist[1].role == "assistant"


async def test_cargar_historial_ignora_roles_no_validos():
    repo = FakeMemoriaRepo()
    repo.sembrar(555, [
        MensajeGuardado(rol="system", contenido="prompt"),
        MensajeGuardado(rol="user", contenido="hola"),
        MensajeGuardado(rol="tool", contenido="{...}"),
        MensajeGuardado(rol="assistant", contenido="¿en qué ayudo?"),
    ])

    hist = await MemoriaService(repo).cargar_historial(555)

    assert hist == [
        Message(role="user", content="hola"),
        Message(role="assistant", content="¿en qué ayudo?"),
    ]


async def test_cargar_historial_vacio_devuelve_lista():
    repo = FakeMemoriaRepo()
    assert await MemoriaService(repo).cargar_historial(999) == []


async def test_cargar_historial_best_effort_si_repo_falla():
    repo = FakeMemoriaRepo(falla=True)
    assert await MemoriaService(repo).cargar_historial(555) == []   # degrada, no propaga


# ------------------------------- guardar_turno ----------------------------

async def test_guardar_turno_persiste_user_y_assistant():
    repo = FakeMemoriaRepo()
    await MemoriaService(repo).guardar_turno(555, usuario="2 martillo", asistente="Listo, registrada.")

    assert repo.guardados == [
        (555, "user", "2 martillo"),
        (555, "assistant", "Listo, registrada."),
    ]


async def test_guardar_turno_trunca_contenido_largo():
    repo = FakeMemoriaRepo()
    largo = "x" * (MAX_CONTENIDO + 5_000)
    await MemoriaService(repo).guardar_turno(555, usuario=largo, asistente="ok")

    assert len(repo.guardados[0][2]) == MAX_CONTENIDO       # truncado al persistir


async def test_guardar_turno_best_effort_no_propaga():
    repo = FakeMemoriaRepo(falla=True)
    # No debe propagar el error del repo (la respuesta al usuario ya salió).
    await MemoriaService(repo).guardar_turno(555, usuario="hola", asistente="hey")


# --------------------------- entidades (round-trip) -----------------------

async def test_recordar_y_leer_entidad_round_trip():
    repo = FakeMemoriaRepo()
    svc = MemoriaService(repo)
    await svc.recordar_entidad(555, TIPO_ULTIMO_CLIENTE, {"id": 7, "nombre": "Juan"})
    await svc.recordar_entidad(555, TIPO_ULTIMO_PRODUCTO, {"id": 3, "nombre": "Martillo"})

    leidas = await svc.leer_entidades(555)

    assert leidas == {
        TIPO_ULTIMO_CLIENTE: {"id": 7, "nombre": "Juan"},
        TIPO_ULTIMO_PRODUCTO: {"id": 3, "nombre": "Martillo"},
    }
    # El alcance se codifica en la clave = str(chat_id).
    assert repo.upserts[0][1] == "555"


async def test_recordar_entidad_upsert_sobrescribe():
    repo = FakeMemoriaRepo()
    svc = MemoriaService(repo)
    await svc.recordar_entidad(555, TIPO_ULTIMO_CLIENTE, {"id": 1, "nombre": "Ana"})
    await svc.recordar_entidad(555, TIPO_ULTIMO_CLIENTE, {"id": 2, "nombre": "Beto"})

    leidas = await svc.leer_entidades(555)
    assert leidas == {TIPO_ULTIMO_CLIENTE: {"id": 2, "nombre": "Beto"}}   # último gana


async def test_leer_entidades_aisla_por_chat():
    repo = FakeMemoriaRepo()
    svc = MemoriaService(repo)
    await svc.recordar_entidad(555, TIPO_ULTIMO_CLIENTE, {"id": 7, "nombre": "Juan"})

    assert await svc.leer_entidades(999) == {}             # otro chat no ve la entidad


async def test_leer_entidades_best_effort_si_repo_falla():
    repo = FakeMemoriaRepo(falla=True)
    assert await MemoriaService(repo).leer_entidades(555) == {}
