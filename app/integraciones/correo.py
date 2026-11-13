"""Mandar un correo por SMTP: el reporte diario del parqueadero a los jefes.

Sirve cualquier cuenta de correo con SMTP. Con Gmail: servidor smtp.gmail.com, puerto 465 y
una "contraseña de aplicación" (no la contraseña normal). Los datos van en el entorno
(CORREO_SERVIDOR, CORREO_PUERTO, CORREO_USUARIO, CORREO_CLAVE), nunca en el código. Sin
CORREO_SERVIDOR no se manda nada.

Ojo: el plan gratis de Render bloquea el correo (los puertos de SMTP); en el plan de pago sí
sale.
"""

import logging
import smtplib
import ssl
from collections.abc import Sequence
from email.message import EmailMessage
from email.utils import formataddr

from app.config import ConfiguracionEntorno, obtener_entorno
from app.errores import ReglaDelNegocio

logger = logging.getLogger(__name__)

ESPERA_SEGUNDOS = 20


class CorreoNoEnviado(ReglaDelNegocio):
    """No salió el correo; el mensaje dice por qué, en palabras para el administrador."""


def enviar(
    asunto: str,
    texto: str,
    destinos: Sequence[str],
    *,
    nombre_remitente: str = "Auto Ibaguer",
    entorno: ConfiguracionEntorno | None = None,
) -> None:
    entorno = entorno or obtener_entorno()
    if not entorno.correo_configurado:
        raise CorreoNoEnviado(
            "El correo del sistema no está configurado (faltan los datos CORREO_ en el servidor)."
        )
    if not destinos:
        raise CorreoNoEnviado("No hay correos a quién mandarlo: escríbelos en Tarifas y ticket.")
    mensaje = EmailMessage()
    mensaje["Subject"] = asunto
    mensaje["From"] = formataddr((nombre_remitente, entorno.correo_remitente_o_usuario))
    mensaje["To"] = ", ".join(destinos)
    mensaje.set_content(texto)
    try:
        with _conectar(entorno) as smtp:
            if entorno.correo_usuario and entorno.correo_clave:
                smtp.login(entorno.correo_usuario, entorno.correo_clave)
            smtp.send_message(mensaje)
    except smtplib.SMTPAuthenticationError as error:
        raise CorreoNoEnviado(
            "El servidor de correo no aceptó el usuario o la contraseña. Con Gmail se usa "
            "una contraseña de aplicación, no la contraseña normal."
        ) from error
    except (OSError, smtplib.SMTPException) as error:
        # Sin el detalle del error en el registro: podría traer direcciones de correo.
        logger.warning("No se pudo mandar un correo: %s", type(error).__name__)
        raise CorreoNoEnviado(
            "No se pudo conectar con el servidor de correo. Si el sistema está en el plan "
            "gratis de Render, el correo está bloqueado: sale cuando pase al plan de pago."
        ) from error
    logger.info("Correo enviado a %d destinos.", len(destinos))


def _conectar(entorno: ConfiguracionEntorno) -> smtplib.SMTP:
    """Puerto 465: conexión cifrada desde el comienzo (Gmail). Otro puerto (587, por
    ejemplo): se cifra con STARTTLS si el servidor lo ofrece. La contraseña nunca viaja sin
    cifrar: sin cifrado, solo un servidor sin contraseña (uno de prueba en tu computador)."""
    servidor, puerto = entorno.correo_servidor, entorno.correo_puerto
    if puerto == 465:
        return smtplib.SMTP_SSL(
            servidor, puerto, timeout=ESPERA_SEGUNDOS, context=ssl.create_default_context()
        )
    smtp = smtplib.SMTP(servidor, puerto, timeout=ESPERA_SEGUNDOS)
    try:
        smtp.ehlo()
        if smtp.has_extn("starttls"):
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        elif entorno.correo_usuario and entorno.correo_clave:
            raise CorreoNoEnviado(
                "El servidor de correo no ofrece conexión cifrada y así no se manda la "
                "contraseña. Usa el puerto 465 o el 587."
            )
    except BaseException:
        smtp.close()
        raise
    return smtp
