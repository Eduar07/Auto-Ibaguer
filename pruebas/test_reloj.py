"""El reloj: cada 30 segundos revisa lo que vence con el tiempo, sin que nadie toque nada.

La prueba del hito 6: un turno llamado que no llega pasa solo al final a los 15 minutos.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, func, select, text
from sqlalchemy.orm import Session

from app import main
from app.config import obtener_entorno
from app.reloj import proceso, tareas
from app.reloj.tareas import Tarea
from app.turnos.estados import EstadoTurno, TipoAviso
from app.turnos.tablas import TurnoHistorial
from pruebas.fabricas import (
    LUNES_8AM,
    avisos,
    estado,
    minutos,
    ocupar_puestos,
    por_whatsapp,
)

E = EstadoTurno


@pytest.fixture
def abrir(conexion: Connection) -> Callable[[], Session]:
    """Sesiones como las que abre el reloj, pero dentro de la transacción de la prueba:
    lo que confirman se deshace al terminar."""
    return lambda: Session(bind=conexion, join_transaction_mode="create_savepoint")


@pytest.fixture
def anotaciones(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="app.reloj")
    return caplog


def reloj_del_servidor(abrir: Callable[[], Session]) -> proceso.Reloj:
    return proceso.Reloj(tareas.del_servidor(enviador=None), abrir)


# --- Lo que hace en cada vuelta ------------------------------------------------------------


def test_un_llamado_que_no_llega_pasa_solo_al_final_a_los_15_minutos(
    sesion: Session, abrir, anotaciones
):
    ocupar_puestos(sesion, "C2")  # C-01
    tarde = por_whatsapp(sesion).turno.id  # C-02, llamado para C1 hasta las 8:15
    siguiente = por_whatsapp(sesion).turno.id  # C-03
    reloj = reloj_del_servidor(abrir)

    reloj.una_vuelta(LUNES_8AM + minutos(14))
    sesion.expire_all()
    assert estado(sesion, tarde) == E.LLAMADO

    reloj.una_vuelta(LUNES_8AM + minutos(15))
    sesion.expire_all()
    assert estado(sesion, tarde) == E.EN_FILA
    assert estado(sesion, siguiente) == E.LLAMADO  # la fila avanzó sola
    assert [a.tipo for a in avisos(sesion, turno_id=tarde)] == [TipoAviso.PASA_AL_FINAL]
    assert "Reloj: C-02 no llegó a tiempo: pasó al final de la fila." in anotaciones.messages


def test_la_segunda_vez_sin_llegar_pierde_el_turno(sesion: Session, abrir, anotaciones):
    solo = por_whatsapp(sesion).turno.id  # puestos libres: lo llaman de una vez
    reloj = reloj_del_servidor(abrir)

    reloj.una_vuelta(LUNES_8AM + minutos(15))  # pasa al final y, solo en la fila, lo llaman
    reloj.una_vuelta(LUNES_8AM + minutos(30))

    sesion.expire_all()
    assert estado(sesion, solo) == E.NO_LLEGO
    assert "Reloj: C-01 no llegó a tiempo por segunda vez: perdió el turno." in (
        anotaciones.messages
    )


def test_una_vuelta_sin_nada_vencido_no_anota_nada(sesion: Session, abrir, anotaciones):
    por_whatsapp(sesion)

    reloj_del_servidor(abrir).una_vuelta(LUNES_8AM + minutos(1))

    assert anotaciones.messages == []


def test_si_una_tarea_falla_las_demas_corren_y_el_error_se_anota_una_sola_vez(anotaciones):
    base_caida = {"si": True}
    vueltas_de_la_otra: list[datetime] = []

    def revisar_algo(_abrir, _ahora) -> list[str]:
        if base_caida["si"]:
            raise RuntimeError("la base de datos no responde")
        return ["algo pasó."]

    def otra_tarea(_abrir, ahora) -> list[str]:
        vueltas_de_la_otra.append(ahora)
        return []

    reloj = proceso.Reloj([Tarea("algo", revisar_algo), Tarea("lo otro", otra_tarea)], Session)

    reloj.una_vuelta(LUNES_8AM)
    reloj.una_vuelta(LUNES_8AM)
    assert len(vueltas_de_la_otra) == 2
    fallas = [r for r in anotaciones.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in fallas] == [
        "Reloj: no pudo revisar algo. Lo vuelve a intentar en cada vuelta."
    ]
    assert "la base de datos no responde" in anotaciones.text  # con el error completo

    base_caida["si"] = False
    reloj.una_vuelta(LUNES_8AM)
    assert anotaciones.messages[-2:] == [
        "Reloj: ya pudo revisar algo otra vez.",
        "Reloj: algo pasó.",
    ]


# --- Dos relojes a la vez ---------------------------------------------------------------------


@pytest.fixture
def base(motor: Engine) -> Iterator[Engine]:
    """Para pruebas que confirman de verdad: al terminar se borra lo que crearon."""
    yield motor
    with motor.begin() as conexion:
        conexion.execute(text("TRUNCATE cliente, administrador, mensaje_entrante CASCADE"))


def test_dos_relojes_a_la_vez_no_hacen_el_trabajo_dos_veces(base: Engine):
    with Session(base) as sesion, sesion.begin():
        ocupar_puestos(sesion, "C2")
        tarde = por_whatsapp(sesion).turno.id
    relojes = [proceso.Reloj([tareas.GRACIAS], lambda: Session(base)) for _ in range(2)]
    barrera = threading.Barrier(2)

    def dar_la_vuelta(reloj: proceso.Reloj) -> None:
        barrera.wait(timeout=10)
        reloj.una_vuelta(LUNES_8AM + minutos(15))

    hilos = [threading.Thread(target=dar_la_vuelta, args=(reloj,)) for reloj in relojes]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)

    with Session(base) as sesion:
        pasos_al_final = sesion.scalar(
            select(func.count()).where(
                TurnoHistorial.turno_id == tarde,
                TurnoHistorial.estado_anterior == E.LLAMADO,
                TurnoHistorial.estado_nuevo == E.EN_FILA,
            )
        )
        assert pasos_al_final == 1
        # Cada aviso una sola vez. Como nadie más esperaba, lo llamaron otra vez de una.
        assert [a.tipo for a in avisos(sesion, turno_id=tarde)] == [
            TipoAviso.PASA_AL_FINAL,
            TipoAviso.LLAMADO,
        ]


# --- Encendido y apagado ----------------------------------------------------------------------


class RelojDePrueba:
    """Cuenta sus vueltas; la primera falla, como si hubiera un error raro."""

    def __init__(self, fallar_la_primera: bool = False) -> None:
        self.vueltas = 0
        self.dio_una_vuelta = threading.Event()
        self.fallar_la_primera = fallar_la_primera

    def una_vuelta(self) -> None:
        self.vueltas += 1
        self.dio_una_vuelta.set()
        if self.fallar_la_primera and self.vueltas == 1:
            raise RuntimeError("falla rara")


def test_el_reloj_sigue_dando_vueltas_aunque_una_falle_hasta_que_lo_apagan():
    async def probar() -> tuple[int, int]:
        reloj = RelojDePrueba(fallar_la_primera=True)
        async with proceso.encendido(reloj, cada=0.01):
            while reloj.vueltas < 3:
                await asyncio.sleep(0.01)
        al_apagar = reloj.vueltas
        await asyncio.sleep(0.05)
        return al_apagar, reloj.vueltas

    al_apagar, despues = asyncio.run(asyncio.wait_for(probar(), timeout=5))

    assert al_apagar >= 3
    assert despues == al_apagar  # apagado, no da más vueltas


@pytest.fixture
def con_reloj(monkeypatch: pytest.MonkeyPatch) -> Callable[[bool], list[RelojDePrueba]]:
    """Hace que el servidor use un reloj de prueba (nunca toca la base de desarrollo)."""
    creados: list[RelojDePrueba] = []

    def configurar(activo: bool) -> list[RelojDePrueba]:
        monkeypatch.setattr(obtener_entorno(), "reloj_activo", activo)

        def del_servidor(enviador=None) -> RelojDePrueba:
            creados.append(RelojDePrueba())
            return creados[-1]

        monkeypatch.setattr(proceso, "del_servidor", del_servidor)
        return creados

    return configurar


def test_el_servidor_enciende_el_reloj_al_arrancar(con_reloj):
    creados = con_reloj(True)

    with TestClient(main.app):
        assert len(creados) == 1
        assert creados[0].dio_una_vuelta.wait(timeout=5)  # la primera vuelta es de una vez


def test_con_el_reloj_apagado_en_la_configuracion_no_arranca(con_reloj):
    creados = con_reloj(False)

    with TestClient(main.app):
        pass

    assert creados == []
