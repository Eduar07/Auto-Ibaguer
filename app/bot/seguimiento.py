"""El seguimiento: volver a escribirle a quien dijo "lo pienso" (pieza 4 del checklist
"¿Tu bot vende o solo contesta?").

WhatsApp deja escribir libremente solo durante las 24 horas siguientes al último mensaje
del cliente; después, solo con una plantilla aprobada por Meta, y un mensaje normal no
llega (error 131047). Por eso el seguimiento se hace siempre **dentro** de esa ventana:

1. El cliente dice "lo pienso" (o "después miro", "de pronto mañana"). El bot le pregunta
   si quiere que le escriba mañana temprano (o más tarde, si mañana ya no alcanza).
2. Si dice que sí, queda un `Seguimiento` con la hora. Si dice que no, nada.
3. A esa hora el reloj le manda cómo está la fila, con el botón de pedir turno.
4. Si antes vuelve a escribir o pide turno, el seguimiento se borra: ya está aquí.

Como un vendedor que pregunta "¿le marco mañana?" y solo marca si le dijeron que sí.
Sin plantillas ni mensajes por fuera de la ventana: nunca un 131047.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import tiempo
from app.bot.mensajes import Mensaje, Opcion
from app.bot.tablas import Seguimiento
from app.bot.textos import BOTONES, Textos, como_va
from app.catalogo.parametros import Parametros, leer_parametros
from app.catalogo.tablas import Fila, TipoVehiculo
from app.clientes import servicio as clientes
from app.integraciones import bandeja_salida
from app.integraciones.tablas import MensajeEntrante
from app.turnos import consultas, reglas
from app.turnos.estados import ESTADOS_ACTIVOS
from app.turnos.tablas import Turno

VENTANA = timedelta(hours=24)  # la de WhatsApp para escribir sin plantilla
MARGEN = timedelta(minutes=30)  # para no rozar el límite (el reloj y el envío tardan)
DESPUES_DE_ABRIR = timedelta(minutes=90)  # "mañana temprano": con la fila ya andando
ANTES_DE_CERRAR = timedelta(minutes=60)  # no se le escribe cuando ya no alcanza a venir
MAS_TARDE = timedelta(hours=4)  # si mañana ya no alcanza: más tarde hoy

MANANA, MAS_TARDE_HOY = "mañana temprano", "más tarde"


@dataclass(frozen=True)
class Cuando:
    momento: datetime
    dicho: str  # "mañana temprano" o "más tarde", para preguntarle


def _sirve(momento: datetime, p: Parametros) -> bool:
    """¿A esa hora se le puede escribir? Que esté abierto y le alcance para venir."""
    local = tiempo.hora_local(momento)
    cierre = reglas.cierre_del_dia(local.date(), p.hora_cierre, local.tzinfo) - ANTES_DE_CERRAR
    return reglas.esta_en_horario(local, p.hora_apertura, p.hora_cierre, p.dias_atencion) and (
        local < cierre
    )


def cuando(ahora: datetime, parametros: Parametros) -> Cuando | None:
    """Cuándo escribirle, dentro de la ventana de WhatsApp. None si no hay un buen momento
    (por ejemplo, mañana no abren y hoy ya no alcanza): entonces no se le ofrece."""
    limite = ahora + VENTANA - MARGEN
    local = tiempo.hora_local(ahora)
    manana = (
        datetime.combine(local.date() + timedelta(days=1), parametros.hora_apertura, local.tzinfo)
        + DESPUES_DE_ABRIR
    )
    if manana <= limite and _sirve(manana, parametros):
        return Cuando(manana, MANANA)
    if _sirve(limite, parametros) and tiempo.hora_local(limite).date() > local.date():
        return Cuando(limite, MANANA)
    tarde = ahora + MAS_TARDE
    if _sirve(tarde, parametros) and tiempo.hora_local(tarde).date() == local.date():
        return Cuando(tarde, MAS_TARDE_HOY)
    return None


def programar(
    sesion: Session,
    telefono: str,
    *,
    momento: datetime,
    cliente_id: int | None,
    tipo_vehiculo_id: int | None,
    ahora: datetime,
) -> None:
    valores = {
        "cliente_id": cliente_id,
        "tipo_vehiculo_id": tipo_vehiculo_id,
        "enviar_en": momento,
        "creado_en": ahora,
    }
    sesion.execute(
        insert(Seguimiento)
        .values(telefono=telefono, **valores)
        .on_conflict_do_update(index_elements=[Seguimiento.telefono], set_=valores)
    )
    sesion.flush()


def cancelar(sesion: Session, telefono: str) -> bool:
    """Volvió a escribir: ya no hace falta. True si había uno."""
    borrados = sesion.execute(delete(Seguimiento).where(Seguimiento.telefono == telefono))
    return bool(borrados.rowcount)


def _le_escribio_desde(sesion: Session, telefono: str, ahora: datetime) -> datetime | None:
    return sesion.scalar(
        select(MensajeEntrante.recibido_en)
        .where(MensajeEntrante.telefono == telefono, MensajeEntrante.recibido_en <= ahora)
        .order_by(MensajeEntrante.recibido_en.desc())
        .limit(1)
    )


def _tiene_turno(sesion: Session, cliente_id: int | None, ahora: datetime) -> bool:
    if cliente_id is None:
        return False
    return bool(
        sesion.scalar(
            select(Turno.id)
            .where(
                Turno.cliente_id == cliente_id,
                Turno.fecha == tiempo.hora_local(ahora).date(),
                Turno.estado.in_(ESTADOS_ACTIVOS),
            )
            .limit(1)
        )
    )


def mensaje(sesion: Session, seguimiento: Seguimiento, ahora: datetime) -> Mensaje:
    """Lo que se le escribe: cómo va la fila que estaba mirando (o todas), con el botón de
    pedir turno. Solo cantidades, como siempre."""
    parametros = leer_parametros(sesion)
    texto = Textos(parametros.textos_bot)
    filas = list(sesion.scalars(select(Fila).order_by(Fila.id)))
    if seguimiento.tipo_vehiculo_id is not None:
        tipo = sesion.get(TipoVehiculo, seguimiento.tipo_vehiculo_id)
        filas = [fila for fila in filas if tipo is not None and fila.id == tipo.fila_id] or filas
    lineas = "\n".join(
        f"*{fila.nombre}:* "
        + como_va(consultas.resumen_de_la_fila(sesion, fila.id, ahora=ahora), texto)
        for fila in filas
    )
    cliente = (
        clientes.buscar_por_telefono(sesion, seguimiento.telefono)
        if seguimiento.cliente_id is not None
        else None
    )
    nombre = f", {cliente.nombre.split()[0]}" if cliente is not None else ""
    return Mensaje(
        texto("seguimiento", nombre=nombre, filas=lineas),
        botones=(
            Opcion("menu.pedir", BOTONES["pedir"]),
            Opcion("menu.precios", BOTONES["precios"]),
        ),
    )


def enviar_los_que_tocan(sesion: Session, *, ahora: datetime) -> int:
    """Deja en la bandeja de salida los seguimientos que ya tocan. Los que ya no sirven
    (pidió turno, se cerró la ventana, el bot está apagado) se borran sin mandar nada.
    Devuelve cuántos se mandaron."""
    debidos = list(
        sesion.scalars(
            select(Seguimiento)
            .where(Seguimiento.enviar_en <= ahora)
            .order_by(Seguimiento.enviar_en)
            .with_for_update(skip_locked=True)
        )
    )
    parametros = leer_parametros(sesion)
    enviados = 0
    for seguimiento in debidos:
        ultimo = _le_escribio_desde(sesion, seguimiento.telefono, ahora)
        sirve = (
            parametros.bot_activo
            and ultimo is not None
            and ultimo > ahora - VENTANA + timedelta(minutes=5)
            and not _tiene_turno(sesion, seguimiento.cliente_id, ahora)
        )
        if sirve:
            bandeja_salida.encolar_respuesta(
                sesion,
                telefono=seguimiento.telefono,
                mensaje=mensaje(sesion, seguimiento, ahora),
                ahora=ahora,
                cliente_id=seguimiento.cliente_id,
            )
            enviados += 1
        sesion.delete(seguimiento)
    sesion.flush()
    return enviados
