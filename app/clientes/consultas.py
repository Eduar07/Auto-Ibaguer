"""Lo que se pregunta sobre los clientes sin cambiar nada: la lista y la ficha de cada uno."""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app import tiempo
from app.bot.tablas import SesionBot
from app.catalogo.tablas import Fila, Servicio, TipoVehiculo
from app.clientes.reglas import normalizar_placa
from app.clientes.servicio import cliente_vigente
from app.clientes.tablas import Cliente, Vehiculo
from app.turnos.estados import EstadoTurno, OrigenTurno
from app.turnos.reglas import numero_de_turno
from app.turnos.tablas import Pago, Turno

POR_PAGINA = 20

# Una visita es un turno que no se canceló ni se perdió: el vehículo vino o viene.
_NO_CUENTAN = (EstadoTurno.CANCELADO, EstadoTurno.NO_LLEGO)
_CON_TILDE, _SIN_TILDE = "áéíóúüñ", "aeiouun"


@dataclass(frozen=True)
class ClienteEnLista:
    id: int
    nombre: str
    telefono: str | None
    placas: list[str]
    visitas: int
    ultima_visita: date | None


@dataclass(frozen=True)
class PaginaDeClientes:
    clientes: list[ClienteEnLista]
    total: int  # los que coinciden con la búsqueda
    pagina: int
    por_pagina: int

    @property
    def hay_anterior(self) -> bool:
        return self.pagina > 1

    @property
    def hay_siguiente(self) -> bool:
        return self.pagina * self.por_pagina < self.total


def _sin_tildes(texto: str) -> str:
    return texto.lower().translate(str.maketrans(_CON_TILDE, _SIN_TILDE))


def contar_clientes(sesion: Session) -> int:
    """Clientes registrados (todos aceptaron el aviso de datos; los borrados no cuentan)."""
    return sesion.scalar(select(func.count(Cliente.id)).where(Cliente.datos_borrados_en.is_(None)))


def buscar_clientes(
    sesion: Session, texto: str = "", *, pagina: int = 1, por_pagina: int = POR_PAGINA
) -> PaginaDeClientes:
    """Los clientes que coinciden por nombre, celular o placa; los de visita más reciente primero.

    El nombre se busca sin importar tildes ni mayúsculas ("andres" encuentra a "Andrés").
    """
    cuenta = Turno.estado.not_in(_NO_CUENTAN)
    visitas = (
        select(func.count(Turno.id)).where(Turno.cliente_id == Cliente.id, cuenta).scalar_subquery()
    )
    ultima = select(func.max(Turno.fecha)).where(Turno.cliente_id == Cliente.id, cuenta)
    ultima = ultima.scalar_subquery()
    placas = (
        select(func.string_agg(Vehiculo.placa, ", "))
        .where(Vehiculo.cliente_id == Cliente.id, Vehiculo.placa.is_not(None))
        .scalar_subquery()
    )

    filtro = Cliente.datos_borrados_en.is_(None)
    texto = texto.strip()
    if texto:
        opciones = [
            func.translate(func.lower(Cliente.nombre), _CON_TILDE, _SIN_TILDE).contains(
                _sin_tildes(texto), autoescape=True
            )
        ]
        digitos = re.sub(r"\D", "", texto)
        if len(digitos) >= 3:
            opciones.append(Cliente.telefono.contains(digitos, autoescape=True))
        placa = normalizar_placa(texto)
        if len(placa) >= 3:
            opciones.append(
                exists().where(
                    Vehiculo.cliente_id == Cliente.id,
                    Vehiculo.placa.contains(placa, autoescape=True),
                )
            )
        filtro = filtro & or_(*opciones)

    total = sesion.scalar(select(func.count(Cliente.id)).where(filtro))
    pagina = max(1, pagina)
    consulta = (
        select(Cliente.id, Cliente.nombre, Cliente.telefono, placas, visitas, ultima)
        .where(filtro)
        .order_by(ultima.desc().nulls_last(), Cliente.id.desc())
        .limit(por_pagina)
        .offset((pagina - 1) * por_pagina)
    )
    clientes = [
        ClienteEnLista(
            id=fila.id,
            nombre=fila.nombre,
            telefono=fila.telefono,
            placas=fila[3].split(", ") if fila[3] else [],
            visitas=fila[4],
            ultima_visita=fila[5],
        )
        for fila in sesion.execute(consulta)
    ]
    return PaginaDeClientes(clientes, total, pagina, por_pagina)


