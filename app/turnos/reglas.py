"""Reglas de la fila, sin base de datos ni internet.

Son funciones puras: reciben datos y devuelven una decisión. Por eso se prueban solas,
en milisegundos, y el bot, el dashboard y el reloj usan exactamente las mismas.
"""

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

from app.caja.reglas import VALOR_MAXIMO_DE_UN_COBRO, sin_ceros_de_mas
from app.formato import pesos
from app.turnos.errores import PrecioInvalido
from app.turnos.estados import Actor, EstadoTurno, TipoAviso

_E = EstadoTurno
_A = Actor

# Quién puede mover un turno de un estado a otro. Lo que no aparece aquí, no se permite.
TRANSICIONES: dict[tuple[EstadoTurno, EstadoTurno], frozenset[Actor]] = {
    # A la hora de la reserva entra a la fila; si llega antes, ya está en el local.
    (_E.RESERVADO, _E.EN_FILA): frozenset({_A.SISTEMA}),
    (_E.RESERVADO, _E.LLEGO): frozenset({_A.CLIENTE, _A.ADMINISTRADOR}),
    (_E.RESERVADO, _E.CANCELADO): frozenset({_A.CLIENTE, _A.ADMINISTRADOR}),
    # El día de la reserva pasó sin que el reloj corriera (el servidor estaba apagado).
    (_E.RESERVADO, _E.NO_LLEGO): frozenset({_A.SISTEMA}),
    # Cambiar la hora: quien está en la fila (o ya lo llamaron) puede reservar para después.
    (_E.EN_FILA, _E.RESERVADO): frozenset({_A.CLIENTE, _A.ADMINISTRADOR}),
    (_E.LLAMADO, _E.RESERVADO): frozenset({_A.CLIENTE, _A.ADMINISTRADOR}),
    (_E.EN_FILA, _E.LLAMADO): frozenset({_A.SISTEMA}),
    (_E.EN_FILA, _E.LLEGO): frozenset({_A.CLIENTE, _A.ADMINISTRADOR}),
    (_E.EN_FILA, _E.CANCELADO): frozenset({_A.CLIENTE, _A.ADMINISTRADOR}),
    (_E.LLAMADO, _E.LLEGO): frozenset({_A.CLIENTE, _A.ADMINISTRADOR}),
    (_E.LLAMADO, _E.EN_LAVADO): frozenset({_A.ADMINISTRADOR}),
    (_E.LLAMADO, _E.EN_FILA): frozenset({_A.SISTEMA}),  # 1.ª vez sin llegar: al final
    (_E.LLAMADO, _E.NO_LLEGO): frozenset({_A.SISTEMA}),  # 2.ª vez sin llegar
    (_E.LLAMADO, _E.CANCELADO): frozenset({_A.CLIENTE, _A.ADMINISTRADOR}),
    (_E.LLEGO, _E.EN_LAVADO): frozenset({_A.ADMINISTRADOR}),
    # Por WhatsApp solo se cancela En fila o Llamado; después, se resuelve en el local.
    (_E.LLEGO, _E.CANCELADO): frozenset({_A.ADMINISTRADOR}),
    (_E.EN_LAVADO, _E.LISTO): frozenset({_A.ADMINISTRADOR}),
    (_E.LISTO, _E.PAGADO): frozenset({_A.ADMINISTRADOR}),
}


def puede_cambiar(actual: EstadoTurno, nuevo: EstadoTurno, actor: Actor) -> bool:
    """¿Este actor puede pasar el turno de `actual` a `nuevo`?"""
    return actor in TRANSICIONES.get((actual, nuevo), frozenset())


def numero_de_turno(prefijo: str, consecutivo: int) -> str:
    """C-14, M-03: la letra de la fila y el consecutivo del día, con mínimo dos dígitos."""
    return f"{prefijo}-{consecutivo:02d}"


# Cerrar "a las 12:00 a.m." es cerrar a las 12 de la noche: al final del día, no al comienzo.
MEDIANOCHE = time(0)


