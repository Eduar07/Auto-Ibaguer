"""Lo que se muestra de los comentarios sin cambiar nada: la lista de la pantalla
Comentarios y cuántos faltan por atender (el número que sale en el menú)."""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.catalogo.tablas import Fila, Servicio
from app.clientes.tablas import Cliente
from app.comentarios.servicio import ComentarioNoExiste
from app.comentarios.tablas import Comentario, TipoComentario
from app.turnos.reglas import numero_de_turno
from app.turnos.tablas import Turno
from app.web.tablas import Administrador

CUANTOS = 50  # los más recientes; los demás siguen guardados


@dataclass(frozen=True)
class ComentarioEnLista:
    id: int
    tipo: TipoComentario
    texto: str
    creado_en: datetime
    cliente_id: int | None
    cliente: str | None  # el nombre, si es cliente
    telefono: str | None  # solo si es cliente o dio permiso para que le respondan
    turno: str | None  # el último turno del cliente cuando escribió: "C-07"
    turno_fecha: date | None
    servicio: str | None
    atendido_en: datetime | None
    atendido_por: str | None  # el usuario del administrador

    @property
    def atendido(self) -> bool:
        return self.atendido_en is not None


def por_atender(sesion: Session) -> int:
    contar = select(func.count()).select_from(Comentario).where(Comentario.atendido_en.is_(None))
    return sesion.scalar(contar) or 0


def lista(
    sesion: Session, *, solo_por_atender: bool, cuantos: int = CUANTOS
) -> list[ComentarioEnLista]:
    """Los más recientes primero."""
    consulta = _consulta().order_by(Comentario.creado_en.desc(), Comentario.id.desc())
    if solo_por_atender:
        consulta = consulta.where(Comentario.atendido_en.is_(None))
    return [_en_lista(*fila) for fila in sesion.execute(consulta.limit(cuantos))]


def uno(sesion: Session, comentario_id: int) -> ComentarioEnLista:
    fila = sesion.execute(_consulta().where(Comentario.id == comentario_id)).first()
    if fila is None:
        raise ComentarioNoExiste("Ese comentario ya no existe.")
    return _en_lista(*fila)


def _consulta() -> Select:
    return (
        select(
            Comentario,
            Cliente.nombre,
            Fila.prefijo,
            Turno.consecutivo,
            Turno.fecha,
            Servicio.nombre,
            Administrador.usuario,
        )
        .outerjoin(Cliente, Cliente.id == Comentario.cliente_id)
        .outerjoin(Turno, Turno.id == Comentario.turno_id)
        .outerjoin(Fila, Fila.id == Turno.fila_id)
        .outerjoin(Servicio, Servicio.id == Turno.servicio_id)
        .outerjoin(Administrador, Administrador.id == Comentario.atendido_por)
    )


def _en_lista(
    comentario: Comentario,
    cliente: str | None,
    prefijo: str | None,
    consecutivo: int | None,
    turno_fecha: date | None,
    servicio: str | None,
    atendido_por: str | None,
) -> ComentarioEnLista:
    return ComentarioEnLista(
        id=comentario.id,
        tipo=comentario.tipo,
        texto=comentario.texto,
        creado_en=comentario.creado_en,
        cliente_id=comentario.cliente_id,
        cliente=cliente,
        telefono=comentario.telefono,
        turno=numero_de_turno(prefijo, consecutivo) if prefijo and consecutivo else None,
        turno_fecha=turno_fecha,
        servicio=servicio,
        atendido_en=comentario.atendido_en,
        atendido_por=atendido_por,
    )
