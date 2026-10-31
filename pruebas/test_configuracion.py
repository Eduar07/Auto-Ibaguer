"""Configuración del entorno: la URL de la base de datos llega bien escrita a todos lados,
y en producción nadie arranca el servidor con la clave de desarrollo."""

import pytest
from pydantic import ValidationError

from app.cli import configuracion_alembic
from app.config import CLAVE_DE_DESARROLLO, ConfiguracionEntorno

CLAVE_LARGA = "x7Kp2" * 10  # 50 caracteres


@pytest.mark.parametrize("clave", [CLAVE_DE_DESARROLLO, "corta-de-20-letras!!"])
def test_en_produccion_la_clave_secreta_es_obligatoria(clave: str):
    with pytest.raises(ValidationError, match="CLAVE_SECRETA"):
        ConfiguracionEntorno(_env_file=None, entorno="produccion", clave_secreta=clave)


def test_en_produccion_arranca_con_una_clave_larga():
    entorno = ConfiguracionEntorno(_env_file=None, entorno="produccion", clave_secreta=CLAVE_LARGA)
    assert entorno.es_produccion


def test_en_desarrollo_sirve_la_clave_de_desarrollo():
    entorno = ConfiguracionEntorno(_env_file=None, entorno="desarrollo")
    assert entorno.clave_secreta == CLAVE_DE_DESARROLLO
    assert not entorno.es_produccion


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
