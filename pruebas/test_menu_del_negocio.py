"""La migración 0021 deja en la base que ya existe el menú con que abre el lavadero.

Se corre sobre el menú de las pruebas (cinco servicios de carro y tres de moto, como el
que tenía la base de la nube) y se revisa que quede como lo decidió el negocio.
"""

import importlib

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalogo.tablas import Fila, Servicio, ServicioPrecio, TipoVehiculo

migracion = importlib.import_module("migraciones.versions.0021_menu_basico_y_encerado")


def aplicar(sesion: Session) -> None:
    sesion.flush()
    contexto = MigrationContext.configure(sesion.connection())
    with Operations.context(contexto):
        migracion.upgrade()
    sesion.expire_all()


def menu(sesion: Session) -> dict[tuple[str, str, str], tuple[int, int]]:
    filas = sesion.execute(
        select(
            Fila.nombre,
            Servicio.nombre,
            TipoVehiculo.nombre,
            ServicioPrecio.precio,
            ServicioPrecio.duracion_min,
        )
        .join(Servicio, Servicio.id == ServicioPrecio.servicio_id)
        .join(Fila, Fila.id == Servicio.fila_id)
        .join(TipoVehiculo, TipoVehiculo.id == ServicioPrecio.tipo_vehiculo_id)
        .where(ServicioPrecio.activo, Servicio.activo)
    )
    return {(f, s, t): (precio, duracion) for f, s, t, precio, duracion in filas}


def test_queda_el_menu_del_negocio(sesion: Session):
    aplicar(sesion)

    assert menu(sesion) == {
        ("Carros", "Básico", "Automóvil"): (30_000, 45),
        ("Carros", "Básico", "Camioneta"): (40_000, 45),
        ("Carros", "Encerado", "Automóvil"): (60_000, 45),
        ("Carros", "Encerado", "Camioneta"): (70_000, 45),
        ("Motos", "Básico", "Moto"): (15_000, 45),
        ("Motos", "Encerado", "Moto"): (20_000, 45),
    }
    encerado = sesion.scalars(select(Servicio).where(Servicio.nombre == "Encerado")).first()
    assert "polichadora" in encerado.descripcion


def test_lo_demas_se_oculta_y_los_precios_viejos_quedan_como_historia(sesion: Session):
    aplicar(sesion)

    ocultos = set(sesion.scalars(select(Servicio.nombre).where(~Servicio.activo)))
    assert ocultos == {"General", "Premium", "Empelotado", "Intermedio"}
    viejos = sesion.scalars(
        select(ServicioPrecio.precio).where(~ServicioPrecio.activo, ServicioPrecio.precio == 40_000)
    ).all()
    assert viejos  # el Básico de automóvil a $40.000 sigue en la historia


def test_correrla_dos_veces_no_cambia_nada(sesion: Session):
    aplicar(sesion)
    antes = menu(sesion), len(sesion.scalars(select(ServicioPrecio)).all())

    aplicar(sesion)

    assert (menu(sesion), len(sesion.scalars(select(ServicioPrecio)).all())) == antes
