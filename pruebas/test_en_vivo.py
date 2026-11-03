"""La fila en vivo: PostgreSQL avisa cada cambio confirmado y el servidor lo entrega.

Los avisos de PostgreSQL (NOTIFY) solo salen cuando el cambio se confirma, así que estas
pruebas confirman de verdad, como en test_choques.py, y al terminar borran lo que crearon.
"""

import asyncio
import signal
import time
from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.web import en_vivo
from pruebas.base_de_pruebas import URL_PRUEBAS
from pruebas.fabricas import por_whatsapp


@pytest.fixture
def base(motor: Engine) -> Iterator[Engine]:
    yield motor
    with motor.begin() as conexion:
        conexion.execute(text("TRUNCATE cliente, administrador, mensaje_entrante CASCADE"))


@pytest.fixture
def apagado_limpio() -> Iterator[None]:
    yield
    en_vivo.APAGANDO.clear()


def test_un_cambio_confirmado_avisa_y_uno_deshecho_no(base: Engine):
    with psycopg.connect(en_vivo.url_para_psycopg(URL_PRUEBAS), autocommit=True) as escucha:
        escucha.execute('LISTEN "fila_cambio"')

        with Session(base) as sesion:
            por_whatsapp(sesion)
            sesion.rollback()
        assert list(escucha.notifies(timeout=0.5)) == []

        with Session(base) as sesion, sesion.begin():
            fila_id = por_whatsapp(sesion).turno.fila_id
        avisos = list(escucha.notifies(timeout=2, stop_after=1))

    assert [aviso.payload for aviso in avisos] == [str(fila_id)]


def test_la_fila_en_vivo_entrega_cada_fila_que_cambia(motor: Engine):
    async def probar() -> list[int]:
        recibidos: list[int] = []
        cambios = en_vivo.cambios(URL_PRUEBAS, duracion_maxima=10)

        async def escuchar() -> None:
            async for fila_id in cambios:
                recibidos.append(fila_id)
                if len(recibidos) == 2:
                    return

        tarea = asyncio.create_task(escuchar())
        await asyncio.sleep(1)  # que alcance a conectarse y a escuchar el canal
        async with await psycopg.AsyncConnection.connect(
            en_vivo.url_para_psycopg(URL_PRUEBAS), autocommit=True
        ) as otra:
            for carga in ("1", "no-es-un-numero", "2"):
                await otra.execute("SELECT pg_notify('fila_cambio', %s)", (carga,))
        await asyncio.wait_for(tarea, timeout=5)
        await cambios.aclose()
        return recibidos

    assert asyncio.run(probar()) == [1, 2]


def test_la_fila_en_vivo_termina_sola_al_cumplir_su_tiempo(motor: Engine):
    async def cuanto_dura() -> float:
        inicio = time.monotonic()
        async for _ in en_vivo.cambios(URL_PRUEBAS, duracion_maxima=0.5):
            pass
        return time.monotonic() - inicio

    assert asyncio.run(cuanto_dura()) < 3


def test_la_fila_en_vivo_termina_al_apagar_el_servidor(motor: Engine, apagado_limpio: None):
    """Así `fastapi dev` recarga de una vez aunque haya un dashboard abierto."""

    async def cuanto_dura() -> float:
        inicio = time.monotonic()

        async def apagar() -> None:
            await asyncio.sleep(1)
            en_vivo.APAGANDO.set()

        apagado = asyncio.create_task(apagar())
        async for _ in en_vivo.cambios(URL_PRUEBAS, duracion_maxima=60):
            pass
        await apagado
        return time.monotonic() - inicio

    assert asyncio.run(cuanto_dura()) < 4


def test_sin_base_de_datos_la_fila_en_vivo_termina_sin_romperse():
    async def recibir() -> list[int]:
        return [f async for f in en_vivo.cambios("postgresql+psycopg://auto:auto@localhost:1/x")]

    assert asyncio.run(recibir()) == []


def test_al_apagar_avisa_y_deja_trabajar_al_manejador_anterior(apagado_limpio: None):
    originales = {senal: signal.getsignal(senal) for senal in (signal.SIGINT, signal.SIGTERM)}
    recibidas: list[int] = []
    try:
        # Hace las veces del manejador de uvicorn, que es el que apaga el servidor.
        signal.signal(signal.SIGTERM, lambda numero, _marco: recibidas.append(numero))
        en_vivo.cerrar_al_apagar()

        signal.raise_signal(signal.SIGTERM)

        assert en_vivo.APAGANDO.is_set()
        assert recibidas == [signal.SIGTERM]
    finally:
        for senal, manejador in originales.items():
            signal.signal(senal, manejador)
