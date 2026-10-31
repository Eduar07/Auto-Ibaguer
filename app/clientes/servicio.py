"""Registrar clientes y vehículos, y resolver las placas.

Reglas del documento que viven aquí:
- El teléfono identifica al cliente y el nombre se guarda solo la primera vez.
- Ley 1581: sin aceptar el aviso de datos no se guarda nada de la persona.
- Placa única pero opcional: una placa nunca queda en dos vehículos ni con dos clientes.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import tiempo
from app.catalogo.tablas import TipoVehiculo
from app.clientes.errores import FaltaAutorizacion, NoExiste, PlacaDeOtroCliente
from app.clientes.reglas import nombre_bonito, normalizar_telefono, validar_placa
from app.clientes.tablas import Cliente, Vehiculo


def buscar_por_telefono(sesion: Session, telefono: str) -> Cliente | None:
    consulta = select(Cliente).where(Cliente.telefono == normalizar_telefono(telefono))
    return sesion.scalars(consulta).one_or_none()


def registrar_cliente(
    sesion: Session,
    *,
    nombre: str,
    acepta_datos: bool,
    telefono: str | None = None,
    ahora: datetime | None = None,
) -> Cliente:
    """Crea el cliente, o devuelve el que ya existe con ese teléfono.

    Si ya existía, conserva su nombre: el bot no vuelve a preguntarlo.
    """
    if not acepta_datos:
        raise FaltaAutorizacion("Sin aceptar el aviso de tratamiento de datos no se guarda nada.")
    ahora = ahora or tiempo.ahora()
    numero = normalizar_telefono(telefono) if telefono else None
    if numero is not None:
        existente = sesion.scalars(select(Cliente).where(Cliente.telefono == numero)).one_or_none()
        if existente is not None:
            if not existente.acepta_datos:
                existente.acepta_datos = True
                existente.fecha_aceptacion = ahora
            return existente
    cliente = Cliente(
        telefono=numero,
        nombre=nombre_bonito(nombre),
        acepta_datos=True,
        fecha_aceptacion=ahora,
    )
    sesion.add(cliente)
    sesion.flush()
    return cliente


def _tipo_de_vehiculo(sesion: Session, tipo_vehiculo_id: int) -> TipoVehiculo:
    tipo = sesion.get(TipoVehiculo, tipo_vehiculo_id)
    if tipo is None:
        raise NoExiste(f"No existe el tipo de vehículo {tipo_vehiculo_id}.")
    return tipo


def registrar_vehiculo(
    sesion: Session, *, cliente_id: int, tipo_vehiculo_id: int, placa: str | None = None
) -> Vehiculo:
    """El vehículo con el que el cliente pide turno.

    Con placa: si ya existe y es de este cliente, se usa ese; si es de otro cliente, se
    rechaza. Sin placa (placa pendiente): se reutiliza el vehículo sin placa de ese tipo
    que el cliente ya tenga, para no llenar la base de vehículos repetidos.
    """
    if sesion.get(Cliente, cliente_id) is None:
        raise NoExiste(f"No existe el cliente {cliente_id}.")
    tipo = _tipo_de_vehiculo(sesion, tipo_vehiculo_id)
    if placa:
        limpia = validar_placa(placa, tipo.patron_placa, tipo.nombre)
        existente = sesion.scalars(select(Vehiculo).where(Vehiculo.placa == limpia)).one_or_none()
        if existente is not None:
            if existente.cliente_id != cliente_id:
                raise PlacaDeOtroCliente(f"La placa {limpia} ya está registrada con otro cliente.")
            return existente
        vehiculo = Vehiculo(cliente_id=cliente_id, tipo_vehiculo_id=tipo.id, placa=limpia)
    else:
        consulta = (
            select(Vehiculo)
            .where(
                Vehiculo.cliente_id == cliente_id,
                Vehiculo.tipo_vehiculo_id == tipo.id,
                Vehiculo.placa.is_(None),
            )
            .order_by(Vehiculo.id)
        )
        existente = sesion.scalars(consulta).first()
        if existente is not None:
            return existente
        vehiculo = Vehiculo(cliente_id=cliente_id, tipo_vehiculo_id=tipo.id)
    sesion.add(vehiculo)
    sesion.flush()
    return vehiculo


def vehiculo_con_placa(sesion: Session, vehiculo: Vehiculo, texto: str) -> Vehiculo:
    """Resuelve la placa que el administrador escribe para el vehículo de un turno.

    Devuelve el vehículo que debe quedar en el turno:
    - Si nadie tiene esa placa y el vehículo está sin placa, se la pone.
    - Si nadie la tiene y el vehículo ya tiene otra, es otro vehículo del mismo cliente:
      se crea uno nuevo, para no cambiarles la placa a los turnos viejos.
    - Si ya es de otro vehículo de este cliente, se usa ese.
    - Si es de otro cliente, se rechaza.
    """
    tipo = _tipo_de_vehiculo(sesion, vehiculo.tipo_vehiculo_id)
    placa = validar_placa(texto, tipo.patron_placa, tipo.nombre)
    if vehiculo.placa == placa:
        return vehiculo
    propietario = sesion.scalars(select(Vehiculo).where(Vehiculo.placa == placa)).one_or_none()
    if propietario is not None:
        if propietario.cliente_id != vehiculo.cliente_id:
            raise PlacaDeOtroCliente(f"La placa {placa} ya está registrada con otro cliente.")
        return propietario
    if vehiculo.placa is None:
        vehiculo.placa = placa
        sesion.flush()
        return vehiculo
    nuevo = Vehiculo(
        cliente_id=vehiculo.cliente_id, tipo_vehiculo_id=vehiculo.tipo_vehiculo_id, placa=placa
    )
    sesion.add(nuevo)
    sesion.flush()
    return nuevo
