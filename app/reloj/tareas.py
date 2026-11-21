"""Lo que el reloj revisa en cada vuelta.

Cada tarea abre sus propias transacciones con `abrir()` y devuelve lo que hizo en frases
cortas, que el reloj escribe en el registro del servidor. Nunca llevan nombres ni
celulares: ese registro lo pueden leer otras personas (por ejemplo, en la nube).
"""

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.bot import pausa as pausa_del_bot
from app.bot import seguimiento
from app.bot import sesiones as sesiones_del_bot
from app.catalogo import videos
from app.comentarios import avisos as avisos_al_dueno
from app.db.sesion import AbrirSesion
from app.integraciones import bandeja_entrada, bandeja_salida
from app.integraciones.bandeja_salida import Enviador, EnvioFallido, Resultado
from app.turnos import avisos
from app.turnos import casos_de_uso as casos
from app.turnos.estados import EstadoTurno

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tarea:
    # Para el registro: "Reloj: no pudo revisar {nombre}".
    nombre: str
    hacer: Callable[[AbrirSesion, datetime], list[str]]


def _cuantos(cantidad: int, singular: str, plural: str) -> str:
    return f"{cantidad} {singular if cantidad == 1 else plural}"


# --- Tiempos de gracia ---------------------------------------------------------------------


def _vencer_gracias(abrir: AbrirSesion, ahora: datetime) -> list[str]:
    """Los llamados que no llegaron a tiempo: la primera vez pasan al final de la fila y la
    segunda pierden el turno. La fila avanza, se llama al siguiente y el dashboard se
    entera solo (app.web.en_vivo)."""
    with abrir() as sesion, sesion.begin():
        vencidas = casos.vencer_gracias(sesion, ahora=ahora)
    return [
        f"{v.numero} no llegó a tiempo: pasó al final de la fila."
        if v.estado == EstadoTurno.EN_FILA
        else f"{v.numero} no llegó a tiempo por segunda vez: perdió el turno."
        for v in vencidas
    ]


GRACIAS = Tarea("los tiempos de gracia", _vencer_gracias)


# --- Reservas ---------------------------------------------------------------------------------


def _reservas(abrir: AbrirSesion, ahora: datetime) -> list[str]:
    """A las reservas que les llegó la hora, entran a la fila de primero (y si hay un puesto
    libre, se les llama). A las que les falta una hora, se les recuerda si WhatsApp lo
    deja. Los mensajes salen con el envío de esta vuelta."""
    with abrir() as sesion, sesion.begin():
        entran = casos.activar_reservas(sesion, ahora=ahora)
    with abrir() as sesion, sesion.begin():
        recordadas = casos.recordar_reservas(sesion, ahora=ahora)
    frases = [
        f"{reserva.numero}: llegó la hora de su reserva, entra a la fila." for reserva in entran
    ]
    if recordadas:
        frases.append(_cuantos(recordadas, "reserva recordada", "reservas recordadas") + ".")
    return frases


RESERVAS = Tarea("las reservas", _reservas)


# --- Sesiones del bot ------------------------------------------------------------------------


def _vencer_sesiones_del_bot(abrir: AbrirSesion, ahora: datetime) -> list[str]:
    with abrir() as sesion, sesion.begin():
        vencidas = sesiones_del_bot.vencer(sesion, ahora=ahora)
        # Las pausas del bot que ya pasaron también se borran: tienen el celular.
        pausa_del_bot.vencer_pausas(sesion, ahora=ahora)
    if not vencidas:
        return []
    return [
        _cuantos(vencidas, "sesión del bot venció", "sesiones del bot vencieron")
        + " sin respuesta: el bot vuelve al menú."
    ]


SESIONES_DEL_BOT = Tarea("las sesiones del bot", _vencer_sesiones_del_bot)


# --- El seguimiento de "lo pienso" --------------------------------------------------------


def _seguir(abrir: AbrirSesion, ahora: datetime) -> list[str]:
    """A quien dijo "lo pienso" y aceptó que le escribiéramos: cómo va la fila, dentro de
    las 24 horas de WhatsApp (app.bot.seguimiento). Sale con el envío de esta vuelta."""
    with abrir() as sesion, sesion.begin():
        enviados = seguimiento.enviar_los_que_tocan(sesion, ahora=ahora)
    if not enviados:
        return []
    return [_cuantos(enviados, "seguimiento", "seguimientos") + ' de "lo pienso" en la bandeja.']


SEGUIMIENTOS = Tarea('los seguimientos de "lo pienso"', _seguir)


# --- El aviso al dueño --------------------------------------------------------------------


def _avisar_al_dueno(abrir: AbrirSesion, ahora: datetime) -> list[str]:
    """Las quejas, los pedidos de hablar con alguien y los pedidos especiales recientes: un
    correo al dueño con el resumen (app.comentarios.avisos). El correo sale después de
    guardar: si el servidor de correo tarda, no deja nada bloqueado."""
    with abrir() as sesion, sesion.begin():
        aviso = avisos_al_dueno.preparar(sesion, ahora=ahora)
    if aviso is None or not avisos_al_dueno.enviar(aviso):
        return []
    return ["Se le avisó al dueño por correo de clientes que esperan respuesta."]


