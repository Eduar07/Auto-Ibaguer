"""Lo que muestra la pantalla "Fila en vivo", armado de una sola vez.

Es la capa de presentación: junta cada turno con su cliente, vehículo, servicio y
precio, y calcula lo que la pantalla necesita (cuánto lleva cada lavado, quién debería
entrar al próximo puesto, cuánto se ha cobrado hoy). Solo lee: no cambia nada.
"""

from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.catalogo.parametros import Parametros, leer_parametros
from app.catalogo.tablas import Fila, Servicio, ServicioPrecio, TipoVehiculo
from app.clientes.tablas import Cliente, Vehiculo
from app.formato import hora_con_articulo
from app.turnos import consultas, datos, reglas
from app.turnos.estados import ESTADOS_ACTIVOS, Actor, EstadoTurno, OrigenTurno
from app.turnos.tablas import Pago, Turno, TurnoHistorial


@dataclass(frozen=True)
class TurnoEnPantalla:
    id: int
    numero: str
    fila_id: int
    estado: EstadoTurno
    nombre: str
    telefono: str | None
    por_whatsapp: bool
    tipo_vehiculo: str
    tipo_vehiculo_id: int
    placa: str | None
    servicio: str | None
    servicio_id: int | None
    precio: int | None  # precio del menú para ese vehículo; None si el servicio está por definir
    duracion_estimada_min: int
    en_camino: bool
    avisado: bool
    intentos_sin_llegar: int
    gracia_hasta: datetime | None
    en_lavado_desde: datetime | None
    listo_desde: datetime | None
    adelante: int | None  # turnos adelante en la fila de espera
    espera_min: int | None  # minutos estimados para entrar a un puesto

    @property
    def placa_pendiente(self) -> bool:
        return self.placa is None

    @property
    def servicio_por_definir(self) -> bool:
        return self.servicio_id is None


@dataclass(frozen=True)
class PuestoEnPantalla:
    id: int
    nombre: str
    turno: TurnoEnPantalla | None  # el vehículo que se está lavando
    mensaje: str = ""  # si está libre: a quién se está esperando


@dataclass(frozen=True)
class FilaEnPantalla:
    id: int
    nombre: str
    puestos: list[PuestoEnPantalla]
    espera: list[TurnoEnPantalla]
    siguiente_id: int | None  # quien debería entrar al próximo puesto libre
    espera_nuevo_min: int | None  # cuánto esperaría un turno nuevo
    aviso_puesto_no_espera: str | None

    @property
    def libres(self) -> list[PuestoEnPantalla]:
        return [p for p in self.puestos if p.turno is None]

    @property
    def en_lavado(self) -> int:
        return sum(1 for p in self.puestos if p.turno is not None)


@dataclass(frozen=True)
class Movimiento:
    hora: datetime
    texto: str


@dataclass(frozen=True)
class Tablero:
    ahora: datetime
    filas: list[FilaEnPantalla]
    por_cobrar: list[TurnoEnPantalla]
    cobrado_hoy: int
    cobros_hoy: int
    actividad: list[Movimiento]
    bot_activo: bool

    @property
    def en_lavado(self) -> int:
        return sum(fila.en_lavado for fila in self.filas)

    @property
    def en_espera(self) -> int:
        return sum(len(fila.espera) for fila in self.filas)


def inicio_del_dia(ahora: datetime) -> datetime:
    """Las 12:00 a.m. de hoy en Bogotá."""
    return datetime.combine(tiempo.hora_local(ahora).date(), time(0), tzinfo=tiempo.zona_horaria())


def _precios(sesion: Session) -> dict[tuple[int, int], int]:
    consulta = select(
        ServicioPrecio.servicio_id, ServicioPrecio.tipo_vehiculo_id, ServicioPrecio.precio
    ).where(ServicioPrecio.activo)
    return {(servicio, tipo): precio for servicio, tipo, precio in sesion.execute(consulta)}


def _listos_desde(sesion: Session, turno_ids: list[int]) -> dict[int, datetime]:
    if not turno_ids:
        return {}
    consulta = (
        select(TurnoHistorial.turno_id, func.max(TurnoHistorial.fecha_hora))
        .where(
            TurnoHistorial.turno_id.in_(turno_ids),
            TurnoHistorial.estado_nuevo == EstadoTurno.LISTO,
        )
        .group_by(TurnoHistorial.turno_id)
    )
    return {turno_id: momento for turno_id, momento in sesion.execute(consulta)}


