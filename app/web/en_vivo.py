"""La fila en vivo: avisarle al dashboard abierto apenas algo cambia.

Cada cambio a una fila deja un aviso en el canal `fila_cambio` de PostgreSQL
(app.turnos.datos.bloquear_fila), y PostgreSQL lo entrega solo si el cambio se
confirmó. Aquí se escucha ese canal y el servidor se lo pasa al navegador con
Server-Sent Events. Así el dashboard se entera de lo que haga el bot, el reloj u otro
celular, sin recargar la página cada pocos segundos.

Funciona aunque el bot y el dashboard corran en procesos distintos: el que avisa es
PostgreSQL, que todos comparten.
"""

import logging
import signal
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from types import FrameType
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.turnos.datos import CANAL_DE_CAMBIOS

logger = logging.getLogger(__name__)

# Cada conexión de eventos dura máximo esto. El navegador se vuelve a conectar solo, y así
# una sesión que venció deja de recibir cambios.
DURACION_MAXIMA_SEGUNDOS = 300.0
# Cada cuánto se mira si el servidor se está apagando.
REVISAR_CADA_SEGUNDOS = 1.0

# Se enciende cuando le piden al servidor que se apague.
APAGANDO = threading.Event()


def cerrar_al_apagar() -> None:
    """Al apagar el servidor (Ctrl+C, una recarga de `fastapi dev` o un despliegue nuevo),
    los eventos abiertos terminan en un segundo.

    Sin esto, el servidor esperaría a que cada navegador conectado se desconecte solo
    (hasta DURACION_MAXIMA_SEGUNDOS) antes de apagarse o de cargar el código nuevo.
    El aviso se encadena al manejador que ya tenía cada señal (el de uvicorn), que sigue
    haciendo su trabajo de siempre.
    """
    if threading.current_thread() is not threading.main_thread():
        return  # las señales solo llegan al hilo principal; en las pruebas no aplica
    APAGANDO.clear()
    for senal in (signal.SIGINT, signal.SIGTERM):
        anterior = signal.getsignal(senal)
        if not callable(anterior):
            continue

        def avisar(
            numero: int, marco: FrameType | None, anterior: Callable[..., Any] = anterior
        ) -> None:
            APAGANDO.set()
            anterior(numero, marco)

        signal.signal(senal, avisar)


def url_para_psycopg(url_sqlalchemy: str) -> str:
    """ "postgresql+psycopg://..." -> "postgresql://...": psycopg no entiende el "+psycopg"."""
    return (
        make_url(url_sqlalchemy).set(drivername="postgresql").render_as_string(hide_password=False)
    )


async def cambios(
    url_sqlalchemy: str, *, duracion_maxima: float = DURACION_MAXIMA_SEGUNDOS
) -> AsyncIterator[int]:
    """Entrega el id de cada fila que cambia, en cuanto el cambio se confirma.

    Termina sola a los `duracion_maxima` segundos o cuando el servidor se apaga. Si la
    base de datos no responde, termina de una vez: el navegador vuelve a intentar.
    """
    fin = time.monotonic() + duracion_maxima
    try:
        conexion = await psycopg.AsyncConnection.connect(
            url_para_psycopg(url_sqlalchemy), autocommit=True, connect_timeout=5
        )
    except psycopg.OperationalError:
        logger.warning("La fila en vivo no pudo conectarse a la base de datos.")
        return
    async with conexion:
        await conexion.execute(sql.SQL("LISTEN {}").format(sql.Identifier(CANAL_DE_CAMBIOS)))
        while not APAGANDO.is_set() and (queda := fin - time.monotonic()) > 0:
            # Se escucha de a un segundo para notar a tiempo que el servidor se apaga. Un
            # aviso que llegue entre una vuelta y la otra no se pierde: psycopg lo guarda.
            async with aclosing(
                conexion.notifies(timeout=min(REVISAR_CADA_SEGUNDOS, queda))
            ) as avisos:
                async for aviso in avisos:
                    if aviso.payload.isdigit():
                        yield int(aviso.payload)