def esta_en_horario(
    momento_local: datetime, apertura: time, cierre: time, dias: frozenset[int]
) -> bool:
    """¿Se dan turnos por WhatsApp en este momento?

    `momento_local` va en hora de Bogotá y `dias` usa 1 = lunes ... 7 = domingo.
    Abre a la hora de apertura en punto; a la hora de cierre en punto ya no da turnos. Un
    cierre a las 12:00 a.m. es a las 12 de la noche: abierto hasta que termina el día.
    """
    hora = momento_local.time()
    antes_del_cierre = cierre == MEDIANOCHE or hora < cierre
    return momento_local.isoweekday() in dias and apertura <= hora and antes_del_cierre


def cierre_del_dia(fecha: date, cierre: time, zona: tzinfo) -> datetime:
    """Cuándo cierra ese día. A las 12 de la noche es el comienzo del día siguiente."""
    if cierre == MEDIANOCHE:
        return datetime.combine(fecha + timedelta(days=1), MEDIANOCHE, zona)
    return datetime.combine(fecha, cierre, zona)


# --- Reservas: los horarios y los cupos --------------------------------------------------


def horarios_del_dia(
    fecha: date,
    *,
    apertura: time,
    cierre: time,
    dias: frozenset[int],
    cada: timedelta,
    duracion: timedelta,
    zona: tzinfo,
) -> list[datetime]:
    """Las horas que se pueden reservar ese día: desde que abren, cada `cada` (6:30, 7:30,
    8:30...), mientras el servicio (`duracion`) alcance a terminar antes del cierre. Un día
    en que no abren no tiene horarios."""
    if fecha.isoweekday() not in dias or cada <= timedelta(0):
        return []
    hora = datetime.combine(fecha, apertura, zona)
    fin = cierre_del_dia(fecha, cierre, zona)
    horas = []
    while hora + duracion <= fin:
        horas.append(hora)
        hora += cada
    return horas


def cabe_otra_reserva(
    inicio: datetime,
    duracion: timedelta,
    reservas: Sequence[tuple[datetime, timedelta]],
    capacidad: int,
) -> bool:
    """¿Cabe una reserva más de `inicio` a `inicio + duracion` sin que a ningún momento haya
    más vehículos reservados que puestos (`capacidad`)? `reservas` son las que ya hay:
    (hora, duración). Una reserva de 45 minutos a las 6:30 no choca con una de las 7:30."""
    if capacidad <= 0:
        return False
    fin = inicio + duracion
    cruzadas = [
        (hora, hora + dura) for hora, dura in reservas if hora < fin and hora + dura > inicio
    ]
    # Donde más reservas se juntan es al comienzo de alguna: basta revisar esos momentos.
    momentos = [inicio, *(hora for hora, _ in cruzadas if inicio < hora < fin)]
    return all(
        sum(1 for desde, hasta in cruzadas if desde <= momento < hasta) < capacidad
        for momento in momentos
    )


def puestos_para_reservas(
    liberados: Sequence[datetime], reservas: Sequence[datetime], libres: int
) -> int:
    """Cuántos de los puestos `libres` se guardan para las reservas que vienen: así a quien
    reservó no le quita el puesto alguien que entró justo antes.

    `reservas` son las horas de las reservas que llegan antes de que termine un lavado que
    empezara ya. `liberados` dice cuándo termina cada puesto ocupado: si uno se libera antes
    de la hora de una reserva, esa reserva entra ahí y no hace falta guardarle uno libre.
    """
    ocupados = sorted(liberados)
    guardar = 0
    for hora in sorted(reservas):
        if ocupados and ocupados[0] <= hora:
            ocupados.pop(0)
        else:
            guardar += 1
    return min(guardar, max(libres, 0))


# --- Llamados y avisos -----------------------------------------------------------------


@dataclass(frozen=True)
class TurnoEnEspera:
    """Lo que las reglas necesitan saber de cada turno de la fila de espera."""

    id: int
    estado: EstadoTurno
    avisado: bool  # ya se le pidió que saliera hacia el lavadero
    # Le llegó la hora de su reserva: va de primero y puede usar los puestos guardados.
    prioridad: bool = False


@dataclass(frozen=True)
class Llamados:
    llamar: tuple[int, ...]
    # (turno, cuántos turnos tiene adelante), para el aviso anticipado.
    avisar: tuple[tuple[int, int], ...]


