"""La bandeja de salida: dejar un mensaje y enviarlo, con reintentos.

Nadie le habla a WhatsApp directamente: quien produce un mensaje lo deja aquí, en la
misma operación que el cambio que lo produjo. La fila deja sus avisos (`encolar`), el
bot sus respuestas (`encolar_respuesta`) y el administrador lo que le escribe a un
cliente desde Comentarios (`encolar_del_administrador`). Si el cambio quedó guardado, el
mensaje sale; si algo se deshace, el mensaje tampoco existe.

Después, `enviar_pendientes` toma los que ya toca enviar y se los pasa al que envía
(app.integraciones.whatsapp). Si WhatsApp no lo acepta, se vuelve a intentar más tarde,
cada vez esperando un poco más. Lo hacen el webhook apenas atiende un mensaje y el reloj
en cada vuelta.
"""

import logging
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session, aliased

from app.bot.mensajes import Mensaje
from app.integraciones.tablas import EstadoMensaje, MensajeSaliente

logger = logging.getLogger(__name__)

# Después de cada intento fallido, cuánto se espera para el siguiente. Cuando se acaba la
# lista, el mensaje queda fallido: son 6 intentos en unos 18 minutos.
ESPERAS = (
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=10),
)


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
        creado_en=ahora,
        proximo_intento_en=ahora,
    )
    sesion.add(mensaje)
    return mensaje


# Así se llaman en la bandeja las respuestas del bot. Los avisos de la fila usan su tipo
# (llamado, vehiculo_listo...: app.turnos.estados.TipoAviso).
TIPO_RESPUESTA = "respuesta"


def encolar_respuesta(
    sesion: Session,
    *,
    telefono: str,
    mensaje: Mensaje,
    ahora: datetime,
    cliente_id: int | None = None,
) -> MensajeSaliente:
    """Una respuesta del bot: el mensaje entero (texto y botones) queda guardado como JSON."""
    return encolar(
        sesion,
        telefono=telefono,
        tipo=TIPO_RESPUESTA,
        contenido=mensaje.a_datos(),
        ahora=ahora,
        cliente_id=cliente_id,
    )


# Lo que el administrador le escribe a un cliente desde Comentarios (hito 7b): sale tal
# cual, como texto. `por` es el usuario que lo escribió, para verlo en la conversación.
TIPO_DEL_ADMINISTRADOR = "del_administrador"


def encolar_del_administrador(
    sesion: Session,
    *,
    telefono: str,
    texto: str,
    por: str,
    ahora: datetime,
    cliente_id: int | None = None,
) -> MensajeSaliente:
    return encolar(
        sesion,
        telefono=telefono,
        tipo=TIPO_DEL_ADMINISTRADOR,
        contenido={"texto": texto, "por": por},
        ahora=ahora,
        cliente_id=cliente_id,
    )


# --- Enviar ------------------------------------------------------------------------------


class EnvioFallido(Exception):
    """WhatsApp no aceptó el mensaje.

    `reintentar` dice si vale la pena volver a intentar: sí cuando se cayó la conexión o
    WhatsApp falló un momento; no cuando el problema no se arregla solo (por ejemplo, el
    número no tiene WhatsApp).
    """

    def __init__(self, motivo: str, *, reintentar: bool) -> None:
        super().__init__(motivo)
        self.reintentar = reintentar


class Enviador(Protocol):
    """Quien entrega los mensajes; en el hito 5, WhatsApp.

    Es como una interfaz de Java: la bandeja solo sabe que tiene el método `enviar`, y
    en las pruebas se usa uno de mentira.
    """

    def enviar(self, sesion: Session, mensaje: MensajeSaliente) -> str:
        """Entrega el mensaje y devuelve el id que le dio WhatsApp.

        Si no pudo, lanza EnvioFallido.
        """
        ...


# Revisa si el aviso todavía sirve antes de enviarlo (app.turnos.avisos.sigue_vigente).
SigueVigente = Callable[[Session, MensajeSaliente, datetime], bool]


class Resultado(StrEnum):
    ENVIADO = "enviado"
    REINTENTO = "reintento"  # falló, pero se vuelve a intentar más tarde
    FALLIDO = "fallido"
    DESCARTADO = "descartado"


def cuando_reintentar(intentos: int, ahora: datetime) -> datetime | None:
    """Cuándo va el próximo intento después de `intentos` intentos fallidos.

    None: ya no se intenta más.
    """
    if intentos > len(ESPERAS):
        return None
    return ahora + ESPERAS[intentos - 1]


