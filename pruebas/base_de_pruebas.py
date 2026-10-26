"""La base de datos de las pruebas: dónde está y cómo se crea desde cero."""

import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

URL_PRUEBAS = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://auto:auto@localhost:5433/auto_ibaguer_pruebas"
)


def _en_el_servidor(url: str, *ordenes: str) -> None:
    """Corre órdenes sobre la base `postgres`, porque una base no se puede borrar a sí misma."""
    destino = make_url(url)
    nombre = destino.database or ""
    # Seguro contra accidentes: nunca tocar una base que no sea de pruebas.
    if "prueba" not in nombre:
        raise RuntimeError(f"La base de pruebas debe tener 'prueba' en el nombre: '{nombre}'")
    servidor = create_engine(destino.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with servidor.connect() as conexion:
            for orden in ordenes:
                conexion.execute(text(orden.format(nombre=nombre)))
    finally:
        servidor.dispose()


def recrear_base(url: str) -> None:
    """Borra la base (si existe) y la crea vacía."""
    _en_el_servidor(
        url, 'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)', 'CREATE DATABASE "{nombre}"'
    )


def borrar_base(url: str) -> None:
    _en_el_servidor(url, 'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)')
