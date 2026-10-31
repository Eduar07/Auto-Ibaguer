"""Preparación compartida de las pruebas.

Las pruebas usan un PostgreSQL real (el de docker-compose.yml) con una base aparte,
`auto_ibaguer_pruebas`, que se borra y se crea de nuevo en cada corrida. Tus datos de
desarrollo no se tocan.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.catalogo.datos_iniciales import cargar_datos_iniciales
from app.cli import MENSAJE_SIN_BASE, migrar
from pruebas.base_de_pruebas import URL_PRUEBAS, recrear_base


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
