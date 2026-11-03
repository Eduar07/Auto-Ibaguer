"""¿Todavía sirve este aviso? Lo pregunta la bandeja de salida antes de enviar cada uno.

La regla está en `reglas.aviso_sigue_vigente`; aquí solo se busca el turno del aviso.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.integraciones.tablas import MensajeSaliente
from app.turnos import reglas
from app.turnos.estados import TipoAviso
from app.turnos.tablas import Turno


def sigue_vigente(sesion: Session, mensaje: MensajeSaliente, ahora: datetime) -> bool:
    """Los avisos de la fila se revisan contra su turno; cualquier otro mensaje sirve."""
    try:
        tipo = TipoAviso(mensaje.tipo)
    except ValueError:
        return True
    turno = sesion.get(Turno, mensaje.turno_id) if mensaje.turno_id is not None else None
    if turno is None:
        return True
    return reglas.aviso_sigue_vigente(
        tipo,
        turno.estado,
        gracia_hasta=turno.gracia_hasta,
        gracia_del_aviso=_fecha(mensaje.contenido.get("gracia_hasta")),
        ahora=ahora,
    )


def _fecha(texto: object) -> datetime | None:
    if not isinstance(texto, str):
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None
