"""Choques: dos acciones al mismo tiempo nunca se quedan con el mismo puesto ni el mismo número.

Estas pruebas abren conexiones separadas y confirman (commit) de verdad, como pasa cuando
dos celulares tocan un botón en el mismo segundo. Por eso no usan la sesión que se
deshace sola: al terminar, borran lo que crearon.
"""

import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from app.turnos import casos_de_uso as casos
from app.turnos.errores import CambioNoPermitido, PuestoNoDisponible, YaTieneTurnoActivo
from app.turnos.estados import MedioPago, OrigenTurno
from app.turnos.tablas import Pago
from pruebas.fabricas import LUNES_8AM, cliente_con_vehiculo, en_el_local, puesto, turno_listo


@pytest.fixture
def base(motor: Engine) -> Iterator[Engine]:
    yield motor
    with motor.begin() as conexion:
        conexion.execute(text("TRUNCATE cliente, administrador, mensaje_entrante CASCADE"))


def preparar(motor: Engine, accion: Callable[[Session], Any]) -> Any:
    """Crea datos y los confirma, para que las otras conexiones los vean."""
    with Session(motor) as sesion, sesion.begin():
        return accion(sesion)


def al_mismo_tiempo(motor: Engine, *acciones: Callable[[Session], Any]) -> list[Any]:
    """Corre cada acción en su propio hilo y su propia conexión, arrancando a la vez.

    Devuelve lo que devolvió cada una, o el error que lanzó.
    """
    barrera = threading.Barrier(len(acciones))
    resultados: list[Any] = [None] * len(acciones)

    def correr(indice: int, accion: Callable[[Session], Any]) -> None:
        try:
            with Session(motor) as sesion, sesion.begin():
                barrera.wait(timeout=10)
                resultados[indice] = accion(sesion)
        except Exception as error:
            resultados[indice] = error

    hilos = [threading.Thread(target=correr, args=par) for par in enumerate(acciones)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    return resultados


def errores(resultados: list[Any]) -> list[Exception]:
    return [r for r in resultados if isinstance(r, Exception)]


def test_dos_turnos_al_mismo_tiempo_reciben_numeros_distintos(base: Engine):
    uno, otro = preparar(base, lambda s: (cliente_con_vehiculo(s).id, cliente_con_vehiculo(s).id))

    def pedir(vehiculo_id: int) -> Callable[[Session], str]:
        return lambda s: (
            casos.pedir_turno(
                s, vehiculo_id=vehiculo_id, origen=OrigenTurno.WHATSAPP, ahora=LUNES_8AM
            ).numero
        )

    numeros = al_mismo_tiempo(base, pedir(uno), pedir(otro))
    assert sorted(numeros) == ["C-01", "C-02"]


def test_el_mismo_telefono_pidiendo_dos_veces_a_la_vez_recibe_un_solo_turno(base: Engine):
    vehiculo = preparar(base, lambda s: cliente_con_vehiculo(s).id)

    def pedir(s: Session) -> str:
        return casos.pedir_turno(
            s, vehiculo_id=vehiculo, origen=OrigenTurno.WHATSAPP, ahora=LUNES_8AM
        ).numero

    resultados = al_mismo_tiempo(base, pedir, pedir)
    assert "C-01" in resultados
    [error] = errores(resultados)
    assert isinstance(error, YaTieneTurnoActivo)


def test_dos_vehiculos_al_mismo_puesto_solo_entra_uno(base: Engine):
    uno, otro, c1 = preparar(
        base,
        lambda s: (en_el_local(s).turno.id, en_el_local(s).turno.id, puesto(s, "C1").id),
    )

    def pasar(turno_id: int) -> Callable[[Session], int]:
        return lambda s: casos.pasar_a_puesto(s, turno_id, c1, ahora=LUNES_8AM).id

    resultados = al_mismo_tiempo(base, pasar(uno), pasar(otro))
    [error] = errores(resultados)
    assert isinstance(error, PuestoNoDisponible)
    assert "C1 está ocupado" in str(error)


def test_cobrar_dos_veces_al_mismo_tiempo_deja_un_solo_pago(base: Engine):
    turno = preparar(base, turno_listo)

    def cobrar(s: Session) -> int:
        return casos.cobrar(s, turno, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM).id

    resultados = al_mismo_tiempo(base, cobrar, cobrar)
    [error] = errores(resultados)
    assert isinstance(error, CambioNoPermitido)
    pagos = preparar(
        base,
        lambda s: s.scalar(select(func.count()).select_from(Pago).where(Pago.turno_id == turno)),
    )
    assert pagos == 1
