"""WhatsApp Cloud API de Meta: enviar lo que dice el bot y leer lo que Meta nos avisa.

- Salida: `WhatsApp` es el que envía los mensajes de la bandeja de salida
  (app.integraciones.bandeja_salida). Arma cada uno como lo pide Meta (texto, botones,
  lista o el video de un servicio) y lo envía por HTTPS. Si Meta no lo acepta, dice si vale
  la pena reintentar. Los videos se le suben a Meta antes (`subir_video`) y se mandan con
  el id que devuelve.
- Entrada: `leer_aviso_de_meta` convierte lo que Meta manda al webhook (app.web.webhook)
  en mensajes de clientes y en avisos de mensajes que no se pudieron entregar. De una
  foto o un audio llega solo el id: `descargar` la trae de Meta para que Gemini la vea o
  la oiga, y no se guarda.
- Mientras el bot piensa, `avisar_que_escribe` le muestra al cliente el chulito azul de
  leído y el "escribiendo…", como cuando contesta una persona.

Los datos de conexión (token, número, clave de la app) vienen del .env (app.config).
"""

import hashlib
import hmac
import logging
import re
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import lru_cache
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.bot.avisos import mensaje_del_aviso
from app.bot.mensajes import Adjunto, Mensaje, tipo_de_archivo
from app.catalogo import videos
from app.catalogo.parametros import leer_parametros
from app.clientes.reglas import es_de_prueba, es_usuario_de_whatsapp
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
    # Su celular (573001234567) o, si Meta no lo comparte (tiene nombre de usuario), su id
    # de usuario de WhatsApp ("CO.1349…"): con cualquiera de los dos se le contesta.
    telefono: str
    tipo: str  # text, interactive (tocó un botón), image, audio...
    texto: str | None = None
    opcion: str | None = None  # el id del botón o de la opción de la lista que tocó
    titulo: str | None = None  # lo que decía ese botón
    ignorar: bool = False  # reacciones y avisos del sistema: se guardan, pero no se contestan
    # De una foto o un audio: el id para descargarlo de Meta y su tipo ("image/jpeg").
    archivo: str | None = None
    mime: str | None = None
    # Su id de usuario de WhatsApp cuando también vino el celular: así la conversación que
    # empezó sin celular sigue con el mismo cliente.
    usuario: str | None = None


@dataclass(frozen=True)
class NoEntregado:
    """Un mensaje que WhatsApp aceptó pero después no pudo entregar."""

    id_meta: str
    motivo: str


# Lo que llega y no merece respuesta: una reacción (👍) o un sticker después de un mensaje
# del bot (una persona tampoco le contesta un sticker), o un aviso del sistema (por
# ejemplo, que el cliente cambió de número).
_IGNORADOS = frozenset(
    {"reaction", "sticker", "system", "unsupported", "ephemeral", "request_welcome"}
)
# Lo que puede traer un texto debajo (el pie de foto).
_CON_PIE = frozenset({"image", "video", "document"})
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
            para = (valor.get("metadata") or {}).get("phone_number_id")
            if para != id_numero:
                # Un mensaje para otro número de la cuenta (o WHATSAPP_ID_NUMERO mal
                # copiado): se descarta, pero que quede rastro. El id del número no es un
                # dato de nadie.
                logger.warning(
                    "Llegó un aviso de WhatsApp para el número %s, y el del bot es %s: no se "
                    "atiende. Si es el del bot, revisa WHATSAPP_ID_NUMERO.",
                    para,
                    id_numero,
                )
                continue
            mensajes, estados = _lista(valor.get("messages")), _lista(valor.get("statuses"))
            # Qué trajo cada aviso, sin datos de nadie: así en el registro se ve si llegó
            # un mensaje o solo el estado de una respuesta (enviado, leído, fallido).
            logger.info(
                "Aviso de WhatsApp: %d mensaje(s) y %d estado(s)%s.",
                len(mensajes),
                len(estados),
                f" ({', '.join(sorted({str(e.get('status')) for e in estados}))})"
                if estados
                else "",
            )
            for mensaje in mensajes:
                recibido = _leer_mensaje(mensaje)
                if recibido is not None:
                    recibidos.append(recibido)
                else:
                    _avisar_descartado(mensaje)
            for estado in estados:
                if estado.get("status") == "failed" and estado.get("id"):
                    motivo = _motivo(estado)
                    logger.warning("WhatsApp no entregó una respuesta del bot: %s", motivo)
                    no_entregados.append(NoEntregado(estado["id"], motivo))
    return recibidos, no_entregados


def _avisar_descartado(mensaje: dict[str, Any]) -> None:
    """Un mensaje que no se puede atender deja rastro: por qué y qué campos traía (los
    nombres de los campos, nunca su contenido)."""
    logger.warning(
        "Llegó un mensaje de WhatsApp que no se puede atender: %s. Campos: %s.",
        por_que_se_descarta(mensaje),
        ", ".join(campos(mensaje)),
    )


