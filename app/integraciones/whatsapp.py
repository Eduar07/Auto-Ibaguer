"""WhatsApp Cloud API de Meta: enviar lo que dice el bot y leer lo que Meta nos avisa.

- Salida: `WhatsApp` es el que envía los mensajes de la bandeja de salida
  (app.integraciones.bandeja_salida). Arma cada uno como lo pide Meta (texto, botones o
  lista) y lo envía por HTTPS. Si Meta no lo acepta, dice si vale la pena reintentar.
- Entrada: `leer_aviso_de_meta` convierte lo que Meta manda al webhook (app.web.webhook)
  en mensajes de clientes y en avisos de mensajes que no se pudieron entregar.

Los datos de conexión (token, número, clave de la app) vienen del .env (app.config).
"""

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.bot.avisos import mensaje_del_aviso
from app.bot.mensajes import Mensaje
from app.catalogo.parametros import leer_parametros
from app.clientes.reglas import es_de_prueba
from app.config import ConfiguracionEntorno, obtener_entorno
from app.integraciones.bandeja_salida import TIPO_DEL_ADMINISTRADOR, TIPO_RESPUESTA, EnvioFallido
from app.integraciones.tablas import MensajeEntrante, MensajeSaliente
from app.turnos.estados import TipoAviso

logger = logging.getLogger(__name__)

URL_DE_META = "https://graph.facebook.com"
# WhatsApp solo deja escribirle libremente a quien le escribió al negocio en las últimas
# 24 horas. Por fuera de esa ventana solo se pueden mandar plantillas aprobadas (Fase 3).
VENTANA_PARA_ESCRIBIR = timedelta(hours=24)
# Errores de Meta que se arreglan solos: se reintenta. Los demás (número sin WhatsApp,
# fuera de la ventana de 24 horas, datos mal armados...) no se arreglan reintentando.
ERRORES_PASAJEROS = frozenset({1, 2, 4, 80007, 130429, 131000, 131016, 131056})
# El token venció o lo revocaron: hay que cambiarlo en el .env. Se reintenta por si se
# arregla pronto, y se avisa en el registro del servidor.
TOKEN_QUE_NO_SIRVE = 190


# --- La firma de Meta ------------------------------------------------------------------------


def firma_valida(cuerpo: bytes, firma: str | None, secreto: str) -> bool:
    """Meta firma cada aviso con la clave secreta de la app (encabezado X-Hub-Signature-256).

    La firma es una huella (HMAC-SHA256) del aviso hecha con esa clave: si alguien que no
    es Meta manda un aviso, no puede firmarlo. Se compara sin atajos (compare_digest) para
    que el tiempo de la comparación no revele nada.
    """
    if not secreto or not firma or not firma.startswith("sha256="):
        return False
    esperada = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma.removeprefix("sha256="))


# --- Lo que manda Meta -----------------------------------------------------------------------


@dataclass(frozen=True)
class Recibido:
    """Un mensaje de un cliente, como lo trajo Meta."""

    id_meta: str  # Meta le pone un id único: si lo manda dos veces, el repetido se ignora
    telefono: str
    tipo: str  # text, interactive (tocó un botón), image, audio...
    texto: str | None = None
    opcion: str | None = None  # el id del botón o de la opción de la lista que tocó
    titulo: str | None = None  # lo que decía ese botón
    ignorar: bool = False  # reacciones y avisos del sistema: se guardan, pero no se contestan


@dataclass(frozen=True)
class NoEntregado:
    """Un mensaje que WhatsApp aceptó pero después no pudo entregar."""

    id_meta: str
    motivo: str


# Lo que llega y no merece respuesta: una reacción (👍) a un mensaje del bot, o un aviso
# del sistema (por ejemplo, que el cliente cambió de número).
_IGNORADOS = frozenset({"reaction", "system", "unsupported", "ephemeral", "request_welcome"})
_TELEFONO = re.compile(r"[0-9]{8,15}")


def leer_aviso_de_meta(
    datos: dict[str, Any], id_numero: str
) -> tuple[list[Recibido], list[NoEntregado]]:
    """Los mensajes nuevos y los no entregados que trae un aviso de Meta.

    Solo se lee lo que es para nuestro número (`id_numero`). Lo demás se descarta.
    """
    recibidos: list[Recibido] = []
    no_entregados: list[NoEntregado] = []
    for entrada in _lista(datos.get("entry")):
        for cambio in _lista(entrada.get("changes")):
            valor = cambio.get("value") or {}
            if cambio.get("field") != "messages":
                continue
            if (valor.get("metadata") or {}).get("phone_number_id") != id_numero:
                continue
            for mensaje in _lista(valor.get("messages")):
                recibido = _leer_mensaje(mensaje)
                if recibido is not None:
                    recibidos.append(recibido)
            for estado in _lista(valor.get("statuses")):
                if estado.get("status") == "failed" and estado.get("id"):
                    no_entregados.append(NoEntregado(estado["id"], _motivo(estado)))
    return recibidos, no_entregados


