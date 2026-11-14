"""Una caja por negocio: el parqueadero y el lavadero tienen cada uno la suya, en la misma tabla.

Lo que tiene que cumplirse siempre:
- cada negocio tiene a lo sumo una caja abierta, y abrir la de uno no toca la del otro (lo
  exige también la base de datos);
- lo del parqueadero (su caja abierta, sus cierres, quién cerró, su balance, una caja por su
  número, sus correcciones) nunca muestra ni toca una caja del lavadero;
- las cajas que ya había antes de la versión 0012 quedan como del parqueadero, sin cambiar.
"""

from datetime import date, datetime

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.caja import casos_de_uso as cajas
from app.caja.errores import CajaNoCorregible, CajaYaAbierta, CobroNoCorregible
from app.caja.estados import Negocio
from app.caja.tablas import Caja
from app.cli import configuracion_alembic, migrar
from app.parqueadero import balance, consultas
from app.parqueadero import casos_de_uso as parqueadero
from app.parqueadero.consultas import FiltroDeCierres
from app.web.administradores import guardar_administrador
from pruebas.base_de_pruebas import URL_PRUEBAS, borrar_base, recrear_base
from pruebas.fabricas import BOGOTA, debe_rechazar

LAVADERO, PARQUEADERO = Negocio.LAVADERO, Negocio.PARQUEADERO


def a_las(dia: int, hora: int) -> datetime:
    return datetime(2026, 10, dia, hora, tzinfo=BOGOTA)


def del_lavadero(sesion: Session, dia: int = 5, *, cerrar: bool = True, por: int | None = None):
    """Una caja del lavadero de ese día de octubre, con $10.000 de base (cerrada por `por`)."""
    caja = cajas.abrir_caja(sesion, LAVADERO, base=10_000, ahora=a_las(dia, 7))
    if cerrar:
        cajas.cerrar_caja(
            sesion,
            LAVADERO,
            caja.id,
            efectivo_esperado=lambda _sesion, caja: caja.base,
            efectivo_contado=9_000,
            administrador_id=por,
            ahora=a_las(dia, 19),
        )
    return caja


# --- Una abierta por negocio ---------------------------------------------------------------


def test_cada_negocio_abre_la_suya(sesion: Session):
    del_parqueadero = parqueadero.abrir_caja(sesion, base=20_000, ahora=a_las(5, 6))
    lavadero = cajas.abrir_caja(sesion, LAVADERO, base=10_000, ahora=a_las(5, 7))

    assert (del_parqueadero.negocio, lavadero.negocio) == (PARQUEADERO, LAVADERO)
    assert parqueadero.caja_abierta(sesion) == del_parqueadero
    assert cajas.caja_abierta(sesion, LAVADERO) == lavadero
    with pytest.raises(CajaYaAbierta):
        cajas.abrir_caja(sesion, LAVADERO, base=0, ahora=a_las(5, 8))


def test_la_base_no_deja_dos_abiertas_del_mismo_negocio(sesion: Session):
    cajas.abrir_caja(sesion, LAVADERO, base=0, ahora=a_las(5, 7))

    with debe_rechazar(sesion, "uq_caja_una_abierta_por_negocio"):
        sesion.add(Caja(negocio=LAVADERO, base=0, abierta_en=a_las(5, 8)))


def test_la_base_no_acepta_otro_negocio(sesion: Session):
    with debe_rechazar(sesion, "ck_caja_negocio"):
        sesion.execute(text("INSERT INTO caja (negocio, base) VALUES ('tienda', 0)"))


def test_cerrar_la_del_parqueadero_no_cierra_la_del_lavadero(sesion: Session):
    del_parqueadero = parqueadero.abrir_caja(sesion, base=0, ahora=a_las(5, 6))
    lavadero = cajas.abrir_caja(sesion, LAVADERO, base=0, ahora=a_las(5, 7))

    parqueadero.cerrar_caja(sesion, del_parqueadero.id, efectivo_contado=0, ahora=a_las(5, 20))

    assert lavadero.abierta and cajas.caja_abierta(sesion, LAVADERO) == lavadero
    # Y la del lavadero no se cierra desde el parqueadero.
    with pytest.raises(Exception, match="ya está cerrada"):
        parqueadero.cerrar_caja(sesion, lavadero.id, efectivo_contado=0, ahora=a_las(5, 20))
    assert lavadero.abierta


# --- El parqueadero no ve las cajas del lavadero -------------------------------------------


def test_el_parqueadero_no_ve_la_caja_abierta_del_lavadero(sesion: Session):
    lavadero = cajas.abrir_caja(sesion, LAVADERO, base=0, ahora=a_las(5, 7))

    assert parqueadero.caja_abierta(sesion) is None
    assert parqueadero.caja_del_parqueadero(sesion, lavadero.id) is None


