"""Acceso a los datos de la fila: los bloqueos y las lecturas que comparten los casos de uso.

Sin choques: todo cambio empieza bloqueando el registro de su fila (SELECT ... FOR
UPDATE). Si dos personas tocan un botón al mismo tiempo, la segunda espera a que la
primera termine y trabaja con lo que quedó. Siempre se bloquea primero la fila y después
el turno, en ese orden, para que dos operaciones nunca se queden esperándose entre sí.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.catalogo.datos_iniciales import DURACION_ESTIMADA_MIN
from app.catalogo.tablas import Fila, Puesto, Servicio, ServicioPrecio
from app.clientes.tablas import Vehiculo
from app.turnos.errores import TurnoNoExiste
from app.turnos.estados import ESTADOS_ACTIVOS, ESTADOS_EN_ESPERA, EstadoTurno
from app.turnos.tablas import Turno

# Canal de PostgreSQL por el que se avisa que una fila cambió. El dashboard lo escucha
# para actualizarse solo (app.web.en_vivo).
CANAL_DE_CAMBIOS = "fila_cambio"


def bloquear_fila(sesion: Session, fila_id: int) -> Fila:
    """Bloquea la fila hasta el commit: los demás cambios a esa fila esperan su turno.

    También deja anotado el aviso de que la fila cambió. PostgreSQL solo lo entrega si
    el cambio se confirma (commit): si algo falla y se deshace, nadie se entera.
    """
    fila = sesion.execute(select(Fila).where(Fila.id == fila_id).with_for_update()).scalar_one()
    sesion.execute(
        text("SELECT pg_notify(:canal, :fila)"), {"canal": CANAL_DE_CAMBIOS, "fila": str(fila_id)}
    )
    return fila


def bloquear_turno(sesion: Session, turno_id: int) -> Turno:
    """Bloquea la fila del turno y después el turno.

    El turno se lee otra vez de la base (populate_existing): si otra operación lo cambió
    mientras esta esperaba el bloqueo, se trabaja con lo último.
    """
    fila_id = sesion.scalar(select(Turno.fila_id).where(Turno.id == turno_id))
    if fila_id is None:
        raise TurnoNoExiste(turno_id)
    bloquear_fila(sesion, fila_id)
    consulta = (
        select(Turno)
        .where(Turno.id == turno_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return sesion.execute(consulta).scalar_one()


def en_espera(sesion: Session, fila_id: int) -> list[Turno]:
    """La fila de espera, en orden: primero a quien le llegó la hora de su reserva (por la
    hora que reservó) y después los demás, el que entró primero (o volvió al final) primero."""
    consulta = (
        select(Turno)
        .where(Turno.fila_id == fila_id, Turno.estado.in_(ESTADOS_EN_ESPERA))
        .order_by(
            Turno.prioridad.desc(),
            case((Turno.prioridad, Turno.hora_reservada)),
            Turno.en_fila_desde,
            Turno.id,
        )
        .execution_options(populate_existing=True)
    )
    return list(sesion.scalars(consulta))


def reservas(
    sesion: Session,
    fila_id: int,
    *,
    desde: datetime,
    hasta: datetime,
    sin_turno_id: int | None = None,
) -> list[Turno]:
    """Los turnos con hora reservada entre `desde` y `hasta` que siguen vivos (los que no
    se cancelaron ni perdieron la reserva): ocupan su cupo en ese horario."""
    consulta = select(Turno).where(
        Turno.fila_id == fila_id,
        Turno.hora_reservada.is_not(None),
        Turno.hora_reservada >= desde,
        Turno.hora_reservada < hasta,
        Turno.estado.in_(ESTADOS_ACTIVOS),
    )
    if sin_turno_id is not None:
        consulta = consulta.where(Turno.id != sin_turno_id)
    return list(sesion.scalars(consulta.order_by(Turno.hora_reservada, Turno.id)))


def reservas_por_llegar(sesion: Session, fila_id: int, *, hasta: datetime) -> list[Turno]:
    """Las reservas a las que todavía no les llega la hora (Reservado, o que llegaron antes
    al local) y que empiezan antes de `hasta`: para ellas se guardan puestos."""
    consulta = select(Turno).where(
        Turno.fila_id == fila_id,
        Turno.hora_reservada.is_not(None),
        Turno.hora_reservada < hasta,
        Turno.prioridad.is_(False),
        Turno.estado.in_((EstadoTurno.RESERVADO, EstadoTurno.LLEGO)),
    )
    return list(sesion.scalars(consulta.order_by(Turno.hora_reservada, Turno.id)))


def en_lavado(sesion: Session, fila_id: int) -> list[Turno]:
    consulta = (
        select(Turno)
        .where(Turno.fila_id == fila_id, Turno.estado == EstadoTurno.EN_LAVADO)
        .execution_options(populate_existing=True)
    )
    return list(sesion.scalars(consulta))


def puestos_activos(sesion: Session, fila_id: int) -> list[Puesto]:
    consulta = (
        select(Puesto).where(Puesto.fila_id == fila_id, Puesto.activo).order_by(Puesto.nombre)
    )
    return list(sesion.scalars(consulta))


def puestos_libres(sesion: Session, fila_id: int) -> int:
    """Puestos activos sin un vehículo lavándose."""
    ocupados = select(Turno.puesto_id).where(
        Turno.estado == EstadoTurno.EN_LAVADO, Turno.puesto_id.is_not(None)
    )
    consulta = (
        select(func.count())
        .select_from(Puesto)
        .where(Puesto.fila_id == fila_id, Puesto.activo, Puesto.id.not_in(ocupados))
    )
    return sesion.scalar(consulta) or 0


def turno_en_el_puesto(sesion: Session, puesto_id: int) -> Turno | None:
    consulta = select(Turno).where(
        Turno.puesto_id == puesto_id, Turno.estado == EstadoTurno.EN_LAVADO
    )
    return sesion.scalars(consulta).one_or_none()


def activos_del_cliente(
    sesion: Session, cliente_id: int, fila_id: int | None = None
) -> list[Turno]:
    consulta = select(Turno).where(
        Turno.cliente_id == cliente_id, Turno.estado.in_(ESTADOS_ACTIVOS)
    )
    if fila_id is not None:
        consulta = consulta.where(Turno.fila_id == fila_id)
    return list(sesion.scalars(consulta.order_by(Turno.en_fila_desde, Turno.id)))


def precio_vigente(
    sesion: Session, servicio_id: int, tipo_vehiculo_id: int
) -> ServicioPrecio | None:
    consulta = select(ServicioPrecio).where(
        ServicioPrecio.servicio_id == servicio_id,
        ServicioPrecio.tipo_vehiculo_id == tipo_vehiculo_id,
        ServicioPrecio.activo,
    )
    return sesion.scalars(consulta).one_or_none()


def tipos_de_vehiculo(sesion: Session, vehiculo_ids: Iterable[int]) -> dict[int, int]:
    """{vehículo: tipo de vehículo}"""
    ids = set(vehiculo_ids)
    if not ids:
        return {}
    consulta = select(Vehiculo.id, Vehiculo.tipo_vehiculo_id).where(Vehiculo.id.in_(ids))
    return {vehiculo: tipo for vehiculo, tipo in sesion.execute(consulta)}


class Duraciones:
    """Cuánto dura cada servicio según el tipo de vehículo, para estimar la espera.

    Un turno con el servicio por definir cuenta como el servicio por defecto (al inicio,
    el Básico). Si algo no tiene duración configurada, se usa 1 hora.
    """

    def __init__(self, sesion: Session, fila_id: int, servicio_por_defecto: str) -> None:
        consulta = (
            select(
                ServicioPrecio.servicio_id,
                ServicioPrecio.tipo_vehiculo_id,
                ServicioPrecio.duracion_min,
            )
            .join(Servicio, Servicio.id == ServicioPrecio.servicio_id)
            .where(Servicio.fila_id == fila_id, ServicioPrecio.activo)
        )
        self._minutos = {
            (servicio, tipo): minutos for servicio, tipo, minutos in sesion.execute(consulta)
        }
        self._por_defecto = sesion.scalar(
            select(Servicio.id).where(
                Servicio.fila_id == fila_id, Servicio.nombre == servicio_por_defecto
            )
        )

    def de(self, servicio_id: int | None, tipo_vehiculo_id: int) -> timedelta:
        servicio = servicio_id if servicio_id is not None else self._por_defecto
        minutos = self._minutos.get((servicio, tipo_vehiculo_id), DURACION_ESTIMADA_MIN)
        return timedelta(minutes=minutos)

    def tipica(self) -> timedelta:
        """Lo que dura el servicio por defecto en esta fila (el más largo, si cambia según
        el vehículo): lo que dura un turno del que todavía no se sabe nada."""
        minutos = [m for (servicio, _), m in self._minutos.items() if servicio == self._por_defecto]
        return timedelta(minutes=max(minutos, default=DURACION_ESTIMADA_MIN))
