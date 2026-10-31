"""Configuración del entorno: conexiones, claves y zona horaria.

Se lee de las variables de entorno o del archivo .env. Aquí solo va lo técnico.
Los parámetros del negocio (horario, tiempo de gracia, precios...) viven en la base de datos,
en la tabla `configuracion`, para que el administrador los cambie sin tocar código.
"""

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Solo sirve en tu computador. En producción el sistema no arranca con esta clave.
CLAVE_DE_DESARROLLO = "solo-para-desarrollo-no-usar-en-produccion"


class ConfiguracionEntorno(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://auto:auto@localhost:5433/auto_ibaguer"
    zona_horaria: str = "America/Bogota"
    entorno: str = "desarrollo"
    # Firma la sesión del dashboard: quien la conozca podría hacerse pasar por el
    # administrador, así que en producción es larga, aleatoria y secreta.
    clave_secreta: str = CLAVE_DE_DESARROLLO
    # Horas sin usar el dashboard para que la sesión venza y pida la contraseña otra vez.
    sesion_horas: int = 12

    @property
    def es_produccion(self) -> bool:
        return self.entorno == "produccion"

    @model_validator(mode="after")
    def clave_segura_en_produccion(self) -> "ConfiguracionEntorno":
        if self.es_produccion and (
            self.clave_secreta == CLAVE_DE_DESARROLLO or len(self.clave_secreta) < 32
        ):
            raise ValueError(
                "En producción CLAVE_SECRETA debe tener al menos 32 caracteres aleatorios. "
                'Genera una con: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self

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
