"""Atajos para crear datos de prueba sin repetir código en cada prueba."""

import itertools
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalogo.tablas import Fila, Puesto, Servicio, TipoVehiculo
from app.clientes import servicio as clientes
from app.clientes.tablas import Cliente, Vehiculo
from app.integraciones.tablas import MensajeSaliente
from app.turnos import casos_de_uso as casos
from app.turnos.estados import EstadoTurno, OrigenTurno, TipoAviso
from app.turnos.tablas import Turno, TurnoHistorial

DIA_DE_PRUEBA = date(2026, 10, 26)

BOGOTA = ZoneInfo("America/Bogota")
# Lunes 26 de octubre de 2026 a las 8:00 a.m. en Bogotá: el lavadero está abierto.
LUNES_8AM = datetime(2026, 10, 26, 8, 0, tzinfo=BOGOTA)

_telefonos = itertools.count(573000000001)


def minutos(cantidad: int) -> timedelta:
    return timedelta(minutes=cantidad)


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


# --- Catálogo --------------------------------------------------------------------------


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


# --- Registros sueltos (para probar las reglas de la base) -----------------------------


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
    """Crea un turno a mano, sin pasar por las reglas. Con guardar=False solo lo arma."""
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
    if estado == EstadoTurno.EN_LAVADO:
        turno.en_lavado_desde = datetime.now(UTC)
    if guardar:
        sesion.add(turno)
        sesion.flush()
    return turno


# --- Por los casos de uso (como lo harán el bot y el dashboard) ------------------------


def cliente_con_vehiculo(
    sesion: Session,
    *,
    tipo: str = "Automóvil",
    placa: str | None = None,
    telefono: str | None = None,
    sin_telefono: bool = False,
    nombre: str = "Cliente",
) -> Vehiculo:
    cliente = clientes.registrar_cliente(
        sesion,
        nombre=nombre,
        telefono=None if sin_telefono else (telefono or str(next(_telefonos))),
        acepta_datos=True,
        ahora=LUNES_8AM,
    )
    return clientes.registrar_vehiculo(
        sesion, cliente_id=cliente.id, tipo_vehiculo_id=tipo_vehiculo(sesion, tipo).id, placa=placa
    )


def por_whatsapp(
    sesion: Session,
    *,
    ahora: datetime = LUNES_8AM,
    tipo: str = "Automóvil",
    placa: str | None = None,
    servicio_id: int | None = None,
    vehiculo: Vehiculo | None = None,
) -> casos.TurnoNuevo:
    vehiculo = vehiculo or cliente_con_vehiculo(sesion, tipo=tipo, placa=placa)
    return casos.pedir_turno(
        sesion,
        vehiculo_id=vehiculo.id,
        origen=OrigenTurno.WHATSAPP,
        servicio_id=servicio_id,
        ahora=ahora,
    )


def en_el_local(
    sesion: Session,
    *,
    ahora: datetime = LUNES_8AM,
    tipo: str = "Automóvil",
    placa: str | None = None,
    servicio_id: int | None = None,
    sin_telefono: bool = False,
    vehiculo: Vehiculo | None = None,
) -> casos.TurnoNuevo:
    """Turno creado desde el dashboard, con el vehículo ya en el local."""
    vehiculo = vehiculo or cliente_con_vehiculo(
        sesion, tipo=tipo, placa=placa, sin_telefono=sin_telefono
    )
    return casos.pedir_turno(
        sesion,
        vehiculo_id=vehiculo.id,
        origen=OrigenTurno.DASHBOARD,
        servicio_id=servicio_id,
        ahora=ahora,
    )


def ocupar_puestos(sesion: Session, *nombres: str, ahora: datetime = LUNES_8AM) -> list[int]:
    """Pone un vehículo lavándose en cada puesto indicado. Devuelve los ids de esos turnos."""
    turnos = []
    for nombre in nombres:
        tipo = "Moto" if nombre.startswith("M") else "Automóvil"
        nuevo = en_el_local(sesion, ahora=ahora, tipo=tipo)
        casos.pasar_a_puesto(sesion, nuevo.turno.id, puesto(sesion, nombre).id, ahora=ahora)
        turnos.append(nuevo.turno.id)
    return turnos


def turno_listo(
    sesion: Session,
    *,
    placa: str | None = "ABC123",
    nombre_servicio: str | None = "Básico",
    tipo: str = "Automóvil",
    ahora: datetime = LUNES_8AM,
) -> int:
    """Un turno que ya se lavó en C1 y está Listo para cobrar."""
    servicio_id = servicio(sesion, nombre_servicio).id if nombre_servicio else None
    nuevo = en_el_local(sesion, ahora=ahora, tipo=tipo, placa=placa, servicio_id=servicio_id)
    casos.pasar_a_puesto(sesion, nuevo.turno.id, puesto(sesion, "C1").id, ahora=ahora)
    casos.marcar_listo(sesion, nuevo.turno.id, ahora=ahora + minutos(50))
    return nuevo.turno.id


def estado(sesion: Session, turno_id: int) -> EstadoTurno:
    return sesion.get(Turno, turno_id).estado


def avisos(
    sesion: Session, *, turno_id: int | None = None, tipo: TipoAviso | None = None
) -> list[MensajeSaliente]:
    consulta = select(MensajeSaliente).order_by(MensajeSaliente.id)
    if turno_id is not None:
        consulta = consulta.where(MensajeSaliente.turno_id == turno_id)
    if tipo is not None:
        consulta = consulta.where(MensajeSaliente.tipo == tipo.value)
    return list(sesion.scalars(consulta))


def tipos_de_aviso(sesion: Session, turno_id: int) -> list[str]:
    return [mensaje.tipo for mensaje in avisos(sesion, turno_id=turno_id)]


def historial(sesion: Session, turno_id: int) -> list[TurnoHistorial]:
    consulta = (
        select(TurnoHistorial)
        .where(TurnoHistorial.turno_id == turno_id)
        .order_by(TurnoHistorial.id)
    )
    return list(sesion.scalars(consulta))
