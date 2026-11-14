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


def con_psycopg(url: str) -> str:
    """Los servicios en la nube entregan la URL como postgres:// o postgresql://.

    SQLAlchemy necesita saber con qué conector hablar; aquí siempre es psycopg.
    """
    for prefijo in ("postgres://", "postgresql://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url.removeprefix(prefijo)
    return url


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
    # El reloj (app.reloj) corre dentro del servidor web y cada 30 segundos revisa lo que
    # vence con el tiempo. En la nube va siempre encendido; en tu computador lo puedes
    # apagar para mirar algo sin que el reloj mueva la fila.
    reloj_activo: bool = True

    # --- WhatsApp (Cloud API de Meta). Sin estos datos el bot no se conecta a WhatsApp,
    # y todo lo demás funciona igual. Son secretos: solo van en el .env, nunca en GitHub.
    # El token permanente del usuario del sistema (no el temporal, que vence en un día).
    whatsapp_token: str = ""
    # El "identificador del número de teléfono" que da Meta (no es el número mismo).
    whatsapp_id_numero: str = ""
    # El "identificador de la cuenta de WhatsApp Business" (WABA). Solo lo usa el comando
    # registrar-whatsapp, para enlazar la app con la cuenta: sin eso, Meta no avisa.
    whatsapp_id_cuenta: str = ""
    # La clave secreta de la app de Meta: con ella se revisa que cada mensaje venga de Meta.
    whatsapp_secreto_app: str = ""
    # Una palabra que inventas tú y escribes también en Meta al registrar el webhook.
    whatsapp_token_verificacion: str = ""
    whatsapp_version_api: str = "v26.0"

    # --- Gemini (Google). Entiende lo que el cliente escribe con sus palabras. Sin la
    # clave, el bot funciona igual con botones y unas pocas palabras. Es secreta.
    gemini_clave: str = ""
    # Si el proyecto de Google tiene facturación (plan de pago). En el plan gratis Google
    # usa los mensajes para mejorar sus productos y personas los pueden leer: en la nube,
    # con clientes de verdad, Gemini solo se usa con el plan de pago.
    gemini_plan_de_pago: bool = False

    # --- Correo, para el reporte diario de los dos negocios (app.integraciones.correo). Por
    # ejemplo un Gmail: smtp.gmail.com, puerto 465 y una "contraseña de aplicación". Sin el
    # servidor no se manda correo; la clave es secreta: solo en el .env o en Render.
    correo_servidor: str = ""
    correo_puerto: int = 465
    correo_usuario: str = ""
    correo_clave: str = ""
    correo_remitente: str = ""  # vacío: el mismo usuario

    @property
    def es_produccion(self) -> bool:
        return self.entorno == "produccion"

    @property
    def correo_configurado(self) -> bool:
        return bool(self.correo_servidor)

    @property
    def correo_remitente_o_usuario(self) -> str:
        return self.correo_remitente or self.correo_usuario or "parqueadero@localhost"

    @property
    def whatsapp_conectado(self) -> bool:
        """Están los datos para hablar con WhatsApp."""
        return bool(self.whatsapp_token and self.whatsapp_id_numero and self.whatsapp_secreto_app)

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
        return con_psycopg(url)


@lru_cache
def obtener_entorno() -> ConfiguracionEntorno:
    """Devuelve la configuración del entorno (se lee una sola vez)."""
    return ConfiguracionEntorno()
