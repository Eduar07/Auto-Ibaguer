"""Preparación compartida de las pruebas.

Las pruebas usan un PostgreSQL real (el de docker-compose.yml) con una base aparte,
`auto_ibaguer_pruebas`, que se borra y se crea de nuevo en cada corrida. Tus datos de
desarrollo no se tocan.
"""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import tiempo
from app.catalogo.datos_iniciales import cargar_datos_iniciales
from app.cli import MENSAJE_SIN_BASE, migrar
from app.db.sesion import obtener_sesion
from app.main import app
from app.web import entrar, sesiones
from app.web.administradores import guardar_administrador
from app.web.tablas import Administrador
from pruebas.base_de_pruebas import URL_PRUEBAS, recrear_base
from pruebas.fabricas import LUNES_8AM
from pruebas.navegador import CLAVE


@pytest.fixture(scope="session")
def motor() -> Iterator[Engine]:
    """Base de pruebas con todas las tablas y los datos de Auto Ibaguer."""
    try:
        recrear_base(URL_PRUEBAS)
    except OperationalError:
        # Sin PostgreSQL, cada prueba que lo necesita fallaría con el mismo error largo.
        # Mejor parar de una vez con un mensaje claro.
        pytest.exit(MENSAJE_SIN_BASE + "\nDespués vuelve a correr: uv run pytest", returncode=1)
    migrar(URL_PRUEBAS, configurar_logs=False)
    motor = create_engine(URL_PRUEBAS)
    with Session(motor) as sesion, sesion.begin():
        cargar_datos_iniciales(sesion)
    yield motor
    motor.dispose()


@pytest.fixture
def conexion(motor: Engine) -> Iterator[Connection]:
    """Conexión dentro de una transacción que se deshace al terminar la prueba.

    Así lo que una prueba guarda nunca afecta a las demás.
    """
    with motor.connect() as conexion:
        transaccion = conexion.begin()
        yield conexion
        transaccion.rollback()


@pytest.fixture
def sesion(conexion: Connection) -> Iterator[Session]:
    """Sesión del ORM para las pruebas. Todo lo que guarde se deshace al final."""
    with Session(bind=conexion, join_transaction_mode="create_savepoint") as sesion:
        yield sesion


# --- El dashboard, como lo usa el navegador ----------------------------------------------


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    """La hora del sistema queda fija. Para adelantarla: reloj["ahora"] += minutos(5)."""
    hora = {"ahora": LUNES_8AM}
    monkeypatch.setattr(tiempo, "ahora", lambda: hora["ahora"])
    return hora


@pytest.fixture
def web(sesion: Session, reloj: dict, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Un navegador sin sesión. Cada prueba empieza sin intentos fallidos guardados."""
    app.dependency_overrides[obtener_sesion] = lambda: sesion
    monkeypatch.setattr(entrar, "LIMITE_DE_INTENTOS", sesiones.LimiteDeIntentos())
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def admin(sesion: Session) -> Administrador:
    administrador = guardar_administrador(sesion, "eduar", CLAVE)[0]
    # Confirmado, como si ya existiera: si una acción del dashboard falla y se deshace,
    # el administrador sigue ahí. Igual se borra al final de la prueba.
    sesion.commit()
    return administrador


@pytest.fixture
def adentro(web: TestClient, admin: Administrador) -> TestClient:
    """Un navegador que ya entró con el usuario del administrador."""
    respuesta = web.post(
        "/entrar", data={"usuario": "eduar", "clave": CLAVE}, follow_redirects=False
    )
    assert respuesta.status_code == 303
    return web
