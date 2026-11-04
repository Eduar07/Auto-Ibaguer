"""El bot en pausa con un cliente mientras le responde una persona (hito 7b, parte 3).

Cuando el administrador le escribe a un cliente desde Comentarios, el bot deja de
contestarle lo que escriba durante un rato (PAUSA, que se alarga con cada mensaje del
administrador), para no meterse en la conversación. Lo que el cliente escriba le llega
al administrador. La fila no espera, así que siguen igual:

- los botones (un botón es una orden: "Ya estoy aquí") y lo que responde cuando va en
  algo con el bot (tocó "Pedir turno" y el bot le pregunta la placa);
- lo que pide sobre su turno en un mensaje corto ("ya llegué", "mi turno") y el código QR;
- los avisos de la fila y los mensajes graves (las líneas de ayuda).

Está en app.bot.conversacion; aquí solo se guarda con quién y hasta cuándo.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.bot.tablas import PausaDelBot

PAUSA = timedelta(hours=1)  # desde el último mensaje del administrador


def pausar(
    sesion: Session,
    telefono: str,
    *,
    administrador_id: int,
    comentario_id: int | None,
    ahora: datetime,
) -> None:
    """Empieza la pausa con ese celular, o la alarga si ya estaba."""
    valores = {
        "hasta": ahora + PAUSA,
        "administrador_id": administrador_id,
        "comentario_id": comentario_id,
    }
    sesion.execute(
        insert(PausaDelBot)
        .values(telefono=telefono, creada_en=ahora, **valores)
        .on_conflict_do_update(index_elements=[PausaDelBot.telefono], set_=valores)
    )
    sesion.flush()


def pausa_de(sesion: Session, telefono: str, ahora: datetime) -> PausaDelBot | None:
    """La pausa vigente con ese celular, o None si el bot le contesta normal."""
    consulta = select(PausaDelBot).where(
        PausaDelBot.telefono == telefono, PausaDelBot.hasta > ahora
    )
    return sesion.scalars(consulta.execution_options(populate_existing=True)).first()


def devolver_al_bot(sesion: Session, telefono: str) -> None:
    """El administrador terminó de hablar: el bot vuelve a contestarle."""
    sesion.execute(delete(PausaDelBot).where(PausaDelBot.telefono == telefono))
    sesion.flush()


def vencer_pausas(sesion: Session, *, ahora: datetime) -> int:
    """Borra las pausas que ya pasaron (el reloj): no se guarda un celular sin necesidad."""
    return sesion.execute(delete(PausaDelBot).where(PausaDelBot.hasta <= ahora)).rowcount
