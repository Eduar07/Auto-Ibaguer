"""Dejar un mensaje en la bandeja de salida.

Quien produce un aviso (por ejemplo, la fila al llamar a alguien) no habla con WhatsApp:
deja el mensaje aquí, en la misma operación que el cambio que lo produjo. El que envía
(hito 5) lo recoge, revisa si WhatsApp deja escribirle a esa persona (ventana de 24
horas) y lo reintenta si falla.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.integraciones.tablas import MensajeSaliente


def encolar(
    sesion: Session,
    *,
    telefono: str,
    tipo: str,
    contenido: dict[str, Any],
    ahora: datetime,
    cliente_id: int | None = None,
    turno_id: int | None = None,
) -> MensajeSaliente:
    mensaje = MensajeSaliente(
        telefono=telefono,
        cliente_id=cliente_id,
        turno_id=turno_id,
        tipo=tipo,
        contenido=contenido,
        proximo_intento_en=ahora,
    )
    sesion.add(mensaje)
    return mensaje