def decidir_llamados(
    espera: Sequence[TurnoEnEspera],
    puestos_libres: int,
    avisar_cuando_falten: int,
    guardados: int = 0,
) -> Llamados:
    """Quién pasa a Llamado y quién recibe el aviso anticipado.

    `espera` va en el orden de la fila: primero los que tienen prioridad (les llegó la hora
    de su reserva). Cada puesto libre le corresponde a uno de los primeros de la fila: si
    ese turno sigue En fila, se llama y empieza su tiempo de gracia. Los que ya fueron
    llamados o ya llegaron no se llaman otra vez. `guardados` son los puestos libres que se
    guardan para reservas que vienen (`puestos_para_reservas`): esos solo los usa quien
    tiene prioridad.

    A quien sigue En fila con `avisar_cuando_falten` turnos o menos adelante se le
    avisa, una sola vez, que salga hacia el lavadero.
    """
    libres = max(puestos_libres, 0)
    con_prioridad = min(sum(1 for t in espera if t.prioridad), libres)
    para_los_demas = max(0, libres - con_prioridad - max(guardados, 0))
    primeros = espera[: con_prioridad + para_los_demas]
    llamar = tuple(t.id for t in primeros if t.estado == EstadoTurno.EN_FILA)
    avisar = tuple(
        (t.id, adelante)
        for adelante, t in enumerate(espera)
        if adelante <= avisar_cuando_falten
        and t.estado == EstadoTurno.EN_FILA
        and not t.avisado
        and t.id not in llamar
    )
    return Llamados(llamar=llamar, avisar=avisar)


def elegir_siguiente_para_puesto(espera: Sequence[TurnoEnEspera]) -> int | None:
    """El puesto no espera: entra el primero de la fila que ya está en el local.

    Si el llamado todavía no llega y alguien más atrás ya llegó, ese pasa primero; el
    llamado conserva su tiempo de gracia y entra al siguiente puesto libre. Si nadie ha
    llegado, devuelve None: el puesto espera al llamado.
    """
    return next((t.id for t in espera if t.estado == EstadoTurno.LLEGO), None)


def al_vencer_la_gracia(intentos_sin_llegar: int) -> EstadoTurno:
    """Primera vez sin llegar: vuelve a la fila, al final. Segunda vez: pierde el turno."""
    return EstadoTurno.EN_FILA if intentos_sin_llegar == 0 else EstadoTurno.NO_LLEGO


# En qué estados del turno todavía sirve cada aviso.
ESTADOS_DONDE_SIRVE_EL_AVISO = {
    # Si ya lo llamaron, el que sirve es el aviso del llamado.
    TipoAviso.AVISO_ANTICIPADO: frozenset({_E.EN_FILA}),
    TipoAviso.LLAMADO: frozenset({_E.LLAMADO}),
    # Si nadie más esperaba, al pasar al final lo llaman otra vez de inmediato: igual
    # sirve que sepa que ya perdió una oportunidad.
    TipoAviso.PASA_AL_FINAL: frozenset({_E.EN_FILA, _E.LLAMADO}),
    TipoAviso.NO_LLEGO: frozenset({_E.NO_LLEGO}),
    TipoAviso.VEHICULO_LISTO: frozenset({_E.LISTO}),
    TipoAviso.RECORDATORIO: frozenset({_E.RESERVADO}),
}


def aviso_sigue_vigente(
    tipo: TipoAviso,
    estado: EstadoTurno,
    *,
    gracia_hasta: datetime | None,
    gracia_del_aviso: datetime | None,
    ahora: datetime,
    hora_reservada: datetime | None = None,
    hora_del_aviso: datetime | None = None,
) -> bool:
    """¿Todavía sirve enviar este aviso?

    Un aviso que no salió a tiempo (por ejemplo, WhatsApp estuvo caído) puede quedar
    viejo: si el turno ya cambió, mandarlo tarde confundiría al cliente. El del llamado,
    además, debe ser de este mismo llamado (no de uno anterior) y salir antes de que se
    acabe el tiempo para llegar.
    """
    if estado not in ESTADOS_DONDE_SIRVE_EL_AVISO[tipo]:
        return False
    if tipo == TipoAviso.LLAMADO:
        mismo_llamado = gracia_del_aviso is None or gracia_del_aviso == gracia_hasta
        return mismo_llamado and gracia_hasta is not None and ahora < gracia_hasta
    if tipo == TipoAviso.RECORDATORIO:
        # Si cambió la hora, este recordatorio ya no es el suyo; y después de la hora, sobra.
        misma_hora = hora_del_aviso is None or hora_del_aviso == hora_reservada
        return misma_hora and hora_reservada is not None and ahora < hora_reservada
    return True


