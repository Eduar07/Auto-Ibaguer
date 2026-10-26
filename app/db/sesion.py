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


def obtener_sesion() -> Iterator[Session]:
    """Entrega una sesión y la cierra al terminar. FastAPI la usa como dependencia."""
    with Session(obtener_motor(), expire_on_commit=False) as sesion:
        yield sesion
