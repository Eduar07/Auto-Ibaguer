"""Atajos para crear datos de prueba sin repetir código en cada prueba."""

import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalogo.tablas import Fila, Puesto, Servicio, TipoVehiculo
from app.clientes.tablas import Cliente, Vehiculo
from app.turnos.tablas import EstadoTurno, OrigenTurno, Turno

DIA_DE_PRUEBA = date(2026, 10, 26)

_telefonos = itertools.count(573000000001)


@contextmanager
def debe_rechazar(sesion: Session, regla: str) -> Iterator[None]:
    """Comprueba que PostgreSQL rechace el cambio y que lo haga por la regla indicada.

    Uso:
        with debe_rechazar(sesion, "uq_cliente_telefono"):
            sesion.add(cliente_repetido)
    """
    with pytest.raises(IntegrityError) as error:
        with sesion.begin_nested():
            yield
            sesion.flush()
    assert regla in str(error.value), f"Se rechazó, pero no por '{regla}': {error.value}"


def fila(sesion: Session, nombre: str = "Carros") -> Fila:
    return sesion.scalars(select(Fila).filter_by(nombre=nombre)).one()


def puesto(sesion: Session, nombre: str = "C1") -> Puesto:
    return sesion.scalars(select(Puesto).filter_by(nombre=nombre)).one()


def tipo_vehiculo(sesion: Session, nombre: str = "Automóvil") -> TipoVehiculo:
    return sesion.scalars(select(TipoVehiculo).filter_by(nombre=nombre)).one()


def servicio(sesion: Session, nombre: str = "Básico", nombre_fila: str = "Carros") -> Servicio:
    return sesion.scalars(
        select(Servicio).filter_by(nombre=nombre, fila_id=fila(sesion, nombre_fila).id)
    ).one()


def nuevo_cliente(sesion: Session, telefono: str | None = None, nombre: str = "Cliente") -> Cliente:
    cliente = Cliente(
        telefono=telefono or str(next(_telefonos)),
        nombre=nombre,
        acepta_datos=True,
        fecha_aceptacion=datetime.now(UTC),
    )
    sesion.add(cliente)
    sesion.flush()
    return cliente


def nuevo_vehiculo(
    sesion: Session,
    cliente: Cliente | None = None,
    tipo: str = "Automóvil",
    placa: str | None = None,
) -> Vehiculo:
    cliente = cliente or nuevo_cliente(sesion)
    vehiculo = Vehiculo(
        cliente_id=cliente.id, tipo_vehiculo_id=tipo_vehiculo(sesion, tipo).id, placa=placa
    )
    sesion.add(vehiculo)
    sesion.flush()
    return vehiculo


def nuevo_turno(
    sesion: Session,
    consecutivo: int = 1,
    nombre_fila: str = "Carros",
    fecha: date = DIA_DE_PRUEBA,
    estado: EstadoTurno = EstadoTurno.EN_FILA,
    nombre_puesto: str | None = None,
    guardar: bool = True,
) -> Turno:
    """Crea un turno con su cliente y vehículo. Con guardar=False solo lo arma, sin enviarlo."""
    tipo = "Moto" if nombre_fila == "Motos" else "Automóvil"
    vehiculo = nuevo_vehiculo(sesion, tipo=tipo)
    turno = Turno(
        fila_id=fila(sesion, nombre_fila).id,
        fecha=fecha,
        consecutivo=consecutivo,
        cliente_id=vehiculo.cliente_id,
        vehiculo_id=vehiculo.id,
        estado=estado,
        origen=OrigenTurno.WHATSAPP,
        puesto_id=puesto(sesion, nombre_puesto).id if nombre_puesto else None,
    )
    if estado == EstadoTurno.LLAMADO:
        turno.llamado_en = datetime.now(UTC)
        turno.gracia_hasta = turno.llamado_en + timedelta(minutes=15)
    if guardar:
        sesion.add(turno)
        sesion.flush()
    return turno