def campos(mensaje: dict[str, Any]) -> list[str]:
    """Los nombres de los campos que trajo un mensaje (nunca lo que dicen)."""
    return sorted(str(clave) for clave in mensaje)


def leer_mensaje(mensaje: dict[str, Any]) -> Recibido | None:
    """Un mensaje del aviso de Meta, o None si no se puede atender (por_que_se_descarta)."""
    return _leer_mensaje(mensaje)


def por_que_se_descarta(mensaje: dict[str, Any]) -> str:
    """Por qué no se puede atender un mensaje que llegó de Meta."""
    remitente = mensaje.get("from")
    if not mensaje.get("id"):
        por_que = "no trae id"
    elif remitente is None and mensaje.get("from_user_id"):
        por_que = (
            "no trae el celular del cliente y su usuario de WhatsApp no tiene la forma de siempre"
        )
    elif not isinstance(remitente, str) or not _TELEFONO.fullmatch(remitente):
        por_que = "el remitente no es un número de celular"
    else:
        por_que = "no se entiende"
    return por_que


def _lista(valor: Any) -> list[dict[str, Any]]:
    return [item for item in valor if isinstance(item, dict)] if isinstance(valor, list) else []


def _leer_mensaje(mensaje: dict[str, Any]) -> Recibido | None:
    id_meta, tipo = mensaje.get("id"), mensaje.get("type", "")
    telefono = _quien(mensaje)
    if not id_meta or telefono is None:
        return None
    recibido = _el_mensaje(mensaje, id_meta, telefono, tipo)
    usuario = mensaje.get("from_user_id")
    if recibido is not None and es_usuario_de_whatsapp(usuario) and usuario != telefono:
        return replace(recibido, usuario=usuario)
    return recibido


def _quien(mensaje: dict[str, Any]) -> str | None:
    """El celular; si Meta no lo manda, el id de usuario de WhatsApp. None si ninguno sirve."""
    celular, usuario = mensaje.get("from"), mensaje.get("from_user_id")
    if isinstance(celular, str) and _TELEFONO.fullmatch(celular):
        return celular
    if isinstance(usuario, str) and es_usuario_de_whatsapp(usuario):
        return usuario
    return None


def _el_mensaje(mensaje: dict[str, Any], id_meta: str, telefono: str, tipo: str) -> Recibido | None:
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
    archivo = mensaje.get(tipo) if isinstance(mensaje.get(tipo), dict) else {}
    pie = archivo.get("caption") if tipo in _CON_PIE else None
    mime = archivo.get("mime_type")
    media_id = archivo.get("id")
    se_puede_ver = (
        tipo not in _IGNORADOS and isinstance(media_id, str) and tipo_de_archivo(mime) is not None
    )
    return Recibido(
        id_meta,
        telefono,
        tipo,
        texto=pie if isinstance(pie, str) and pie.strip() else None,
        ignorar=tipo in _IGNORADOS,
        archivo=media_id if se_puede_ver else None,
        mime=mime if se_puede_ver else None,
    )


def motivo_del_estado(estado: dict[str, Any]) -> str:
    return _motivo(estado)


def _motivo(estado: dict[str, Any]) -> str:
    errores = _lista(estado.get("errors"))
    if not errores:
        return "WhatsApp no pudo entregarlo."
    error = errores[0]
    return f"WhatsApp no pudo entregarlo (error {error.get('code')}: {error.get('title', '')})."[
        :500
    ]


# --- Enviar ----------------------------------------------------------------------------------


def contenido_para_meta(
    mensaje: Mensaje, telefono: str, id_del_video: str | None = None
) -> dict[str, Any]:
    """El mensaje del bot como lo pide la Cloud API: texto, texto con botones, con lista, o
    el video de un servicio (con `id_del_video`, el id con que Meta lo tiene guardado)."""
    # A quien no comparte su celular se le escribe con su id de usuario: va en
    # "recipient", no en "to".
    destino = "recipient" if es_usuario_de_whatsapp(telefono) else "to"
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", destino: telefono}
    if mensaje.video is not None:
        if not id_del_video:
            raise ValueError("Para mandar un video hace falta el id con que Meta lo tiene.")
        return {**base, "type": "video", "video": {"id": id_del_video, "caption": mensaje.texto}}
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


# Lo más pesado que se le muestra a Gemini: una foto de WhatsApp pesa menos de 5 MB, y una
# nota de voz de unos minutos, mucho menos. Más que eso no se descarga.
PESO_MAXIMO = 5 * 1024 * 1024
# Subir un video de hasta 16 MB puede tardar: se le da más tiempo que a un mensaje.
ESPERA_AL_SUBIR = httpx.Timeout(90.0, connect=5.0)