def _lista(valor: Any) -> list[dict[str, Any]]:
    return [item for item in valor if isinstance(item, dict)] if isinstance(valor, list) else []


def _leer_mensaje(mensaje: dict[str, Any]) -> Recibido | None:
    id_meta, telefono, tipo = mensaje.get("id"), mensaje.get("from"), mensaje.get("type", "")
    if not id_meta or not isinstance(telefono, str) or not _TELEFONO.fullmatch(telefono):
        return None
    if tipo == "text":
        return Recibido(id_meta, telefono, tipo, texto=(mensaje.get("text") or {}).get("body"))
    if tipo == "interactive":
        interactivo = mensaje.get("interactive") or {}
        respuesta = interactivo.get("button_reply") or interactivo.get("list_reply") or {}
        return Recibido(
            id_meta, telefono, tipo, opcion=respuesta.get("id"), titulo=respuesta.get("title")
        )
    if tipo == "button":  # el botón de una plantilla: se entiende como si lo escribiera
        return Recibido(id_meta, telefono, tipo, texto=(mensaje.get("button") or {}).get("text"))
    return Recibido(id_meta, telefono, tipo, ignorar=tipo in _IGNORADOS)


def _motivo(estado: dict[str, Any]) -> str:
    errores = _lista(estado.get("errors"))
    if not errores:
        return "WhatsApp no pudo entregarlo."
    error = errores[0]
    return f"WhatsApp no pudo entregarlo (error {error.get('code')}: {error.get('title', '')})."[
        :500
    ]


# --- Enviar ----------------------------------------------------------------------------------


def contenido_para_meta(mensaje: Mensaje, telefono: str) -> dict[str, Any]:
    """El mensaje del bot como lo pide la Cloud API: texto, texto con botones, o con lista."""
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": telefono}
    if mensaje.botones:
        botones = [
            {"type": "reply", "reply": {"id": boton.id, "title": boton.titulo}}
            for boton in mensaje.botones
        ]
        interactivo = {
            "type": "button",
            "body": {"text": mensaje.texto},
            "action": {"buttons": botones},
        }
        return {**base, "type": "interactive", "interactive": interactivo}
    if mensaje.lista is not None:
        filas = [
            {
                "id": o.id,
                "title": o.titulo,
                **({"description": o.descripcion} if o.descripcion else {}),
            }
            for o in mensaje.lista.opciones
        ]
        interactivo = {
            "type": "list",
            "body": {"text": mensaje.texto},
            "action": {
                "button": mensaje.lista.boton,
                "sections": [{"title": "Opciones", "rows": filas}],
            },
        }
        return {**base, "type": "interactive", "interactive": interactivo}
    return {**base, "type": "text", "text": {"preview_url": False, "body": mensaje.texto}}


def mensaje_del_bot(sesion: Session, mensaje: MensajeSaliente) -> Mensaje:
    """Lo que dice un mensaje de la bandeja de salida: una respuesta, un aviso de la fila o
    lo que escribió el administrador."""
    if mensaje.tipo == TIPO_RESPUESTA:
        return Mensaje.desde_datos(mensaje.contenido)
    if mensaje.tipo == TIPO_DEL_ADMINISTRADOR:
        return Mensaje(str(mensaje.contenido["texto"]))
    if mensaje.tipo in {tipo.value for tipo in TipoAviso}:
        return mensaje_del_aviso(mensaje.tipo, mensaje.contenido, leer_parametros(sesion))
    raise EnvioFallido(f"No se sabe enviar un mensaje de tipo {mensaje.tipo!r}.", reintentar=False)


def le_escribio_hace_poco(sesion: Session, telefono: str) -> bool:
    """¿Esa persona le escribió al bot en las últimas 24 horas? Si no, WhatsApp no deja."""
    ultimo = sesion.scalar(
        select(func.max(MensajeEntrante.recibido_en)).where(MensajeEntrante.telefono == telefono)
    )
    return ultimo is not None and ultimo >= tiempo.ahora() - VENTANA_PARA_ESCRIBIR


class WhatsApp:
    """El que envía los mensajes de la bandeja de salida por la Cloud API de Meta."""

    def __init__(self, entorno: ConfiguracionEntorno, http: httpx.Client | None = None) -> None:
        self.url = (
            f"{URL_DE_META}/{entorno.whatsapp_version_api}/{entorno.whatsapp_id_numero}/messages"
        )
        self._encabezados = {"Authorization": f"Bearer {entorno.whatsapp_token}"}
        # Si Meta no contesta en 10 segundos, se deja para el próximo intento.
        self.http = http or httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0))

    def enviar(self, sesion: Session, mensaje: MensajeSaliente) -> str:
        if es_de_prueba(mensaje.telefono):
            raise EnvioFallido(
                "Es un celular de prueba (Probar el bot): no sale por WhatsApp.",
                reintentar=False,
            )
        if not le_escribio_hace_poco(sesion, mensaje.telefono):
            raise EnvioFallido(
                "No le ha escrito al bot en las últimas 24 horas: WhatsApp no deja escribirle.",
                reintentar=False,
            )
        contenido = contenido_para_meta(mensaje_del_bot(sesion, mensaje), mensaje.telefono)
        try:
            respuesta = self.http.post(self.url, json=contenido, headers=self._encabezados)
        except httpx.TimeoutException as error:
            raise EnvioFallido("WhatsApp no respondió a tiempo.", reintentar=True) from error
        except httpx.HTTPError as error:
            raise EnvioFallido(
                f"No hubo conexión con WhatsApp ({type(error).__name__}).", reintentar=True
            ) from error
        if respuesta.is_success:
            return _id_del_mensaje(respuesta)
        raise _por_que_no(respuesta)


