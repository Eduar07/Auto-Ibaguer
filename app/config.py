"""Configuración del entorno: conexiones, claves y zona horaria.

Se lee de las variables de entorno o del archivo .env. Aquí solo va lo técnico.
Los parámetros del negocio (horario, tiempo de gracia, precios...) viven en la base de datos,
en la tabla `configuracion`, para que el administrador los cambie sin tocar código.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfiguracionEntorno(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://auto:auto@localhost:5433/auto_ibaguer"
    zona_horaria: str = "America/Bogota"
    entorno: str = "desarrollo"

    @field_validator("database_url")
    @classmethod
    def usar_psycopg(cls, url: str) -> str:
        """Los servicios en la nube entregan la URL como postgres:// o postgresql://.

        SQLAlchemy necesita saber con qué conector hablar; aquí siempre es psycopg.
        """
        for prefijo in ("postgres://", "postgresql://"):
            if url.startswith(prefijo):
                return "postgresql+psycopg://" + url.removeprefix(prefijo)
        return url


@lru_cache
def obtener_entorno() -> ConfiguracionEntorno:
    """Devuelve la configuración del entorno (se lee una sola vez)."""
    return ConfiguracionEntorno()
