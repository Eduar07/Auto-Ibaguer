"""Conexión a la base de datos.

Una "sesión" es una conversación con PostgreSQL: se abren cambios, se confirman
(commit) o se deshacen (rollback). Cada petición al servidor usa su propia sesión.
"""

from collections.abc import Callable, Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.config import obtener_entorno

# Todas las tablas quedan cargadas antes de abrir una sesión: si falta una, SQLAlchemy no
# sabe resolver las llaves foráneas que apuntan a ella (por ejemplo, la del administrador
# en el historial de un turno). Así funciona igual desde el servidor, un comando o un script.
from app.db import modelos  # noqa: F401


@lru_cache
def obtener_motor() -> Engine:
    """El "motor" maneja las conexiones abiertas con PostgreSQL y las reutiliza.

    hide_parameters: si una consulta falla, el error que queda en el registro del servidor
    muestra la consulta pero no los datos (celulares, nombres, lo que escribió un cliente).
    """
    return create_engine(obtener_entorno().database_url, pool_pre_ping=True, hide_parameters=True)


# Lo que abre una sesión nueva cada vez que se llama. El reloj y la bandeja de entrada lo
# reciben así (y no una sesión ya abierta) porque hacen varias transacciones seguidas; en
# las pruebas se les pasa uno que trabaja dentro de la transacción de la prueba.
AbrirSesion = Callable[[], Session]


def abrir_sesion() -> Session:
    """Una sesión nueva. La usan las peticiones al servidor, el reloj y los comandos."""
    return Session(obtener_motor(), expire_on_commit=False)


def obtener_sesion() -> Iterator[Session]:
    """Entrega una sesión y la cierra al terminar. FastAPI la usa como dependencia."""
    with abrir_sesion() as sesion:
        yield sesion