@dataclass(frozen=True)
class VehiculoDelCliente:
    tipo: str
    placa: str | None


@dataclass(frozen=True)
class VisitaDelCliente:
    numero: str
    fecha: date
    estado: EstadoTurno
    tipo_vehiculo: str
    placa: str | None
    servicio: str | None
    cobrado: int | None


@dataclass(frozen=True)
class FichaDelCliente:
    id: int
    nombre: str
    telefono: str | None
    fecha_aceptacion: date | None
    escribio_por_whatsapp: bool
    visitas: int
    total_pagado: int
    servicio_frecuente: str | None
    vehiculos: list[VehiculoDelCliente]
    ultimas_visitas: list[VisitaDelCliente]
    turno_activo: str | None  # el número del turno que tiene en la fila, si tiene uno


def ficha_del_cliente(sesion: Session, cliente_id: int, *, ultimas: int = 8) -> FichaDelCliente:
    cliente = cliente_vigente(sesion, cliente_id)
    vehiculos = [
        VehiculoDelCliente(tipo, placa)
        for tipo, placa in sesion.execute(
            select(TipoVehiculo.nombre, Vehiculo.placa)
            .join(TipoVehiculo, TipoVehiculo.id == Vehiculo.tipo_vehiculo_id)
            .where(Vehiculo.cliente_id == cliente.id)
            .order_by(Vehiculo.placa.is_(None), Vehiculo.id)
        )
    ]
    turnos = sesion.execute(
        select(Turno, Fila.prefijo, TipoVehiculo.nombre, Vehiculo.placa, Servicio.nombre, Pago)
        .join(Fila, Fila.id == Turno.fila_id)
        .join(Vehiculo, Vehiculo.id == Turno.vehiculo_id)
        .join(TipoVehiculo, TipoVehiculo.id == Vehiculo.tipo_vehiculo_id)
        .outerjoin(Servicio, Servicio.id == Turno.servicio_id)
        .outerjoin(Pago, Pago.turno_id == Turno.id)
        .where(Turno.cliente_id == cliente.id)
        .order_by(Turno.fecha.desc(), Turno.id.desc())
    ).all()
    visitas = [
        VisitaDelCliente(
            numero=numero_de_turno(prefijo, turno.consecutivo),
            fecha=turno.fecha,
            estado=turno.estado,
            tipo_vehiculo=tipo,
            placa=placa,
            servicio=servicio,
            cobrado=pago.precio_cobrado if pago else None,
        )
        for turno, prefijo, tipo, placa, servicio, pago in turnos
    ]
    pagadas = [v for v in visitas if v.estado == EstadoTurno.PAGADO]
    frecuente = Counter(v.servicio for v in pagadas if v.servicio).most_common(1)
    escribio = any(turno.origen == OrigenTurno.WHATSAPP for turno, *_ in turnos) or bool(
        sesion.scalar(select(exists().where(SesionBot.cliente_id == cliente.id)))
    )
    terminados = (*_NO_CUENTAN, EstadoTurno.PAGADO)
    activo = next((v.numero for v in visitas if v.estado not in terminados), None)
    return FichaDelCliente(
        id=cliente.id,
        nombre=cliente.nombre,
        telefono=cliente.telefono,
        fecha_aceptacion=(
            tiempo.hora_local(cliente.fecha_aceptacion).date() if cliente.fecha_aceptacion else None
        ),
        escribio_por_whatsapp=escribio,
        visitas=sum(1 for v in visitas if v.estado not in _NO_CUENTAN),
        total_pagado=sum(v.cobrado or 0 for v in pagadas),
        servicio_frecuente=frecuente[0][0] if frecuente else None,
        vehiculos=vehiculos,
        ultimas_visitas=visitas[:ultimas],
        turno_activo=activo,
    )
