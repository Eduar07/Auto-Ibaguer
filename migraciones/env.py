"""Cómo corre Alembic: a qué base se conecta y qué tablas conoce."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import obtener_entorno
from app.db.modelos import metadata

config = context.config

if config.config_file_name is not None and config.attributes.get("configurar_logs", True):
    fileConfig(config.config_file_name)

target_metadata = metadata


def url_de_la_base() -> str:
    """Usa la URL que le pasen (las pruebas lo hacen) o la de DATABASE_URL."""
    return config.get_main_option("sqlalchemy.url") or obtener_entorno().database_url


def correr_sin_conexion() -> None:
    """Genera el SQL sin conectarse (alembic upgrade head --sql)."""
    context.configure(
        url=url_de_la_base(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def correr_con_conexion() -> None:
    motor = create_engine(url_de_la_base(), poolclass=pool.NullPool)
    with motor.connect() as conexion:
        context.configure(connection=conexion, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    correr_sin_conexion()
else:
    correr_con_conexion()
