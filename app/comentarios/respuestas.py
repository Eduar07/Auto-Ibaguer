"""Responderle a un cliente por WhatsApp desde Comentarios (hito 7b, parte 3).

Lo que escribe el administrador sale por la bandeja de salida, como las respuestas del
bot (con sus reintentos y en orden), y solo si el cliente escribió en las últimas 24
horas: WhatsApp no deja escribirle libremente a nadie más. Mientras tanto, el bot queda en
pausa con ese cliente (app.bot.pausa), para no meterse en la conversación.

La conversación que se muestra (charla_con) junta lo que el cliente escribió y lo que le
salió: las respuestas del bot, los avisos de la fila y lo que escribió el administrador.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.bot import pausa
from app.bot.avisos import mensaje_del_aviso
from app.bot.mensajes import recortar
from app.catalogo.parametros import Parametros
from app.comentarios import servicio as comentarios
from app.comentarios.servicio import ComentarioNoExiste
from app.comentarios.tablas import Comentario
from app.errores import ReglaDelNegocio
from app.integraciones import bandeja_salida
from app.integraciones.bandeja_salida import TIPO_DEL_ADMINISTRADOR, TIPO_RESPUESTA
from app.integraciones.tablas import EstadoMensaje, MensajeEntrante, MensajeSaliente
from app.integraciones.whatsapp import VENTANA_PARA_ESCRIBIR
from app.turnos.estados import TipoAviso

LARGO_MAXIMO = 1000  # caracteres: WhatsApp deja más, pero un mensaje así ya es largo
CUANTOS = 30  # lo último de la conversación que se muestra
_TIPOS_DE_AVISO = {tipo.value for tipo in TipoAviso}


class NoSePuedeResponder(ReglaDelNegocio):
    pass


class Quien(StrEnum):
    CLIENTE = "cliente"
    BOT = "bot"
    ADMINISTRADOR = "administrador"


@dataclass(frozen=True)
class Linea:
    """Un mensaje de la conversación, como se muestra en Comentarios."""

    cuando: datetime
    quien: Quien
    dice: str
    por: str | None = None  # el usuario del administrador que lo escribió
    estado: EstadoMensaje | None = None  # de lo que salió: por enviar, enviado, no salió...
    error: str | None = None


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


# --- La conversación --------------------------------------------------------------------------


def charla_con(
    sesion: Session, telefono: str, parametros: Parametros, *, cuantos: int = CUANTOS
) -> list[Linea]:
    """Lo último que se dijeron con ese celular, del más viejo al más nuevo."""
    entrantes = sesion.scalars(
        select(MensajeEntrante)
        .where(MensajeEntrante.telefono == telefono)
        .order_by(MensajeEntrante.id.desc())
        .limit(cuantos)
    )
    salientes = sesion.scalars(
        select(MensajeSaliente)
        .where(MensajeSaliente.telefono == telefono)
        .order_by(MensajeSaliente.id.desc())
        .limit(cuantos)
    )
    # Cada lista del más viejo al más nuevo; al ordenar, lo que pasó a la misma hora queda
    # en ese orden, y primero lo que escribió el cliente que lo que se le contestó.
    lineas = [_lo_que_llego(m) for m in reversed(list(entrantes))]
    lineas += [_lo_que_salio(m, parametros) for m in reversed(list(salientes))]
    lineas.sort(key=lambda linea: (linea.cuando, linea.quien != Quien.CLIENTE))
    return lineas[-cuantos:]


def _lo_que_llego(mensaje: MensajeEntrante) -> Linea:
    contenido = mensaje.contenido or {}
    if contenido.get("titulo") or contenido.get("opcion"):
        dice = f"Tocó «{contenido.get('titulo') or contenido.get('opcion')}»"
    elif contenido.get("texto"):
        dice = recortar(str(contenido["texto"]), LARGO_MAXIMO)
    elif mensaje.tipo == "reaction":
        dice = "Reaccionó a un mensaje."
    else:
        dice = "Mandó algo que no es texto (una foto, un audio...): aquí no se puede ver."
    return Linea(mensaje.recibido_en, Quien.CLIENTE, dice)


def _lo_que_salio(mensaje: MensajeSaliente, parametros: Parametros) -> Linea:
    contenido = mensaje.contenido or {}
    if mensaje.tipo == TIPO_DEL_ADMINISTRADOR:
        quien, por, dice = Quien.ADMINISTRADOR, contenido.get("por"), contenido.get("texto", "")
    elif mensaje.tipo == TIPO_RESPUESTA:
        quien, por, dice = Quien.BOT, None, contenido.get("texto", "")
    elif mensaje.tipo in _TIPOS_DE_AVISO:
        quien, por = Quien.BOT, None
        try:
            dice = mensaje_del_aviso(mensaje.tipo, contenido, parametros).texto
        except (KeyError, TypeError, ValueError):
            dice = "Un aviso de la fila."
    else:
        quien, por, dice = Quien.BOT, None, mensaje.tipo
    return Linea(
        mensaje.creado_en,
        quien,
        recortar(str(dice), LARGO_MAXIMO),
        por=por,
        estado=mensaje.estado,
        error=mensaje.ultimo_error,
    )
