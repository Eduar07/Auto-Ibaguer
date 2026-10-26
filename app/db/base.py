"""Base común de todas las tablas.

Cada módulo (turnos, clientes, catalogo...) define sus tablas heredando de `Base`.
"""

from enum import StrEnum

from sqlalchemy import Enum, MetaData
from sqlalchemy.orm import DeclarativeBase

# Nombres fijos para llaves, índices y restricciones. Así las migraciones siempre
# generan los mismos nombres y es fácil saber qué regla falló cuando la base rechaza algo.
CONVENCION_DE_NOMBRES = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCION_DE_NOMBRES)


def lista_cerrada(tipo: type[StrEnum], nombre: str) -> Enum:
    """Columna de texto que solo acepta los valores de `tipo`.

    La base de datos rechaza cualquier otro valor con una restricción CHECK.
    Se guarda como texto (no como ENUM de PostgreSQL) para poder agregar valores
    más adelante con una migración sencilla.
    """
    return Enum(
        tipo,
        name=nombre,
        native_enum=False,
        create_constraint=True,
        length=20,
        validate_strings=True,
        values_callable=lambda miembros: [m.value for m in miembros],
    )
