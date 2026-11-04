"""Guardar los comentarios que llegan por WhatsApp y atenderlos desde el dashboard.

Lo que se muestra (la lista y el número del menú) está en app.comentarios.consultas.
"""

from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app import tiempo
from app.bot.mensajes import recortar
from app.clientes.tablas import Cliente
from app.comentarios.tablas import LARGO_MAXIMO, Comentario, TipoComentario
from app.errores import ReglaDelNegocio
from app.turnos.tablas import Turno


class ComentarioNoExiste(ReglaDelNegocio):
    pass


def registrar(
    sesion: Session,
    tipo: TipoComentario,
    texto: str,
    *,
    cliente: Cliente | None = None,
    ahora: datetime | None = None,
) -> Comentario:
    """Guarda lo que escribió el cliente.

    De un cliente que ya aceptó el aviso de datos se guarda su celular, para responderle.
    De alguien que no lo aceptó, nada que lo identifique, hasta que dé permiso
    (dar_permiso).
    """
    ultimo_turno = None
    if cliente is not None:
        ultimo_turno = sesion.scalar(
            select(Turno.id).where(Turno.cliente_id == cliente.id).order_by(Turno.id.desc())
        )
    comentario = Comentario(
        tipo=tipo,
        texto=_sin_pasarse(texto),
        cliente_id=cliente.id if cliente is not None else None,
        telefono=cliente.telefono if cliente is not None else None,
        turno_id=ultimo_turno,
        creado_en=ahora or tiempo.ahora(),
    )
    sesion.add(comentario)
    sesion.flush()
    return comentario


def agregar_detalle(sesion: Session, comentario_id: int, texto: str) -> bool:
    """Lo que el cliente contó después ("qué pasó y cuándo"), en el mismo comentario.

    Devuelve False si el comentario ya no existe (por ejemplo, lo borraron en el dashboard).
    """
    comentario = sesion.get(Comentario, comentario_id)
    if comentario is None:
        return False
    comentario.texto = _sin_pasarse(f"{comentario.texto}\n{texto}")
    sesion.flush()
    return True


def dar_permiso(
    sesion: Session, comentario_id: int, telefono: str, *, ahora: datetime | None = None
) -> None:
    """Quien no es cliente aceptó que el administrador le responda: se guarda su celular."""
    comentario = sesion.get(Comentario, comentario_id)
    if comentario is not None:
        comentario.telefono = telefono
        comentario.permiso_en = ahora or tiempo.ahora()
        sesion.flush()


def marcar_atendido(
    sesion: Session, comentario_id: int, administrador_id: int, *, ahora: datetime | None = None
) -> Comentario:
    comentario = _el_comentario(sesion, comentario_id)
    if comentario.atendido_en is None:
        comentario.atendido_en = ahora or tiempo.ahora()
        comentario.atendido_por = administrador_id
        sesion.flush()
    return comentario


def volver_a_pendiente(sesion: Session, comentario_id: int) -> Comentario:
    """Por si se marcó como atendido sin querer."""
    comentario = _el_comentario(sesion, comentario_id)
    comentario.atendido_en = None
    comentario.atendido_por = None
    sesion.flush()
    return comentario


def borrar(sesion: Session, comentario_id: int) -> None:
    """Por si quien lo escribió pide que se borre (Ley 1581), o si no era un comentario."""
    sesion.delete(_el_comentario(sesion, comentario_id))
    sesion.flush()


def borrar_los_del_cliente(sesion: Session, cliente: Cliente) -> None:
    """Ley 1581: cuando un cliente pide borrar sus datos, se borra también lo que escribió."""
    condiciones = [Comentario.cliente_id == cliente.id]
    if cliente.telefono:
        condiciones.append(Comentario.telefono == cliente.telefono)
    sesion.execute(delete(Comentario).where(or_(*condiciones)))


def _el_comentario(sesion: Session, comentario_id: int) -> Comentario:
    comentario = sesion.get(Comentario, comentario_id)
    if comentario is None:
        raise ComentarioNoExiste("Ese comentario ya no existe.")
    return comentario


def _sin_pasarse(texto: str) -> str:
    return recortar(texto.strip(), LARGO_MAXIMO)
