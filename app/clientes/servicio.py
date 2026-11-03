"""Registrar clientes y vehículos, resolver las placas y borrar datos a pedido.

Reglas del documento que viven aquí:
- El teléfono identifica al cliente y el nombre se guarda solo la primera vez.
- Ley 1581: sin aceptar el aviso de datos no se guarda nada de la persona, y si pide que
  se borren sus datos, se borran.
- Placa única pero opcional: una placa nunca queda en dos vehículos ni con dos clientes.
"""

import re
from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app import tiempo
from app.bot.tablas import SesionBot
from app.catalogo.tablas import Fila, TipoVehiculo
from app.clientes.errores import (
    FaltaAutorizacion,
    NoExiste,
    PlacaDeOtroCliente,
    TelefonoDeOtroCliente,
    TieneTurnoActivo,
)
from app.clientes.reglas import nombre_bonito, normalizar_telefono, validar_placa
from app.clientes.tablas import Cliente, Consulta, TipoConsulta, Vehiculo
from app.integraciones.tablas import MensajeEntrante, MensajeSaliente
from app.turnos.estados import ESTADOS_ACTIVOS
from app.turnos.reglas import numero_de_turno
from app.turnos.tablas import Pago, Turno, TurnoHistorial

# Así queda el nombre de quien pidió borrar sus datos.
NOMBRE_BORRADO = "Cliente borrado"


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


def registrar_consulta(
    sesion: Session,
    *,
    tipo: TipoConsulta,
    cliente_id: int | None = None,
    tipo_vehiculo_id: int | None = None,
    ahora: datetime | None = None,
) -> Consulta:
    """Alguien preguntó precios o cómo va la fila. Sirve para medir cuántos preguntan y no
    vienen. Sin aviso de datos aceptado se guarda sin cliente: solo cuenta."""
    consulta = Consulta(
        cliente_id=cliente_id,
        tipo=tipo,
        tipo_vehiculo_id=tipo_vehiculo_id,
        fecha_hora=ahora or tiempo.ahora(),
    )
    sesion.add(consulta)
    sesion.flush()
    return consulta


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


# --- Editar y borrar ---------------------------------------------------------------------


def cliente_vigente(sesion: Session, cliente_id: int) -> Cliente:
    """El cliente, si existe y no ha pedido borrar sus datos."""
    cliente = sesion.get(Cliente, cliente_id)
    if cliente is None or cliente.datos_borrados_en is not None:
        raise NoExiste("Ese cliente no existe o pidió borrar sus datos.")
    return cliente


def editar_cliente(sesion: Session, cliente_id: int, *, nombre: str, telefono: str) -> Cliente:
    """El administrador corrige el nombre o el celular de un cliente.

    Un celular vacío lo deja sin celular (no recibe avisos). Si el celular ya es de otro
    cliente, se rechaza: el celular es lo que identifica a cada uno.
    """
    cliente = cliente_vigente(sesion, cliente_id)
    cliente.nombre = nombre_bonito(nombre)
    numero = normalizar_telefono(telefono) if telefono.strip() else None
    if numero is not None and numero != cliente.telefono:
        otro = sesion.scalars(select(Cliente).where(Cliente.telefono == numero)).one_or_none()
        if otro is not None:
            raise TelefonoDeOtroCliente(f"Ese celular ya es de otro cliente: {otro.nombre}.")
    cliente.telefono = numero
    sesion.flush()
    return cliente


def _sin_datos(texto: str | None, datos: list[str]) -> str | None:
    """Cambia cada dato personal que aparezca en el texto por "[borrado]"."""
    if not texto:
        return texto
    for dato in datos:
        texto = re.sub(rf"\b{re.escape(dato)}\b", "[borrado]", texto, flags=re.IGNORECASE)
    return texto


def borrar_datos(sesion: Session, cliente_id: int, *, ahora: datetime | None = None) -> str:
    """Ley 1581: borra los datos personales de un cliente que lo pide. Devuelve su nombre.

    Se borran su nombre, su celular y sus placas, y también lo que quedó escrito con esos
    datos: los mensajes de WhatsApp, su conversación con el bot y las notas del historial
    o de los cobros. Sus turnos y cobros quedan, sin datos personales, para que la
    contabilidad siga cuadrando. No se puede deshacer.
    """
    ahora = ahora or tiempo.ahora()
    cliente = cliente_vigente(sesion, cliente_id)
    activo = sesion.execute(
        select(Turno.consecutivo, Fila.prefijo)
        .join(Fila, Fila.id == Turno.fila_id)
        .where(Turno.cliente_id == cliente.id, Turno.estado.in_(ESTADOS_ACTIVOS))
        .limit(1)
    ).first()
    if activo is not None:
        numero = numero_de_turno(activo.prefijo, activo.consecutivo)
        raise TieneTurnoActivo(
            f"Tiene el turno {numero} en la fila. Cóbralo o cancélalo antes de borrar sus datos."
        )

    nombre = cliente.nombre
    vehiculos = list(sesion.scalars(select(Vehiculo).where(Vehiculo.cliente_id == cliente.id)))
    datos = [v.placa for v in vehiculos if v.placa]
    datos += [parte for parte in nombre.split() if len(parte.strip(".")) >= 3]
    if cliente.telefono:
        datos += [cliente.telefono, cliente.telefono.removeprefix("57")]

    turnos = select(Turno.id).where(Turno.cliente_id == cliente.id).scalar_subquery()
    for paso in sesion.scalars(select(TurnoHistorial).where(TurnoHistorial.turno_id.in_(turnos))):
        paso.detalle = _sin_datos(paso.detalle, datos)
    for pago in sesion.scalars(select(Pago).where(Pago.turno_id.in_(turnos))):
        pago.motivo_ajuste = _sin_datos(pago.motivo_ajuste, datos)

    if cliente.telefono:
        sesion.execute(delete(MensajeEntrante).where(MensajeEntrante.telefono == cliente.telefono))
    sesion.execute(
        delete(MensajeSaliente).where(
            or_(
                MensajeSaliente.cliente_id == cliente.id,
                MensajeSaliente.telefono == cliente.telefono,
            )
        )
    )
    sesion.execute(
        delete(SesionBot).where(
            or_(SesionBot.cliente_id == cliente.id, SesionBot.telefono == cliente.telefono)
        )
    )
    # Las consultas quedan para contar cuánta gente pregunta, pero ya sin saber quién.
    sesion.execute(
        update(Consulta).where(Consulta.cliente_id == cliente.id).values(cliente_id=None)
    )

    for vehiculo in vehiculos:
        vehiculo.placa = None
    cliente.nombre = NOMBRE_BORRADO
    cliente.telefono = None
    cliente.acepta_datos = False
    cliente.fecha_aceptacion = None
    cliente.datos_borrados_en = ahora
    sesion.flush()
    return nombre
