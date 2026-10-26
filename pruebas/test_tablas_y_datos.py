"""Hito 2: las 16 tablas existen y los datos de Auto Ibaguer quedan cargados."""

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, select
from sqlalchemy.orm import Session

from app.catalogo.datos_iniciales import cargar_datos_iniciales
from app.catalogo.tablas import (
    Configuracion,
    Fila,
    Puesto,
    Servicio,
    ServicioPrecio,
    TipoVehiculo,
)
from app.cli import configuracion_alembic
from pruebas.base_de_pruebas import URL_PRUEBAS

TABLAS_DE_LA_FASE_1 = {
    # Negocio (13)
    "fila",
    "puesto",
    "tipo_vehiculo",
    "servicio",
    "servicio_precio",
    "configuracion",
    "cliente",
    "vehiculo",
    "consulta",
    "turno",
    "turno_historial",
    "pago",
    "sesion_bot",
    # Técnicas (3)
    "administrador",
    "mensaje_entrante",
    "mensaje_saliente",
}

MENU_ESPERADO = {
    ("Básico", "Automóvil"): 40_000,
    ("Básico", "Camioneta"): 50_000,
    ("Encerado", "Automóvil"): 60_000,
    ("Encerado", "Camioneta"): 70_000,
    ("General", "Automóvil"): 120_000,
    ("General", "Camioneta"): 140_000,
    ("Premium", "Automóvil"): 160_000,
    ("Premium", "Camioneta"): 220_000,
    ("Empelotado", "Automóvil"): 300_000,
    ("Empelotado", "Camioneta"): 400_000,
    ("Básico", "Moto"): 18_000,
    ("Intermedio", "Moto"): 25_000,
    ("Premium", "Moto"): 35_000,
}


def contar_registros(sesion: Session) -> dict[str, int]:
    modelos = (Fila, Puesto, TipoVehiculo, Servicio, ServicioPrecio, Configuracion)
    return {m.__tablename__: len(sesion.scalars(select(m)).all()) for m in modelos}


def test_existen_las_16_tablas(motor: Engine):
    tablas = set(inspect(motor).get_table_names()) - {"alembic_version"}
    assert tablas == TABLAS_DE_LA_FASE_1


@pytest.mark.usefixtures("motor")
def test_los_modelos_y_la_migracion_coinciden():
    # Falla si alguien cambia una tabla en el código y olvida crear su migración.
    command.check(configuracion_alembic(URL_PRUEBAS, configurar_logs=False))


def test_filas_y_puestos(sesion: Session):
    filas = {f.nombre: f for f in sesion.scalars(select(Fila))}
    assert {nombre: f.prefijo for nombre, f in filas.items()} == {"Carros": "C", "Motos": "M"}

    puestos = {}
    for puesto in sesion.scalars(select(Puesto).order_by(Puesto.nombre)):
        puestos.setdefault(puesto.fila_id, []).append(puesto.nombre)
    assert puestos[filas["Carros"].id] == ["C1", "C2"]
    assert puestos[filas["Motos"].id] == ["M1", "M2", "M3", "M4"]


def test_menu_con_precios_y_duracion(sesion: Session):
    filas = (
        select(
            Servicio.nombre, TipoVehiculo.nombre, ServicioPrecio.precio, ServicioPrecio.duracion_min
        )
        .join(Servicio, Servicio.id == ServicioPrecio.servicio_id)
        .join(TipoVehiculo, TipoVehiculo.id == ServicioPrecio.tipo_vehiculo_id)
        .where(ServicioPrecio.activo)
    )
    precios = {(servicio, tipo): precio for servicio, tipo, precio, _ in sesion.execute(filas)}
    duraciones = {duracion for *_, duracion in sesion.execute(filas)}

    assert precios == MENU_ESPERADO
    assert duraciones == {60}


def test_cada_tipo_de_vehiculo_va_a_su_fila(sesion: Session):
    tipos = sesion.execute(
        select(TipoVehiculo.nombre, Fila.nombre).join(Fila, Fila.id == TipoVehiculo.fila_id)
    )
    assert dict(tipos.all()) == {"Automóvil": "Carros", "Camioneta": "Carros", "Moto": "Motos"}


def test_parametros_del_negocio(sesion: Session):
    parametros = {c.clave: c.valor for c in sesion.scalars(select(Configuracion))}
    assert parametros["nombre_negocio"] == "Auto Ibaguer"
    assert parametros["telefono_contacto"] == "573227556850"
    assert parametros["hora_apertura"] == "05:30"
    assert parametros["hora_cierre"] == "19:30"
    assert parametros["gracia_minutos"] == 15
    assert parametros["avisar_cuando_falten"] == 2
    assert parametros["bot_activo"] is True


def test_cargar_los_datos_otra_vez_no_duplica_nada(sesion: Session):
    antes = contar_registros(sesion)
    creados = cargar_datos_iniciales(sesion)
    assert set(creados.values()) == {0}
    assert contar_registros(sesion) == antes


def test_cargar_los_datos_no_borra_cambios_del_administrador(sesion: Session):
    gracia = sesion.get(Configuracion, "gracia_minutos")
    gracia.valor = 20
    sesion.flush()

    cargar_datos_iniciales(sesion)

    sesion.refresh(gracia)
    assert gracia.valor == 20
