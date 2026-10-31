"""La hora del sistema.

En la base de datos todo se guarda con zona horaria. Para el negocio cuenta la hora de
Bogotá: el día del turno, el horario de atención y el cierre de caja.

Los casos de uso reciben `ahora` como parámetro (por defecto, la hora real). Así las
pruebas pueden decir "son las 7:29 p.m. del lunes" sin esperar a que lo sean.
"""

from datetime import UTC, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.config import obtener_entorno


@lru_cache
def zona_horaria() -> ZoneInfo:
    return ZoneInfo(obtener_entorno().zona_horaria)


def ahora() -> datetime:
    return datetime.now(UTC)


def hora_local(momento: datetime) -> datetime:
    """El mismo instante, expresado en la hora de Bogotá."""
    return momento.astimezone(zona_horaria())