def _mensaje_puesto_libre(candidato: TurnoEnPantalla | None) -> str:
    if candidato is None:
        return "Nadie en espera."
    if candidato.estado == EstadoTurno.LLAMADO and candidato.gracia_hasta is not None:
        limite = hora_con_articulo(tiempo.hora_local(candidato.gracia_hasta))
        return f"Llamamos a {candidato.numero}. Tiene hasta {limite} para llegar."
    if candidato.estado == EstadoTurno.LLEGO:
        return f"{candidato.numero} ya está en el local: puede pasar."
    return f"Sigue {candidato.numero}."


def _aviso_puesto_no_espera(fila: list[TurnoEnPantalla], siguiente_id: int | None) -> str | None:
    """El aviso de la regla "el puesto no espera", cuando aplica."""
    if not fila or siguiente_id is None or fila[0].id == siguiente_id:
        return None
    primero = fila[0]
    if primero.estado != EstadoTurno.LLAMADO:
        return None
    siguiente = next(t for t in fila if t.id == siguiente_id)
    como_viene = "viene en camino" if primero.en_camino else "todavía no llega"
    return (
        f"{primero.numero} {como_viene}. {siguiente.numero} ya está en el local y puede pasar "
        f"primero; {primero.numero} conserva su tiempo para llegar."
    )


def _texto_del_movimiento(numero: str, paso: TurnoHistorial) -> str:
    anterior, nuevo = paso.estado_anterior, paso.estado_nuevo
    detalle = paso.detalle or ""
    if anterior is None:
        if paso.actor == Actor.CLIENTE:
            return f"{numero} pidió turno por WhatsApp"
        return f"{numero} creado en el local"
    if anterior == nuevo:
        return f"{numero} corregido: {detalle}"
    textos = {
        EstadoTurno.LLAMADO: f"{numero} llamado",
        EstadoTurno.LLEGO: f"{numero} llegó",
        # "Puesto M4" -> "pasó al puesto M4": solo la primera letra va en minúscula.
        EstadoTurno.EN_LAVADO: f"{numero} pasó al {detalle[:1].lower()}{detalle[1:]}",
        EstadoTurno.LISTO: f"{numero} quedó listo",
        EstadoTurno.PAGADO: f"{numero} cobrado: {detalle}",
        EstadoTurno.CANCELADO: f"{numero} cancelado"
        + (" por WhatsApp" if paso.actor == Actor.CLIENTE else ""),
        EstadoTurno.NO_LLEGO: f"{numero} no llegó: perdió el turno",
        EstadoTurno.EN_FILA: f"{numero} no llegó a tiempo: pasó al final",
    }
    return textos[nuevo]


def _actividad(sesion: Session, ahora: datetime, limite: int) -> list[Movimiento]:
    consulta = (
        select(TurnoHistorial, Turno.consecutivo, Fila.prefijo)
        .join(Turno, Turno.id == TurnoHistorial.turno_id)
        .join(Fila, Fila.id == Turno.fila_id)
        .where(TurnoHistorial.fecha_hora >= inicio_del_dia(ahora))
        .order_by(TurnoHistorial.fecha_hora.desc(), TurnoHistorial.id.desc())
        .limit(limite)
    )
    return [
        Movimiento(
            hora=paso.fecha_hora,
            texto=_texto_del_movimiento(reglas.numero_de_turno(prefijo, consecutivo), paso),
        )
        for paso, consecutivo, prefijo in sesion.execute(consulta)
    ]


