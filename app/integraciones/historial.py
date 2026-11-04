"""Lo que se han dicho con un celular: lo que llegó y lo que salió por WhatsApp, juntos y en
orden, como en el chat.

Lo usan dos:
- El diálogo de responder de Comentarios, para mostrarle la conversación al administrador.
- El bot, para que Gemini entienda el mensaje nuevo con lo que se venía hablando (su
  memoria): solo lo que el cliente alcanzó a ver, sin el mensaje que se está atendiendo.

No guarda nada aparte: lee las dos bandejas (app.integraciones.tablas), que ya tienen todo.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.avisos import mensaje_del_aviso
from app.bot.mensajes import recortar
from app.catalogo.parametros import Parametros
from app.integraciones.bandeja_salida import TIPO_DEL_ADMINISTRADOR, TIPO_RESPUESTA
from app.integraciones.tablas import EstadoMensaje, MensajeEntrante, MensajeSaliente
from app.turnos.estados import TipoAviso

LARGO_MAXIMO = 1000  # caracteres de cada mensaje: más ya no se lee de un vistazo
_TIPOS_DE_AVISO = {tipo.value for tipo in TipoAviso}
# Lo que el cliente sí recibió (o está por recibir). Lo que no salió no lo vio.
_LE_LLEGO = (EstadoMensaje.PENDIENTE, EstadoMensaje.ENVIADO)


class Quien(StrEnum):
    CLIENTE = "cliente"
    BOT = "bot"
    ADMINISTRADOR = "administrador"


@dataclass(frozen=True)
class Linea:
    """Un mensaje de la conversación."""

    cuando: datetime
    quien: Quien
    dice: str
    por: str | None = None  # el usuario del administrador que lo escribió
    estado: EstadoMensaje | None = None  # de lo que salió: por enviar, enviado, no salió...
    error: str | None = None
    opciones: tuple[str, ...] = ()  # los botones (o la lista) que traía un mensaje del bot


def conversacion(
    sesion: Session,
    telefono: str,
    parametros: Parametros,
    *,
    cuantos: int = 30,
    desde: datetime | None = None,
    solo_lo_que_vio: bool = False,
) -> list[Linea]:
    """Lo último que se dijeron con ese celular, del más viejo al más nuevo.

    `desde`: solo lo que pasó desde esa hora. `solo_lo_que_vio` (la memoria del bot): sin
    lo que todavía no se ha atendido (el mensaje que el bot está contestando) ni lo que no
    le llegó al cliente (no salió o ya no servía).
    """
    entrantes = select(MensajeEntrante).where(MensajeEntrante.telefono == telefono)
    salientes = select(MensajeSaliente).where(MensajeSaliente.telefono == telefono)
    if desde is not None:
        entrantes = entrantes.where(MensajeEntrante.recibido_en >= desde)
        salientes = salientes.where(MensajeSaliente.creado_en >= desde)
    if solo_lo_que_vio:
        entrantes = entrantes.where(MensajeEntrante.procesado_en.is_not(None))
        salientes = salientes.where(MensajeSaliente.estado.in_(_LE_LLEGO))
    llegaron = sesion.scalars(entrantes.order_by(MensajeEntrante.id.desc()).limit(cuantos))
    salieron = sesion.scalars(salientes.order_by(MensajeSaliente.id.desc()).limit(cuantos))
    # Cada lista del más viejo al más nuevo; al ordenar, lo que pasó a la misma hora queda
    # en ese orden, y primero lo que escribió el cliente que lo que se le contestó.
    lineas = [_lo_que_llego(m) for m in reversed(list(llegaron))]
    lineas += [_lo_que_salio(m, parametros) for m in reversed(list(salieron))]
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
    quien, por, opciones = Quien.BOT, None, ()
    if mensaje.tipo == TIPO_DEL_ADMINISTRADOR:
        quien, por, dice = Quien.ADMINISTRADOR, contenido.get("por"), contenido.get("texto", "")
    elif mensaje.tipo == TIPO_RESPUESTA:
        dice, opciones = contenido.get("texto", ""), _opciones(contenido)
    elif mensaje.tipo in _TIPOS_DE_AVISO:
        try:
            aviso = mensaje_del_aviso(mensaje.tipo, contenido, parametros)
            dice, opciones = aviso.texto, tuple(opcion.titulo for opcion in aviso.opciones)
        except (KeyError, TypeError, ValueError):
            dice = "Un aviso de la fila."
    else:
        dice = mensaje.tipo
    return Linea(
        mensaje.creado_en,
        quien,
        recortar(str(dice), LARGO_MAXIMO),
        por=por,
        estado=mensaje.estado,
        error=mensaje.ultimo_error,
        opciones=opciones,
    )


def _opciones(contenido: dict) -> tuple[str, ...]:
    """Los títulos de los botones o de la lista de una respuesta del bot (como se guardó:
    app.bot.mensajes.Mensaje.a_datos)."""
    try:
        if contenido.get("botones"):
            return tuple(str(boton[1]) for boton in contenido["botones"])
        if contenido.get("lista"):
            return tuple(str(opcion[1]) for opcion in contenido["lista"]["opciones"])
    except (KeyError, IndexError, TypeError):
        pass
    return ()