def _id_del_mensaje(respuesta: httpx.Response) -> str:
    try:
        return str(respuesta.json()["messages"][0]["id"])
    except (ValueError, KeyError, IndexError, TypeError):
        return "sin-id"  # lo aceptó, aunque no devolvió el id


def _por_que_no(respuesta: httpx.Response) -> EnvioFallido:
    try:
        error = respuesta.json().get("error") or {}
    except ValueError:
        error = {}
    codigo = error.get("code")
    detalle = (error.get("error_data") or {}).get("details") or error.get("message") or ""
    motivo = f"WhatsApp respondió {respuesta.status_code}"
    if codigo is not None:
        motivo += f" (error {codigo})"
    if detalle:
        motivo += f": {detalle}"
    if codigo == TOKEN_QUE_NO_SIRVE:
        logger.error(
            "WhatsApp no acepta el token: venció o lo revocaron. Cámbialo en WHATSAPP_TOKEN."
        )
    pasajero = (
        codigo in ERRORES_PASAJEROS
        or codigo == TOKEN_QUE_NO_SIRVE
        or respuesta.status_code == 429
        or respuesta.status_code >= 500
    )
    return EnvioFallido(motivo[:500], reintentar=pasajero)


# --- Registrar el número (una sola vez, el día que llega la SIM) -----------------------------


class RegistroFallido(Exception):
    """Meta no registró el número; el mensaje dice por qué."""


def registrar_numero(
    entorno: ConfiguracionEntorno, pin: str, http: httpx.Client | None = None
) -> None:
    """Registra el número del bot en la Cloud API. Meta solo deja hacerlo por la API.

    Se hace una vez, después de agregar y verificar el número en el Administrador de
    WhatsApp, y otra vez si Meta aprueba un cambio del nombre que ven los clientes. El PIN
    es el de la verificación en dos pasos: si el número no tenía, queda ese.
    """
    url = f"{URL_DE_META}/{entorno.whatsapp_version_api}/{entorno.whatsapp_id_numero}/register"
    cliente = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))
    try:
        respuesta = cliente.post(
            url,
            json={"messaging_product": "whatsapp", "pin": pin},
            headers={"Authorization": f"Bearer {entorno.whatsapp_token}"},
        )
    except httpx.HTTPError as error:
        raise RegistroFallido(f"No hubo conexión con WhatsApp ({type(error).__name__}).") from error
    if not respuesta.is_success:
        raise RegistroFallido(str(_por_que_no(respuesta)))


def enlazar_la_app(entorno: ConfiguracionEntorno, http: httpx.Client | None = None) -> list[str]:
    """Enlaza la app con la cuenta de WhatsApp Business (WABA) para que Meta le avise de cada
    mensaje al webhook, y devuelve los nombres de las apps enlazadas.

    Meta muchas veces no lo hace solo: la configuración del webhook se ve bien, el botón de
    prueba de Meta funciona, pero los mensajes de verdad nunca llegan. Enlazarla otra vez no
    daña nada (Meta contesta lo mismo), así que el comando lo hace siempre.
    """
    url = (
        f"{URL_DE_META}/{entorno.whatsapp_version_api}/{entorno.whatsapp_id_cuenta}/subscribed_apps"
    )
    cliente = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))
    encabezados = {"Authorization": f"Bearer {entorno.whatsapp_token}"}
    try:
        enlace = cliente.post(url, headers=encabezados)
        if not enlace.is_success:
            raise RegistroFallido(str(_por_que_no(enlace)))
        enlazadas = cliente.get(url, headers=encabezados)
    except httpx.HTTPError as error:
        raise RegistroFallido(f"No hubo conexión con WhatsApp ({type(error).__name__}).") from error
    if not enlazadas.is_success:
        raise RegistroFallido(str(_por_que_no(enlazadas)))
    try:
        datos = enlazadas.json().get("data") or []
    except (ValueError, AttributeError):
        datos = []
    return [
        str((app.get("whatsapp_business_api_data") or {}).get("name") or "sin nombre")
        for app in datos
        if isinstance(app, dict)
    ]


@lru_cache
def del_entorno() -> WhatsApp | None:
    """El enviador de WhatsApp, si el .env tiene los datos para conectarse. Si no, None."""
    entorno = obtener_entorno()
    return WhatsApp(entorno) if entorno.whatsapp_conectado else None
