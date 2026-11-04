"""Reúne todas las tablas en un solo lugar.

Alembic y las pruebas importan este archivo para conocer las 18 tablas.
Cuando un módulo nuevo agregue tablas (Fase 2 y 3), se importa aquí.
"""

from app.bot import tablas as bot  # noqa: F401
from app.catalogo import tablas as catalogo  # noqa: F401
from app.clientes import tablas as clientes  # noqa: F401
from app.comentarios import tablas as comentarios  # noqa: F401
from app.db.base import Base
from app.integraciones import tablas as integraciones  # noqa: F401
from app.turnos import tablas as turnos  # noqa: F401
from app.web import tablas as web  # noqa: F401

metadata = Base.metadata
