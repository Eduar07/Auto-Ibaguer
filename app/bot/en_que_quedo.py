"""En qué quedó un cliente con el bot (pieza 6 del checklist "¿Tu bot vende o solo
contesta?"): lo que se ve en su ficha, en Clientes, sin buscar en el chat.

Junta lo que ya está guardado, sin guardar nada aparte:
- su último mensaje por WhatsApp (de las bandejas);
- si va en algo con el bot ahora mismo (pidiendo turno, eligiendo el servicio...);
- si dijo "lo pienso" y el bot le va a escribir (app.bot.seguimiento);
- sus comentarios por atender (una queja, un pedido especial...);
- si preguntó precios o la fila después de su última visita y no pidió turno.

Solo de clientes: de quien no aceptó el aviso de datos no se sabe quién es.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.bot.tablas import Seguimiento, SesionBot
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import TipoVehiculo
from app.clientes.tablas import Cliente, Consulta, TipoConsulta
from app.comentarios.tablas import Comentario, TipoComentario
from app.integraciones import historial
from app.turnos.tablas import Turno

# Lo que va haciendo con el bot, según el paso de la conversación (app.bot.conversacion.Paso).
VA_EN = {
    "aviso_datos": "Va pidiendo turno: el bot le mostró el aviso de datos.",
    "nombre": "Va pidiendo turno: el bot le preguntó el nombre.",
    "vehiculo": "Va pidiendo turno: el bot le preguntó el vehículo.",
    "placa": "Va pidiendo turno: el bot le preguntó la placa.",
    "servicio": "Va pidiendo turno: está eligiendo el servicio.",
    "confirmar": "Va pidiendo turno: le falta confirmarlo.",
    "segundo_turno": "Quiso pedir otro turno en una fila donde ya tiene uno.",
    "precios": "Está mirando los precios.",
    "cancelar": "El bot le preguntó si cancela su turno.",
    "seguimiento": "Dijo «lo pienso»: el bot le preguntó si le escribe después.",
}
NOMBRE_DEL_COMENTARIO = {
    TipoComentario.QUEJA: "Queja",
    TipoComentario.FELICITACION: "Felicitación",
    TipoComentario.QUIERE_HABLAR: "Quiere hablar contigo",
    TipoComentario.PREGUNTA: "Pregunta",
    TipoComentario.PEDIDO_ESPECIAL: "Pedido especial",
}
RECIENTE = timedelta(days=30)  # una consulta más vieja ya no dice en qué quedó


@dataclass(frozen=True)
class EnQueQuedo:
    ultimo_mensaje: str | None = None
    ultimo_en: datetime | None = None
    cosas: list[str] = field(default_factory=list)


def en_que_quedo(sesion: Session, cliente_id: int, *, ahora: datetime) -> EnQueQuedo | None:
    """None si nunca ha escrito por WhatsApp y no hay nada pendiente."""
    cliente = sesion.get(Cliente, cliente_id)
    if cliente is None or not cliente.telefono:
        return None
    hoy = tiempo.hora_local(ahora).date()
    parametros = leer_parametros(sesion)
    lineas = historial.conversacion(sesion, cliente.telefono, parametros, cuantos=20)
    suyas = [linea for linea in lineas if linea.quien == historial.Quien.CLIENTE]
    cosas: list[str] = []

    conversacion = sesion.scalars(
        select(SesionBot).where(SesionBot.telefono == cliente.telefono, SesionBot.expira_en > ahora)
    ).first()
    if conversacion is not None and conversacion.paso_actual in VA_EN:
        cosas.append(VA_EN[conversacion.paso_actual])

    pendiente = sesion.get(Seguimiento, cliente.telefono)
    if pendiente is not None:
        cuando = formato.cuando_sera(tiempo.hora_local(pendiente.enviar_en), hoy)
        cosas.append(f"Dijo «lo pienso»: el bot le escribe {cuando}.")

    por_atender = sesion.scalars(
        select(Comentario)
        .where(Comentario.cliente_id == cliente.id, Comentario.atendido_en.is_(None))
        .order_by(Comentario.creado_en.desc())
        .limit(2)
    )
    for comentario in por_atender:
        que = comentario.resumen or formato_corto(comentario.texto)
        cosas.append(
            f"{NOMBRE_DEL_COMENTARIO[comentario.tipo]} por atender (en Comentarios): {que}"
        )

    ultima_visita = sesion.scalar(
        select(Turno.creado_en).where(Turno.cliente_id == cliente.id).order_by(Turno.id.desc())
    )
    consulta = sesion.execute(
        select(Consulta.tipo, Consulta.fecha_hora, TipoVehiculo.nombre)
        .outerjoin(TipoVehiculo, TipoVehiculo.id == Consulta.tipo_vehiculo_id)
        .where(Consulta.cliente_id == cliente.id, Consulta.fecha_hora > ahora - RECIENTE)
        .order_by(Consulta.fecha_hora.desc())
        .limit(1)
    ).first()
    if consulta is not None and (ultima_visita is None or consulta.fecha_hora > ultima_visita):
        que = (
            f"los precios de {consulta.nombre.lower()}"
            if consulta.tipo == TipoConsulta.PRECIOS and consulta.nombre
            else "los precios"
            if consulta.tipo == TipoConsulta.PRECIOS
            else "cómo estaba la fila"
        )
        cuando = formato.cuando_paso(tiempo.hora_local(consulta.fecha_hora), hoy)
        cosas.append(f"Preguntó {que} {cuando} y no ha pedido turno.")

    if not suyas and not cosas:
        return None
    ultima = suyas[-1] if suyas else None
    return EnQueQuedo(
        ultimo_mensaje=ultima.dice if ultima else None,
        ultimo_en=tiempo.hora_local(ultima.cuando) if ultima else None,
        cosas=cosas,
    )


def formato_corto(texto: str, largo: int = 120) -> str:
    limpio = " ".join(texto.split())
    return limpio if len(limpio) <= largo else limpio[: largo - 1].rstrip() + "…"
