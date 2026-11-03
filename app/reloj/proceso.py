"""Cómo da las vueltas el reloj.

Corre dentro del mismo servidor web (en la Fase 1 son un solo servicio en la nube): al
arrancar el servidor se enciende y al apagarlo se detiene (app.main). Cada vuelta corre
en un hilo aparte, porque habla con la base de datos y mientras tanto el servidor debe
seguir atendiendo el dashboard.

Si por error quedaran dos relojes corriendo (por ejemplo, dos copias del servidor), no
pasa nada: cada tarea bloquea lo que cambia, y lo que un reloj ya hizo el otro lo
encuentra hecho.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from app import tiempo
from app.db.sesion import AbrirSesion, abrir_sesion
from app.integraciones.bandeja_salida import Enviador
from app.reloj import tareas as tareas_del_reloj
from app.reloj.tareas import Tarea

logger = logging.getLogger(__name__)

CADA_SEGUNDOS = 30.0
# Si pasa esto sin que empiece una vuelta, el reloj se detuvo: /salud lo dice y Render
# reinicia el servidor. Es largo porque una vuelta con WhatsApp caído puede tardar varios
# minutos (cada mensaje espera hasta 10 segundos).
SE_DETUVO_SI_PASAN = 15 * 60.0


class Reloj:
    """Corre las tareas, una tras otra. Si una falla, las demás corren igual."""

    def __init__(self, tareas: Sequence[Tarea], abrir: AbrirSesion) -> None:
        self.tareas = list(tareas)
        self.abrir = abrir
        # Las tareas que fallaron en la vuelta anterior. El error completo se escribe solo
        # la primera vez, para no llenar el registro si la base de datos se cae un rato.
        self._fallando: set[str] = set()
        # Cuándo empezó la última vuelta (time.monotonic), para saber si sigue dando vueltas.
        self.encendido_desde = time.monotonic()
        self.empezo_ultima_vuelta: float | None = None

    def una_vuelta(self, ahora: datetime | None = None) -> None:
        self.empezo_ultima_vuelta = time.monotonic()
        for tarea in self.tareas:
            self._correr(tarea, ahora or tiempo.ahora())

    def se_detuvo(self, ahora: float | None = None) -> bool:
        """¿Lleva demasiado sin empezar una vuelta? (Algo lo trancó.)"""
        desde = self.empezo_ultima_vuelta
        if desde is None:
            desde = self.encendido_desde  # todavía no ha dado la primera
        if ahora is None:
            ahora = time.monotonic()
        return ahora - desde > SE_DETUVO_SI_PASAN

    def _correr(self, tarea: Tarea, ahora: datetime) -> None:
        try:
            hechos = tarea.hacer(self.abrir, ahora)
        except Exception:
            if tarea.nombre not in self._fallando:
                self._fallando.add(tarea.nombre)
                logger.exception(
                    "Reloj: no pudo revisar %s. Lo vuelve a intentar en cada vuelta.", tarea.nombre
                )
            return
        if tarea.nombre in self._fallando:
            self._fallando.discard(tarea.nombre)
            logger.info("Reloj: ya pudo revisar %s otra vez.", tarea.nombre)
        for hecho in hechos:
            logger.info("Reloj: %s", hecho)


def del_servidor(enviador: Enviador | None = None) -> Reloj:
    if enviador is None:
        logger.info(
            "Reloj: WhatsApp no está conectado; los mensajes esperan en la bandeja de salida."
        )
    else:
        logger.info("Reloj: WhatsApp conectado; también envía y reintenta los mensajes.")
    return Reloj(tareas_del_reloj.del_servidor(enviador), abrir_sesion)


async def correr(reloj: Reloj, *, cada: float = CADA_SEGUNDOS) -> None:
    """Da una vuelta apenas arranca y después cada `cada` segundos, hasta que lo cancelan."""
    while True:
        inicio = time.monotonic()
        try:
            await asyncio.to_thread(reloj.una_vuelta)
        except Exception:
            # Una vuelta ya atrapa los errores de cada tarea; esto es por si acaso: el
            # reloj nunca se detiene por un error.
            logger.exception("Reloj: la vuelta falló.")
        await asyncio.sleep(max(0.0, cada - (time.monotonic() - inicio)))


@asynccontextmanager
async def encendido(reloj: Reloj, *, cada: float = CADA_SEGUNDOS) -> AsyncIterator[None]:
    """Mientras dure el bloque `async with`, el reloj da sus vueltas."""
    tarea = asyncio.create_task(correr(reloj, cada=cada), name="reloj")
    logger.info("Reloj encendido: revisa cada %g segundos.", cada)
    try:
        yield
    finally:
        tarea.cancel()
        with suppress(asyncio.CancelledError):
            await tarea
        logger.info("Reloj apagado.")
