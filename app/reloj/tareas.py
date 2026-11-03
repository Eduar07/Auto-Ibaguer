"""Lo que el reloj revisa en cada vuelta.

Cada tarea abre sus propias transacciones con `abrir()` y devuelve lo que hizo en frases
cortas, que el reloj escribe en el registro del servidor. Nunca llevan nombres ni
celulares: ese registro lo pueden leer otras personas (por ejemplo, en la nube).
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.bot import sesiones as sesiones_del_bot
from app.db.sesion import AbrirSesion
from app.integraciones import bandeja_entrada, bandeja_salida
from app.integraciones.bandeja_salida import Enviador, Resultado
from app.turnos import avisos
from app.turnos import casos_de_uso as casos
from app.turnos.estados import EstadoTurno


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


# --- Sesiones del bot ------------------------------------------------------------------------


def _vencer_sesiones_del_bot(abrir: AbrirSesion, ahora: datetime) -> list[str]:
    with abrir() as sesion, sesion.begin():
        vencidas = sesiones_del_bot.vencer(sesion, ahora=ahora)
    if not vencidas:
        return []
    return [
        _cuantos(vencidas, "sesión del bot venció", "sesiones del bot vencieron")
        + " sin respuesta: el bot vuelve al menú."
    ]


SESIONES_DEL_BOT = Tarea("las sesiones del bot", _vencer_sesiones_del_bot)


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


# --- Todas -----------------------------------------------------------------------------------


def del_servidor(enviador: Enviador | None) -> list[Tarea]:
    """Las tareas, en el orden en que corren en cada vuelta.

    El envío va de último: así salen en la misma vuelta los mensajes que acaban de dejar
    las otras tareas (por ejemplo, el aviso de "pasaste al final"). Sin WhatsApp conectado
    no se envía nada y los mensajes esperan en la bandeja de salida.
    """
    tareas = [GRACIAS, SESIONES_DEL_BOT]
    if enviador is not None:
        tareas += [MENSAJES_OLVIDADOS, enviar_mensajes(enviador)]
    return tareas