AVISO_AL_DUENO = Tarea("el aviso al dueño", _avisar_al_dueno)


# --- WhatsApp: mensajes por atender y por enviar ----------------------------------------------


def _atender_olvidados(abrir: AbrirSesion, ahora: datetime) -> list[str]:
    """Mensajes de clientes que nadie atendió porque el servidor se cayó a mitad de camino."""
    with abrir() as sesion:
        telefonos = bandeja_entrada.telefonos_con_mensajes_olvidados(sesion, ahora=ahora)
    atendidos = sum(bandeja_entrada.atender_pendientes(abrir, t, ahora=ahora) for t in telefonos)
    if not atendidos:
        return []
    return [
        _cuantos(atendidos, "mensaje de WhatsApp", "mensajes de WhatsApp")
        + " que se habían quedado sin atender ya se atendieron."
    ]


MENSAJES_OLVIDADOS = Tarea("los mensajes de WhatsApp sin atender", _atender_olvidados)


def enviar_mensajes(enviador: Enviador) -> Tarea:
    """Envía lo que ya toca de la bandeja de salida: respuestas del bot, avisos de la fila
    y los reintentos de lo que WhatsApp no aceptó."""

    def hacer(abrir: AbrirSesion, ahora: datetime) -> list[str]:
        cuenta = bandeja_salida.enviar_pendientes(
            abrir, enviador, ahora=ahora, sigue_vigente=avisos.sigue_vigente
        )
        return _resumen_de_envios(cuenta)

    return Tarea("los mensajes por WhatsApp", hacer)


def _resumen_de_envios(cuenta: Counter[Resultado]) -> list[str]:
    frases = {
        Resultado.ENVIADO: ("mensaje enviado", "mensajes enviados"),
        Resultado.REINTENTO: (
            "mensaje no salió: se reintenta",
            "mensajes no salieron: se reintentan",
        ),
        Resultado.FALLIDO: (
            "mensaje no se pudo enviar y se dejó de intentar",
            "mensajes no se pudieron enviar y se dejó de intentar",
        ),
        Resultado.DESCARTADO: (
            "aviso ya no servía (el turno cambió) y no se envió",
            "avisos ya no servían (el turno cambió) y no se enviaron",
        ),
    }
    return [
        _cuantos(cuenta[resultado], *frases[resultado]) + "."
        for resultado in Resultado
        if cuenta[resultado]
    ]


# --- Los videos de los servicios: que WhatsApp los tenga listos ------------------------------


class SubeVideos(Protocol):
    def subir_video(self, contenido: bytes, tipo: str, nombre: str) -> str: ...


# Si WhatsApp no recibe un video, se vuelve a intentar después de esto (no en cada vuelta:
# serían 16 MB cada 30 segundos).
ESPERA_TRAS_FALLAR = timedelta(hours=1)


def subir_videos(subidor: SubeVideos) -> Tarea:
    """Le sube a WhatsApp los videos de los servicios que todavía no tiene, o que tiene por
    vencerse (los guarda 30 días). Así, cuando el bot manda uno, ya está listo."""
    fallas: dict[int, datetime] = {}

    def hacer(abrir: AbrirSesion, ahora: datetime) -> list[str]:
        with abrir() as sesion:
            pendientes = videos.por_subir_a_whatsapp(sesion, ahora)
        subidos = 0
        for servicio_id in pendientes:
            if servicio_id in fallas and ahora - fallas[servicio_id] < ESPERA_TRAS_FALLAR:
                continue
            with abrir() as sesion, sesion.begin():
                video = videos.para_subir(sesion, servicio_id)
                if video is None or videos.id_vigente(video, ahora):
                    continue
                try:
                    id_en_meta = subidor.subir_video(video.contenido, video.tipo, video.nombre)
                except EnvioFallido as error:
                    fallas[servicio_id] = ahora
                    logger.warning("No se pudo subir a WhatsApp el video de un servicio: %s", error)
                    continue
                videos.anotar_subida(video, id_en_meta, ahora)
                fallas.pop(servicio_id, None)
                subidos += 1
        if not subidos:
            return []
        return [
            _cuantos(
                subidos,
                "video de un servicio quedó listo en WhatsApp",
                "videos de servicios quedaron listos en WhatsApp",
            )
            + "."
        ]

    return Tarea("los videos de los servicios", hacer)


# --- Todas -----------------------------------------------------------------------------------


def del_servidor(enviador: Enviador | None) -> list[Tarea]:
    """Las tareas, en el orden en que corren en cada vuelta.

    El envío va después de las demás: así salen en la misma vuelta los mensajes que acaban de
    dejar las otras tareas (por ejemplo, el aviso de "pasaste al final"). Subir los videos va
    de último, para no demorar ningún mensaje. Sin WhatsApp conectado no se envía nada y los
    mensajes esperan en la bandeja de salida.
    """
    tareas = [RESERVAS, GRACIAS, SESIONES_DEL_BOT, AVISO_AL_DUENO, SEGUIMIENTOS]
    if enviador is not None:
        tareas += [MENSAJES_OLVIDADOS, enviar_mensajes(enviador)]
        if hasattr(enviador, "subir_video"):
            tareas.append(subir_videos(enviador))
    return tareas
