"""La bandeja de entrada: cada mensaje de WhatsApp se guarda antes de atenderlo.

1. Meta avisa al webhook (app.web.webhook): cada mensaje se guarda (si Meta repite uno,
   el repetido se ignora) y se le contesta a Meta de una vez, para que no lo reintente.
2. Aparte se atiende: la conversación del bot decide qué contestar y las respuestas quedan
   en la bandeja de salida en la misma transacción. Después salen.
3. Si el servidor se cae entre 1 y 2, el reloj encuentra los que quedaron sin atender.

Los de un mismo teléfono se atienden en orden y de a uno: si el cliente toca dos botones
seguidos, el segundo espera a que el primero termine.
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import tiempo
from app.bot import conversacion
from app.bot.mensajes import Entrada, Mensaje
from app.bot.textos import texto
from app.catalogo.parametros import leer_parametros
from app.clientes import servicio as clientes
from app.db.sesion import AbrirSesion
from app.formato import telefono as telefono_legible
from app.integraciones import bandeja_salida, gemini
from app.integraciones.bandeja_salida import Enviador
from app.integraciones.tablas import MensajeEntrante
from app.integraciones.whatsapp import Recibido
from app.turnos import avisos

logger = logging.getLogger(__name__)

# Un mensaje sin atender con más de esto se da por abandonado (el servidor se cayó) y lo
# atiende el reloj. Antes, lo está atendiendo el webhook.
ESPERA_ANTES_DE_RETOMAR = timedelta(minutes=1)


def guardar(sesion: Session, recibidos: Iterable[Recibido], *, ahora: datetime) -> set[str]:
    """Guarda los mensajes nuevos. Devuelve los teléfonos que tienen algo por contestar."""
    por_atender: set[str] = set()
    for recibido in recibidos:
        nuevo = sesion.execute(
            insert(MensajeEntrante)
            .values(
                id_meta=recibido.id_meta,
                telefono=recibido.telefono,
                tipo=recibido.tipo,
                contenido=_lo_que_dice(recibido),
                recibido_en=ahora,
                # Una reacción no se contesta: queda atendida de una vez.
                procesado_en=ahora if recibido.ignorar else None,
            )
            .on_conflict_do_nothing(index_elements=[MensajeEntrante.id_meta])
            .returning(MensajeEntrante.id)
        ).scalar()
        if nuevo is not None and not recibido.ignorar:
            por_atender.add(recibido.telefono)
    return por_atender


def _lo_que_dice(recibido: Recibido) -> dict[str, Any]:
    """Solo lo que hace falta para atenderlo: de una foto o un audio no se guarda nada."""
    if recibido.opcion is not None:
        return {"opcion": recibido.opcion, "titulo": recibido.titulo}
    if recibido.texto is not None:
        return {"texto": recibido.texto}
    return {}


def _entrada(mensaje: MensajeEntrante) -> Entrada:
    contenido = mensaje.contenido or {}
    if contenido.get("opcion"):
        return Entrada(mensaje.telefono, opcion=contenido["opcion"])
    if contenido.get("texto"):
        return Entrada(mensaje.telefono, texto=contenido["texto"])
    return Entrada(mensaje.telefono, otro=True)


def _siguiente(sesion: Session, telefono: str) -> MensajeEntrante | None:
    """El más viejo sin atender de ese teléfono."""
    consulta = (
        select(MensajeEntrante)
        .where(MensajeEntrante.telefono == telefono, MensajeEntrante.procesado_en.is_(None))
        .order_by(MensajeEntrante.recibido_en, MensajeEntrante.id)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    return sesion.scalars(consulta).first()


def _bloquear_telefono(sesion: Session, telefono: str) -> None:
    """Un teléfono a la vez: si otro proceso ya está atendiendo a este cliente, se espera.

    Es un candado de PostgreSQL (advisory lock) con el nombre del teléfono, que se suelta
    solo con el commit. Así dos mensajes seguidos del mismo cliente no se cruzan.
    """
    sesion.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"whatsapp:{telefono}"))))


def _contestar(sesion: Session, telefono: str, respuestas: list[Mensaje], ahora: datetime) -> None:
    cliente = clientes.buscar_por_telefono(sesion, telefono)
    for respuesta in respuestas:
        bandeja_salida.encolar_respuesta(
            sesion,
            telefono=telefono,
            mensaje=respuesta,
            ahora=ahora,
            cliente_id=cliente.id if cliente else None,
        )


def atender_pendientes(abrir: AbrirSesion, telefono: str, *, ahora: datetime | None = None) -> int:
    """Atiende, en orden, los mensajes sin atender de ese teléfono. Devuelve cuántos.

    Cada uno en su propia transacción: lo que hace el bot (pedir el turno, cancelarlo...),
    sus respuestas en la bandeja de salida y la marca de "atendido" se guardan juntos.
    """
    atendidos = 0
    while True:
        momento = ahora or tiempo.ahora()
        mensaje_id: int | None = None
        try:
            with abrir() as sesion, sesion.begin():
                _bloquear_telefono(sesion, telefono)
                mensaje = _siguiente(sesion, telefono)
                if mensaje is None:
                    return atendidos
                mensaje_id = mensaje.id
                # Gemini entiende lo que el cliente escribe con sus palabras, si está.
                interprete = gemini.para_el_bot(leer_parametros(sesion))
                respuestas = conversacion.atender(
                    sesion, _entrada(mensaje), ahora=momento, interprete=interprete
                )
                _contestar(sesion, telefono, respuestas, momento)
                mensaje.procesado_en = momento
        except Exception as error:
            if mensaje_id is None:
                raise  # ni siquiera se pudo leer (la base no responde): lo retoma el reloj
            # Un mensaje que hace fallar al bot no se puede quedar atascado: se marca con
            # el error y se le pide disculpas al cliente. Los siguientes se atienden igual.
            logger.exception("El bot no pudo atender un mensaje de WhatsApp.")
            _marcar_con_error(abrir, telefono, mensaje_id, momento, error)
        atendidos += 1


def _marcar_con_error(
    abrir: AbrirSesion, telefono: str, mensaje_id: int, ahora: datetime, error: Exception
) -> None:
    with abrir() as sesion, sesion.begin():
        _bloquear_telefono(sesion, telefono)
        mensaje = sesion.get(MensajeEntrante, mensaje_id, populate_existing=True)
        if mensaje is None or mensaje.procesado_en is not None:
            return  # mientras tanto lo atendió otro (por ejemplo, el reloj)
        mensaje.procesado_en = ahora
        mensaje.error = repr(error)[:500]
        contacto = telefono_legible(leer_parametros(sesion).telefono_contacto)
        disculpa = Mensaje(texto("error_interno", telefono=contacto))
        _contestar(sesion, telefono, [disculpa], ahora)


def telefonos_con_mensajes_olvidados(sesion: Session, *, ahora: datetime) -> list[str]:
    """Teléfonos con mensajes que nadie atendió (el servidor se cayó a mitad de camino)."""
    consulta = (
        select(MensajeEntrante.telefono)
        .where(
            MensajeEntrante.procesado_en.is_(None),
            MensajeEntrante.recibido_en <= ahora - ESPERA_ANTES_DE_RETOMAR,
        )
        .distinct()
    )
    return sorted(sesion.scalars(consulta))


def atender_y_responder(abrir: AbrirSesion, enviador: Enviador, telefonos: Iterable[str]) -> None:
    """Lo que hace el webhook después de contestarle a Meta: atender y enviar ya mismo."""
    try:
        for telefono in sorted(telefonos):
            atender_pendientes(abrir, telefono)
        bandeja_salida.enviar_pendientes(
            abrir, enviador, ahora=tiempo.ahora(), sigue_vigente=avisos.sigue_vigente
        )
    except Exception:
        # Lo que no salió aquí lo retoma el reloj en su próxima vuelta.
        logger.exception("No se pudieron atender o enviar los mensajes de WhatsApp.")
