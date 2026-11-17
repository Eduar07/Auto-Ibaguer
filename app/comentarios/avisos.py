"""El aviso al dueño: cuando un cliente espera que una persona le responda (una queja, un
pedido de hablar con alguien o un pedido especial), le llega un correo con el resumen.

En Comentarios ya se ve, pero el dueño no siempre tiene el dashboard abierto: el correo le
llega al celular. Como un mesero que, además de anotar el pedido en la comanda, va y le
avisa al jefe de cocina cuando es algo que solo él decide.

- Lo manda el reloj (app.reloj.tareas), a los correos del reporte del lavadero
  (Configuración > Recibo y reporte), si el correo del sistema está configurado.
- Espera un par de minutos desde que llega: así alcanza a ir lo que el cliente contó
  después ("qué pasó y cuándo").
- Lleva el primer nombre del cliente (si lo es) y qué pide, nunca su celular: para
  responderle, Comentarios.
- Cada comentario se avisa una sola vez, aunque el correo falle (queda en el registro y
  en Comentarios): un servidor de correo caído no llena el registro con cada vuelta.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.bot.mensajes import recortar
from app.catalogo.parametros import leer_parametros
from app.clientes.tablas import Cliente
from app.comentarios.tablas import TIPOS_QUE_SE_AVISAN, Comentario, TipoComentario
from app.config import obtener_entorno
from app.integraciones import correo
from app.turnos.recibo import leer_ajustes

logger = logging.getLogger(__name__)

ESPERA = timedelta(minutes=2)  # para que alcance a ir lo que contó después
VIGENCIA = timedelta(hours=12)  # lo más viejo ya no es aviso: está en Comentarios

QUE_ES = {
    TipoComentario.QUEJA: "Queja",
    TipoComentario.QUIERE_HABLAR: "Quiere hablar contigo",
    TipoComentario.PEDIDO_ESPECIAL: "Pedido especial",
}


@dataclass(frozen=True)
class Aviso:
    asunto: str
    texto: str
    destinos: tuple[str, ...]
    negocio: str


def preparar(sesion: Session, *, ahora: datetime) -> Aviso | None:
    """El correo con lo que falta avisar, y los comentarios quedan marcados como avisados.
    None si no hay nada, o si no hay a quién ni cómo mandarlo (entonces no se marca nada:
    se avisa cuando se configure, si todavía es reciente)."""
    if not obtener_entorno().correo_configurado:
        return None
    destinos = tuple(leer_ajustes(sesion).destinos_del_reporte)
    if not destinos:
        return None
    filas = sesion.execute(
        select(Comentario, Cliente.nombre)
        .outerjoin(Cliente, Cliente.id == Comentario.cliente_id)
        .where(
            Comentario.tipo.in_(TIPOS_QUE_SE_AVISAN),
            Comentario.avisado_en.is_(None),
            Comentario.atendido_en.is_(None),
            Comentario.creado_en <= ahora - ESPERA,
            Comentario.creado_en > ahora - VIGENCIA,
        )
        .order_by(Comentario.creado_en, Comentario.id)
        .with_for_update(of=Comentario, skip_locked=True)
    ).all()
    if not filas:
        return None
    lineas = []
    for comentario, nombre in filas:
        comentario.avisado_en = ahora
        quien = nombre.split()[0] if nombre else "Alguien que no es cliente"
        cuando = formato.hora(tiempo.hora_local(comentario.creado_en))
        que = comentario.resumen or recortar(" ".join(comentario.texto.split()), 200)
        lineas.append(f"- {QUE_ES[comentario.tipo]} · {quien} · {cuando}\n  {que}")
    sesion.flush()
    negocio = leer_parametros(sesion).nombre_negocio
    cuantos = len(filas)
    asunto = (
        f"{negocio}: un cliente espera que le respondas"
        if cuantos == 1
        else f"{negocio}: {cuantos} clientes esperan que les respondas"
    )
    texto = (
        "Por WhatsApp:\n\n"
        + "\n\n".join(lineas)
        + "\n\nEl bot no les prometió nada: les dijo que les escribes. Respóndeles desde "
        "Comentarios, en el dashboard (Responder por WhatsApp), o llámalos desde ahí."
    )
    return Aviso(asunto, texto, destinos, negocio)


def enviar(aviso: Aviso) -> bool:
    try:
        correo.enviar(aviso.asunto, aviso.texto, aviso.destinos, nombre_remitente=aviso.negocio)
    except correo.CorreoNoEnviado as error:
        logger.warning("No se pudo avisar al dueño por correo: %s Quedó en Comentarios.", error)
        return False
    return True
