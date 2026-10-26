"""Configuración del entorno: la URL de la base de datos llega bien escrita a todos lados."""

import pytest

from app.cli import configuracion_alembic
from app.config import ConfiguracionEntorno


@pytest.mark.parametrize(
    "url",
    [
        "postgres://auto:clave@servidor:5432/auto_ibaguer",
        "postgresql://auto:clave@servidor:5432/auto_ibaguer",
        "postgresql+psycopg://auto:clave@servidor:5432/auto_ibaguer",
    ],
)
def test_la_url_de_la_nube_se_ajusta_a_psycopg(url: str):
    entorno = ConfiguracionEntorno(database_url=url)
    assert entorno.database_url == "postgresql+psycopg://auto:clave@servidor:5432/auto_ibaguer"


def test_alembic_acepta_contrasenas_con_porcentaje():
    # "%40" es una "@" escrita dentro de la contraseña.
    url = "postgresql+psycopg://auto:clave%40segura@localhost:5433/auto_ibaguer"
    assert configuracion_alembic(url).get_main_option("sqlalchemy.url") == url