class WhatsApp:
    """El que envía los mensajes de la bandeja de salida por la Cloud API de Meta."""

    def __init__(self, entorno: ConfiguracionEntorno, http: httpx.Client | None = None) -> None:
        self.url_de_la_api = f"{URL_DE_META}/{entorno.whatsapp_version_api}"
        self.url = f"{self.url_de_la_api}/{entorno.whatsapp_id_numero}/messages"
        self.url_de_los_archivos = f"{self.url_de_la_api}/{entorno.whatsapp_id_numero}/media"
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
        del_bot = mensaje_del_bot(sesion, mensaje)
        if del_bot.video is None:
            return self._mandar(contenido_para_meta(del_bot, mensaje.telefono))
        # Un video es un extra: si no sale, no se reintenta. Así no frena lo que el bot le
        # dice después al mismo cliente (sus mensajes salen en orden).
        try:
            id_del_video = self._id_del_video(sesion, del_bot.video)
            return self._mandar(contenido_para_meta(del_bot, mensaje.telefono, id_del_video))
        except EnvioFallido as error:
            raise EnvioFallido(str(error), reintentar=False) from error

    def _mandar(self, contenido: dict[str, Any]) -> str:
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

    def _id_del_video(self, sesion: Session, servicio_id: int) -> str:
        """El id con que Meta tiene el video del servicio. Si no lo tiene (o se le venció),
        se lo sube ahora; casi siempre ya lo subió el reloj."""
        ahora = tiempo.ahora()
        video = videos.del_servicio(sesion, servicio_id)
        if video is None:
            raise EnvioFallido("Ese servicio ya no tiene video.", reintentar=False)
        vigente = videos.id_vigente(video, ahora)
        if vigente:
            return vigente
        # Leer `contenido` trae el archivo de la base (no se lee si no hace falta).
        id_en_meta = self.subir_video(video.contenido, video.tipo, video.nombre)
        videos.anotar_subida(video, id_en_meta, ahora)
        sesion.flush()
        return id_en_meta

    def subir_video(self, contenido: bytes, tipo: str, nombre: str) -> str:
        """Le sube el video a Meta y devuelve el id con que lo guarda (le sirve 30 días)."""
        try:
            respuesta = self.http.post(
                self.url_de_los_archivos,
                headers=self._encabezados,
                data={"messaging_product": "whatsapp", "type": tipo},
                files={"file": (nombre, contenido, tipo)},
                timeout=ESPERA_AL_SUBIR,
            )
        except httpx.TimeoutException as error:
            raise EnvioFallido("WhatsApp no recibió el video a tiempo.", reintentar=True) from error
        except httpx.HTTPError as error:
            raise EnvioFallido(
                f"No hubo conexión con WhatsApp para subir el video ({type(error).__name__}).",
                reintentar=True,
            ) from error
        if not respuesta.is_success:
            raise _por_que_no(respuesta)
        try:
            return str(respuesta.json()["id"])
        except (ValueError, KeyError, TypeError) as error:
            raise EnvioFallido(
                "WhatsApp recibió el video pero no dio su id.", reintentar=True
            ) from error

    def avisar_que_escribe(self, id_meta: str) -> None:
        """El chulito azul de leído y el "escribiendo…" mientras el bot piensa la respuesta.
        WhatsApp lo quita al llegar la respuesta (o a los 25 segundos). Si falla, no pasa
        nada: es un detalle, no un mensaje."""
        contenido = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": id_meta,
            "typing_indicator": {"type": "text"},
        }
        try:
            respuesta = self.http.post(self.url, json=contenido, headers=self._encabezados)
        except httpx.HTTPError as error:
            logger.info("No se pudo mostrar el escribiendo (%s).", type(error).__name__)
            return
        if not respuesta.is_success:
            logger.info("No se pudo mostrar el escribiendo (%s).", respuesta.status_code)

    def descargar(self, media_id: str) -> Adjunto | None:
        """La foto o el audio que mandó el cliente, para que Gemini lo vea o lo oiga. Meta
        da primero un enlace (dura 5 minutos) y después el archivo, los dos con el token.
        None si no se pudo o si pesa demasiado: el bot sigue como si no lo hubiera visto."""
        try:
            datos = self.http.get(f"{self.url_de_la_api}/{media_id}", headers=self._encabezados)
            if not datos.is_success:
                return _sin_archivo(f"Meta no dio el enlace ({datos.status_code})")
            info = datos.json()
            enlace, mime = info.get("url"), str(info.get("mime_type") or "")
            if not isinstance(enlace, str) or tipo_de_archivo(mime) is None:
                return _sin_archivo("Meta no dio un enlace o el tipo no se puede ver")
            if int(info.get("file_size") or 0) > PESO_MAXIMO:
                return _sin_archivo("pesa demasiado")
            archivo = self.http.get(enlace, headers=self._encabezados, follow_redirects=True)
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
            return _sin_archivo(type(error).__name__)
        if not archivo.is_success:
            return _sin_archivo(f"Meta no entregó el archivo ({archivo.status_code})")
        if len(archivo.content) > PESO_MAXIMO:
            return _sin_archivo("pesa demasiado")
        return Adjunto(mime, archivo.content)


def _sin_archivo(motivo: str) -> None:
    logger.warning("No se pudo descargar una foto o un audio de WhatsApp: %s.", motivo)
    return None


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
