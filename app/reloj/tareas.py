"""Lo que el reloj revisa en cada vuelta.

Cada tarea abre sus propias transacciones con `abrir()` y devuelve lo que hizo en frases
cortas, que el reloj escribe en el registro del servidor. Nunca llevan nombres ni
celulares: ese registro lo pueden leer otras personas (por ejemplo, en la nube).
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.bot import sesiones as sesiones_del_bot
from app.integraciones import bandeja_salida
from app.integraciones.bandeja_salida import Enviador, Resultado
from app.turnos import avisos
from app.turnos import casos_de_uso as casos
from app.turnos.estados import EstadoTurno

AbrirSesion = Callable[[], Session]


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


# --- Avisos por WhatsApp ---------------------------------------------------------------------

# Máximo de mensajes por vuelta, para que una vuelta nunca se alargue demasiado. Los que
# falten salen en la siguiente.
MENSAJES_POR_VUELTA = 50


def enviar_avisos(enviador: Enviador, *, por_vuelta: int = MENSAJES_POR_VUELTA) -> Tarea:
    """Envía los mensajes de la bandeja de salida que ya toca enviar.

    Cada mensaje va en su propia transacción: si el servidor se cae a mitad de la vuelta,
    los que ya salieron quedan anotados como enviados y no se repiten.
    """

    def hacer(abrir: AbrirSesion, ahora: datetime) -> list[str]:
        cuenta: Counter[Resultado] = Counter()
        for _ in range(por_vuelta):
            with abrir() as sesion, sesion.begin():
                resultado = bandeja_salida.enviar_siguiente(
                    sesion, enviador, ahora=ahora, sigue_vigente=avisos.sigue_vigente
                )
            if resultado is None:
                break
            cuenta[resultado] += 1
        return _resumen_de_envios(cuenta)

    return Tarea("los avisos por WhatsApp", hacer)


def _resumen_de_envios(cuenta: Counter[Resultado]) -> list[str]:
    frases = {
        Resultado.ENVIADO: ("aviso enviado", "avisos enviados"),
        Resultado.REINTENTO: ("aviso no salió: se reintenta", "avisos no salieron: se reintentan"),
        Resultado.FALLIDO: (
            "aviso no se pudo enviar y se dejó de intentar",
            "avisos no se pudieron enviar y se dejó de intentar",
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

    Los avisos van de últimos: así salen en la misma vuelta los que acaban de dejar las
    otras tareas (por ejemplo, el de "pasaste al final"). Sin enviador (hasta el hito 5)
    no se envía nada y los avisos esperan en la bandeja de salida.
    """
    tareas = [GRACIAS, SESIONES_DEL_BOT]
    if enviador is not None:
        tareas.append(enviar_avisos(enviador))
    return tareas
