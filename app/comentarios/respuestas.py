"""Responderle a un cliente por WhatsApp desde Comentarios (hito 7b, parte 3).

Lo que escribe el administrador sale por la bandeja de salida, como las respuestas del
bot (con sus reintentos y en orden), y solo si el cliente escribió en las últimas 24
horas: WhatsApp no deja escribirle libremente a nadie más. Mientras tanto, el bot queda en
pausa con ese cliente (app.bot.pausa), para no meterse en la conversación.

La conversación que se muestra en el diálogo sale de app.integraciones.historial.
"""

import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.bot import pausa
from app.comentarios import servicio as comentarios
from app.comentarios.servicio import ComentarioNoExiste
from app.comentarios.tablas import Comentario
from app.errores import ReglaDelNegocio
from app.integraciones import bandeja_salida
from app.integraciones.tablas import MensajeEntrante, MensajeSaliente
from app.integraciones.whatsapp import VENTANA_PARA_ESCRIBIR

LARGO_MAXIMO = 1000  # caracteres: WhatsApp deja más, pero un mensaje así ya es largo


class NoSePuedeResponder(ReglaDelNegocio):
    pass


def hasta_cuando_se_puede(sesion: Session, telefono: str, ahora: datetime) -> datetime | None:
    """Hasta qué hora WhatsApp deja escribirle (24 horas después de su último mensaje), o
    None si ya pasaron (o nunca escribió)."""
    ultimo = sesion.scalar(
        select(func.max(MensajeEntrante.recibido_en)).where(MensajeEntrante.telefono == telefono)
    )
    if ultimo is None:
        return None
    hasta = ultimo + VENTANA_PARA_ESCRIBIR
    return hasta if hasta > ahora else None


def responder(
    sesion: Session,
    comentario_id: int,
    texto: str,
    *,
    administrador_id: int,
    usuario: str,
    ahora: datetime | None = None,
) -> MensajeSaliente:
    """Deja el mensaje en la bandeja de salida, pone al bot en pausa con ese cliente y marca
    el comentario como atendido. Si no se puede, lanza NoSePuedeResponder con el motivo."""
    ahora = ahora or tiempo.ahora()
    comentario = sesion.get(Comentario, comentario_id)
    if comentario is None:
        raise ComentarioNoExiste("Ese comentario ya no existe.")
    if not comentario.telefono:
        raise NoSePuedeResponder("Este comentario no tiene celular: no hay a quién responderle.")
    texto = limpio(texto)
    if not texto:
        raise NoSePuedeResponder("Escribe lo que le quieres decir.")
    if len(texto) > LARGO_MAXIMO:
        raise NoSePuedeResponder(
            f"El mensaje puede tener hasta {LARGO_MAXIMO} caracteres; este tiene {len(texto)}."
        )
    if hasta_cuando_se_puede(sesion, comentario.telefono, ahora) is None:
        raise NoSePuedeResponder(
            "WhatsApp solo deja escribirle hasta 24 horas después de su último mensaje, y ya "
            "pasaron. Llámalo, o espera a que vuelva a escribir."
        )
    mensaje = bandeja_salida.encolar_del_administrador(
        sesion,
        telefono=comentario.telefono,
        texto=texto,
        por=usuario,
        ahora=ahora,
        cliente_id=comentario.cliente_id,
    )
    pausa.pausar(
        sesion,
        comentario.telefono,
        administrador_id=administrador_id,
        comentario_id=comentario.id,
        ahora=ahora,
    )
    comentarios.marcar_atendido(sesion, comentario.id, administrador_id, ahora=ahora)
    sesion.flush()
    return mensaje


def saludo_sugerido(cliente: str | None, usuario: str, negocio: str) -> str:
    """Con qué empieza el mensaje, para que sepa que ahora le escribe una persona y quién:
    "Hola Ana, te habla Eduar, de Auto Ibaguer. " El administrador lo puede cambiar."""
    nombre = f" {cliente.split()[0]}" if cliente and cliente.split() else ""
    quien = usuario[:1].upper() + usuario[1:]
    return f"Hola{nombre}, te habla {quien}, de {negocio}. "


def limpio(texto: str) -> str:
    """Sin espacios de más al principio y al final de cada línea, ni varias líneas en blanco
    seguidas."""
    lineas = [linea.strip() for linea in texto.replace("\r\n", "\n").strip().split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas))