def test_los_cierres_del_parqueadero_no_traen_los_del_lavadero(sesion: Session):
    eduar, _ = guardar_administrador(sesion, "eduar.prueba", "clave-larga-de-eduar")
    ana, _ = guardar_administrador(sesion, "ana", "clave-larga-de-ana")
    caja = parqueadero.abrir_caja(sesion, base=0, ahora=a_las(4, 6))
    parqueadero.cerrar_caja(
        sesion, caja.id, efectivo_contado=0, administrador_id=eduar.id, ahora=a_las(4, 20)
    )
    del_lavadero(sesion, 5, por=ana.id)

    todos = consultas.cierres(sesion, FiltroDeCierres())

    assert [fila.caja.id for fila in todos.filas] == [caja.id]
    assert (todos.cuantos, todos.faltantes) == (1, 0)
    assert [u for _, u in consultas.quienes_cerraron(sesion)] == ["eduar.prueba"]
    assert consultas.todos_los_cierres(sesion, FiltroDeCierres()) == todos.filas
    assert consultas.cierres(sesion, FiltroDeCierres(resultado="falto")).cuantos == 0


def test_el_balance_del_parqueadero_no_cuenta_cajas_del_lavadero(sesion: Session):
    del_lavadero(sesion, 5)

    del_dia = balance.balance(sesion, balance.periodo("dia", date(2026, 10, 5)))

    assert del_dia.cajas == []


def test_desde_el_parqueadero_no_se_corrige_una_caja_del_lavadero(sesion: Session):
    lavadero = del_lavadero(sesion, 5)

    with pytest.raises(CajaNoCorregible, match="no existe"):
        parqueadero.corregir_base(sesion, lavadero.id, base=0, motivo="Prueba", ahora=a_las(6, 9))
    with pytest.raises(CajaNoCorregible, match="no existe"):
        parqueadero.corregir_conteo(
            sesion, lavadero.id, efectivo_contado=10_000, motivo="Prueba", ahora=a_las(6, 9)
        )
    with pytest.raises(CobroNoCorregible, match="no existe"):
        cajas.caja_del_cobro(sesion, PARQUEADERO, lavadero.id, a_las(6, 9))
    # Desde el lavadero, sí.
    cajas.corregir_conteo(
        sesion, LAVADERO, lavadero.id, efectivo_contado=10_000, motivo="Era así", ahora=a_las(6, 9)
    )
    assert lavadero.diferencia == 0


def test_la_pantalla_de_una_caja_del_lavadero_no_existe_en_el_parqueadero(
    adentro: TestClient, sesion: Session
):
    lavadero = del_lavadero(sesion, 5)
    sesion.commit()

    assert adentro.get(f"/parqueadero/caja/{lavadero.id}").status_code == 404
    assert adentro.get(f"/parqueadero/caja/{lavadero.id}/ticket").status_code == 404


# --- La migración: las cajas que ya había son del parqueadero -------------------------------


@pytest.mark.usefixtures("motor")
def test_las_cajas_de_antes_quedan_del_parqueadero():
    url = (
        make_url(URL_PRUEBAS)
        .set(database="auto_ibaguer_pruebas_caja_por_negocio")
        .render_as_string(hide_password=False)
    )
    recrear_base(url)
    motor = create_engine(url)
    alembic = configuracion_alembic(url, configurar_logs=False)
    try:
        command.upgrade(alembic, "0011")
        with motor.begin() as conexion:
            conexion.execute(
                text(
                    "INSERT INTO caja_parqueadero "
                    "(base, abierta, cerrada_en, efectivo_contado, efectivo_esperado) "
                    "VALUES (5000, false, now(), 8000, 8000), (7000, true, NULL, NULL, NULL)"
                )
            )

        migrar(url, configurar_logs=False)

        with motor.begin() as conexion:
            filas = conexion.execute(
                text("SELECT id, negocio, base, abierta FROM caja ORDER BY id")
            ).all()
            assert filas == [(1, "parqueadero", 5000, False), (2, "parqueadero", 7000, True)]
            # El lavadero abre la suya aunque la del parqueadero siga abierta, y los números
            # siguen después de los que ya había.
            nueva = conexion.execute(
                text("INSERT INTO caja (negocio, base) VALUES ('lavadero', 0) RETURNING id")
            ).scalar_one()
            assert nueva == 3

        # Con cajas del lavadero no se puede volver atrás: no tendrían dónde quedar.
        with pytest.raises(Exception, match="El lavadero ya tiene cajas"):
            command.downgrade(alembic, "0011")
        with motor.begin() as conexion:
            conexion.execute(text("DELETE FROM caja WHERE negocio = 'lavadero'"))
        command.downgrade(alembic, "0011")
        with motor.begin() as conexion:
            assert conexion.execute(
                text("SELECT id, base FROM caja_parqueadero ORDER BY id")
            ).all() == [(1, 5000), (2, 7000)]
    finally:
        motor.dispose()
        borrar_base(url)