# --- Espera estimada -------------------------------------------------------------------


@dataclass(frozen=True)
class Estimacion:
    # Cuándo entra a un puesto cada turno de la fila de espera, en el mismo orden.
    inicios: tuple[datetime, ...]
    # Cuándo entraría un turno nuevo, que llega al final. None si no hay puestos activos.
    siguiente: datetime | None


def estimar_fila(
    libres_desde: Sequence[datetime],
    duraciones: Sequence[timedelta],
    ahora: datetime,
    reservas: Sequence[tuple[datetime, timedelta]] = (),
    duracion_nueva: timedelta | None = None,
) -> Estimacion:
    """Simula los puestos para estimar cuándo le toca a cada turno.

    `libres_desde` dice cuándo queda libre cada puesto activo: ya, si está vacío, o
    cuando termine el vehículo que tiene. Cada turno de la fila, en orden, toma el
    puesto que se libere primero y lo ocupa lo que dure su servicio (`duraciones`, en
    el mismo orden de la fila).

    Las `reservas` que vienen (hora, duración) se atienden a su hora: si un turno de la
    fila no alcanza a terminar antes de que llegue una, ese puesto se queda para la
    reserva y el turno espera el siguiente. `duracion_nueva` es lo que duraría un turno
    nuevo (para `siguiente`); sin ella, lo que dura el último de la fila o 1 hora.

    Es la regla del documento (sumar lo de adelante y repartirlo entre los puestos),
    pero contando también lo que les falta a los que ya se están lavando.
    """
    if not libres_desde:
        return Estimacion(inicios=(), siguiente=None)
    puestos = [max(momento, ahora) for momento in libres_desde]
    heapq.heapify(puestos)
    pendientes = sorted(reservas)

    def entrar(duracion: timedelta) -> datetime:
        # Antes de este turno, las reservas que llegarían mientras se lava.
        while pendientes and pendientes[0][0] < puestos[0] + duracion:
            hora, dura = pendientes.pop(0)
            libre = heapq.heappop(puestos)
            heapq.heappush(puestos, max(hora, libre) + dura)
        inicio = heapq.heappop(puestos)
        heapq.heappush(puestos, inicio + duracion)
        return inicio

    inicios = tuple(entrar(duracion) for duracion in duraciones)
    nueva = duracion_nueva or (duraciones[-1] if duraciones else timedelta(hours=1))
    while pendientes and pendientes[0][0] < puestos[0] + nueva:
        hora, dura = pendientes.pop(0)
        libre = heapq.heappop(puestos)
        heapq.heappush(puestos, max(hora, libre) + dura)
    return Estimacion(inicios=inicios, siguiente=puestos[0])


def minutos_hasta(momento: datetime | None, ahora: datetime) -> int | None:
    """Minutos que faltan, redondeados hacia arriba. 0 si ya pasó; None si no se sabe."""
    if momento is None:
        return None
    return max(0, math.ceil((momento - ahora).total_seconds() / 60))


# --- Cobro -----------------------------------------------------------------------------


def motivo_del_cobro(precio_menu: int, precio_cobrado: int, motivo: str | None) -> str | None:
    """Valida el precio cobrado y devuelve el motivo limpio (o None).

    El administrador puede cobrar distinto al menú (descuento, barro extra...), pero
    entonces debe escribir el motivo: así Reportes muestra cuánto se fue en descuentos y
    cuánto entró por extras.
    """
    if precio_cobrado < 0:
        raise PrecioInvalido("El precio cobrado no puede ser negativo.")
    sin_ceros_de_mas(precio_cobrado, VALOR_MAXIMO_DE_UN_COBRO, PrecioInvalido)
    limpio = (motivo or "").strip() or None
    if precio_cobrado != precio_menu and limpio is None:
        raise PrecioInvalido(
            f"Vas a cobrar {pesos(precio_cobrado)} y el menú dice {pesos(precio_menu)}: "
            "escribe el motivo."
        )
    return limpio
