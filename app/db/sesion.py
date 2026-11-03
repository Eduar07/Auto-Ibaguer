"""Conexión a la base de datos.

Una "sesión" es una conversación con PostgreSQL: se abren cambios, se confirman
(commit) o se deshacen (rollback). Cada petición al servidor usa su propia sesión.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from app.config import obtener_entorno


@lru_cache
def obtener_motor() -> Engine:
    """El "motor" maneja las conexiones abiertas con PostgreSQL y las reutiliza."""
    return create_engine(obtener_entorno().database_url, pool_pre_ping=True)


def abrir_sesion() -> Session:
    """Una sesión nueva. La usan las peticiones al servidor, el reloj y los comandos."""
    return Session(obtener_motor(), expire_on_commit=False)


def obtener_sesion() -> Iterator[Session]:
    """Entrega una sesión y la cierra al terminar. FastAPI la usa como dependencia."""
    with abrir_sesion() as sesion:
        yield sesion