def enviar_siguiente(
    sesion: Session, enviador: Enviador, *, ahora: datetime, sigue_vigente: SigueVigente
) -> Resultado | None:
    """Envía el próximo mensaje que ya toca y anota cómo le fue. None si no hay ninguno.

    El mensaje queda bloqueado hasta el commit (FOR UPDATE SKIP LOCKED): si otro reloj
    corre al mismo tiempo, se lo salta y toma otro, así nadie recibe el mismo aviso dos
    veces. Antes de enviarlo se revisa que todavía sirva.

    A cada celular sus mensajes le llegan en orden: uno no sale mientras haya otro más
    viejo para el mismo celular sin enviar (porque se está enviando o espera reintento).
    """
    anterior = aliased(MensajeSaliente)
    hay_uno_antes = (
        select(anterior.id)
        .where(
            anterior.telefono == MensajeSaliente.telefono,
            anterior.estado == EstadoMensaje.PENDIENTE,
            anterior.id < MensajeSaliente.id,
        )
        .exists()
    )
    consulta = (
        select(MensajeSaliente)
        .where(
            MensajeSaliente.estado == EstadoMensaje.PENDIENTE,
            MensajeSaliente.proximo_intento_en <= ahora,
            ~hay_uno_antes,
        )
        .order_by(MensajeSaliente.proximo_intento_en, MensajeSaliente.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=MensajeSaliente)
    )
    mensaje = sesion.scalars(consulta).first()
    if mensaje is None:
        return None
    if not sigue_vigente(sesion, mensaje, ahora):
        mensaje.estado = EstadoMensaje.DESCARTADO
        sesion.flush()
        return Resultado.DESCARTADO

    mensaje.intentos += 1
    try:
        # Si el que envía falla a mitad de algo en la base, solo se deshace lo suyo
        # (un SAVEPOINT): el intento igual queda anotado.
        with sesion.begin_nested():
            id_meta = enviador.enviar(sesion, mensaje)
    except EnvioFallido as error:
        resultado = _fallo(mensaje, str(error), reintentar=error.reintentar, ahora=ahora)
    except Exception as error:
        # Un error del programa no se arregla reintentando y no puede trancar la bandeja:
        # este mensaje queda fallido (y en el registro, para arreglar el error) y los
        # demás siguen saliendo, también los del mismo celular.
        logger.exception("Error inesperado al enviar el mensaje %s.", mensaje.id)
        resultado = _fallo(mensaje, f"Error inesperado: {error!r}", reintentar=False, ahora=ahora)
    else:
        mensaje.id_meta = id_meta
        mensaje.estado = EstadoMensaje.ENVIADO
        mensaje.enviado_en = ahora
        resultado = Resultado.ENVIADO
    sesion.flush()
    return resultado


def _fallo(
    mensaje: MensajeSaliente, motivo: str, *, reintentar: bool, ahora: datetime
) -> Resultado:
    mensaje.ultimo_error = motivo
    siguiente = cuando_reintentar(mensaje.intentos, ahora) if reintentar else None
    if siguiente is None:
        mensaje.estado = EstadoMensaje.FALLIDO
        return Resultado.FALLIDO
    mensaje.proximo_intento_en = siguiente
    return Resultado.REINTENTO


# Máximo de mensajes por tanda, para que una vuelta del reloj nunca se alargue demasiado.
# Los que falten salen en la siguiente.
MENSAJES_POR_TANDA = 50


def enviar_pendientes(
    abrir: Callable[[], Session],
    enviador: Enviador,
    *,
    ahora: datetime,
    sigue_vigente: SigueVigente,
    limite: int = MENSAJES_POR_TANDA,
) -> Counter[Resultado]:
    """Envía los mensajes que ya toca enviar, en orden. Devuelve cómo le fue a cada uno.

    Cada mensaje va en su propia transacción: si el servidor se cae a mitad de camino, los
    que ya salieron quedan anotados como enviados y no se repiten.
    """
    cuenta: Counter[Resultado] = Counter()
    for _ in range(limite):
        with abrir() as sesion, sesion.begin():
            resultado = enviar_siguiente(sesion, enviador, ahora=ahora, sigue_vigente=sigue_vigente)
        if resultado is None:
            break
        cuenta[resultado] += 1
    return cuenta


def registrar_estados(sesion: Session, estados: Iterable[tuple[str, str | None]]) -> int:
    """WhatsApp avisa después si un mensaje que aceptó no se pudo entregar.

    Recibe (id de Meta, motivo) de cada mensaje que falló; ese mensaje queda fallido.
    Devuelve cuántos se encontraron.
    """
    cambiados = 0
    for id_meta, motivo in estados:
        resultado = sesion.execute(
            update(MensajeSaliente)
            .where(MensajeSaliente.id_meta == id_meta)
            .values(estado=EstadoMensaje.FALLIDO, ultimo_error=motivo)
            .execution_options(synchronize_session=False)
        )
        cambiados += resultado.rowcount
    return cambiados