def armar_tablero(
    sesion: Session,
    ahora: datetime | None = None,
    *,
    parametros: Parametros | None = None,
    movimientos: int = 8,
) -> Tablero:
    ahora = ahora or tiempo.ahora()
    parametros = parametros or leer_parametros(sesion)
    filas = list(sesion.scalars(select(Fila).order_by(Fila.id)))
    prefijos = {fila.id: fila.prefijo for fila in filas}
    precios = _precios(sesion)

    consulta = (
        select(Turno, Cliente, Vehiculo, TipoVehiculo, Servicio)
        .join(Cliente, Cliente.id == Turno.cliente_id)
        .join(Vehiculo, Vehiculo.id == Turno.vehiculo_id)
        .join(TipoVehiculo, TipoVehiculo.id == Vehiculo.tipo_vehiculo_id)
        .outerjoin(Servicio, Servicio.id == Turno.servicio_id)
        .where(Turno.estado.in_(ESTADOS_ACTIVOS))
        .execution_options(populate_existing=True)
    )
    filas_de_turnos = sesion.execute(consulta).all()
    listos = _listos_desde(
        sesion, [t.id for t, *_ in filas_de_turnos if t.estado == EstadoTurno.LISTO]
    )

    fotos = {
        fila.id: consultas.foto_de_la_fila(sesion, fila.id, ahora, parametros) for fila in filas
    }
    duraciones = {
        fila.id: datos.Duraciones(sesion, fila.id, parametros.servicio_por_defecto)
        for fila in filas
    }

    en_pantalla: dict[int, TurnoEnPantalla] = {}
    for turno, cliente, vehiculo, tipo, servicio in filas_de_turnos:
        foto = fotos[turno.fila_id]
        duracion = duraciones[turno.fila_id].de(turno.servicio_id, tipo.id)
        en_pantalla[turno.id] = TurnoEnPantalla(
            id=turno.id,
            numero=reglas.numero_de_turno(prefijos[turno.fila_id], turno.consecutivo),
            fila_id=turno.fila_id,
            estado=turno.estado,
            nombre=cliente.nombre,
            telefono=cliente.telefono,
            por_whatsapp=turno.origen == OrigenTurno.WHATSAPP,
            tipo_vehiculo=tipo.nombre,
            tipo_vehiculo_id=tipo.id,
            placa=vehiculo.placa,
            servicio=servicio.nombre if servicio else None,
            servicio_id=turno.servicio_id,
            precio=precios.get((turno.servicio_id, tipo.id)) if turno.servicio_id else None,
            duracion_estimada_min=int(duracion.total_seconds() // 60),
            en_camino=turno.en_camino,
            avisado=turno.aviso_anticipado_en is not None,
            intentos_sin_llegar=turno.intentos_sin_llegar,
            gracia_hasta=turno.gracia_hasta if turno.estado == EstadoTurno.LLAMADO else None,
            en_lavado_desde=turno.en_lavado_desde,
            listo_desde=listos.get(turno.id),
            adelante=foto.adelante_de(turno.id),
            espera_min=reglas.minutos_hasta(foto.inicio_de(turno.id), ahora),
        )

    filas_en_pantalla = []
    for fila in filas:
        foto = fotos[fila.id]
        espera = [en_pantalla[turno.id] for turno in foto.espera]
        turnos_en_puesto = {
            turno.puesto_id: en_pantalla[turno.id] for turno in datos.en_lavado(sesion, fila.id)
        }
        candidatos = iter(espera)
        puestos = []
        for puesto in datos.puestos_activos(sesion, fila.id):
            ocupante = turnos_en_puesto.get(puesto.id)
            mensaje = "" if ocupante else _mensaje_puesto_libre(next(candidatos, None))
            puestos.append(PuestoEnPantalla(puesto.id, puesto.nombre, ocupante, mensaje))
        siguiente_id = reglas.elegir_siguiente_para_puesto(
            [reglas.TurnoEnEspera(t.id, t.estado, t.avisado) for t in espera]
        )
        filas_en_pantalla.append(
            FilaEnPantalla(
                id=fila.id,
                nombre=fila.nombre,
                puestos=puestos,
                espera=espera,
                siguiente_id=siguiente_id,
                espera_nuevo_min=reglas.minutos_hasta(foto.estimacion.siguiente, ahora),
                aviso_puesto_no_espera=_aviso_puesto_no_espera(espera, siguiente_id)
                if any(p.turno is None for p in puestos)
                else None,
            )
        )

    por_cobrar = sorted(
        (t for t in en_pantalla.values() if t.estado == EstadoTurno.LISTO),
        key=lambda t: (t.listo_desde or ahora, t.id),
    )
    cobrado, cobros = sesion.execute(
        select(func.coalesce(func.sum(Pago.precio_cobrado), 0), func.count(Pago.id)).where(
            Pago.fecha_hora >= inicio_del_dia(ahora)
        )
    ).one()

    return Tablero(
        ahora=ahora,
        filas=filas_en_pantalla,
        por_cobrar=por_cobrar,
        cobrado_hoy=int(cobrado),
        cobros_hoy=int(cobros),
        actividad=_actividad(sesion, ahora, movimientos),
        bot_activo=parametros.bot_activo,
    )
