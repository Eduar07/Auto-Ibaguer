"""Las sesiones del bot que vencen.

Si el cliente deja de responder a mitad de una conversación (30 minutos; se cambia en
Configuración), la sesión vence y el bot vuelve al menú. El bot también lo revisará al
recibir cada mensaje (hito 5); el reloj, además, limpia las vencidas para que lo que la
persona escribió a medias no se quede guardado.
"""

from datetime import datetime

from sqlalchemy import delete, or_, update
from sqlalchemy.orm import Session

from app.bot.tablas import SesionBot

# El paso donde empieza (y vuelve) toda conversación.
PASO_INICIAL = "menu"


def vencer(sesion: Session, *, ahora: datetime) -> int:
    """Vence las sesiones sin respuesta. Devuelve cuántas vencieron.

    - La de un cliente vuelve al menú y olvida lo que iba respondiendo. La sesión se
      conserva, porque dice qué celular ya le escribe al bot.
    - La de alguien que nunca aceptó el aviso de datos se borra, celular incluido: sin su
      autorización no se guarda nada (Ley 1581). Si vuelve a escribir, empieza de cero.

    Si el bot está atendiendo a esa persona en este momento, tiene su sesión bloqueada:
    esto espera a que termine y, como el bot le renovó el vencimiento, ya no la toca.
    """
    borradas = sesion.execute(
        delete(SesionBot).where(SesionBot.expira_en <= ahora, SesionBot.cliente_id.is_(None))
    )
    al_menu = sesion.execute(
        update(SesionBot)
        .where(
            SesionBot.expira_en <= ahora,
            # Las que ya están en el menú y sin datos quedan quietas: así no se reescriben
            # en cada vuelta del reloj.
            or_(SesionBot.paso_actual != PASO_INICIAL, SesionBot.datos_temporales != {}),
        )
        .values(paso_actual=PASO_INICIAL, datos_temporales={})
        .execution_options(synchronize_session=False)
    )
    return borradas.rowcount + al_menu.rowcount
