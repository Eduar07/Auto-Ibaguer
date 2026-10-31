"""Reglas de la fila, sin base de datos ni internet.

Son funciones puras: reciben datos y devuelven una decisión. Por eso se prueban solas,
en milisegundos, y el bot, el dashboard y el reloj usan exactamente las mismas.
"""

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.formato import pesos
from app.turnos.errores import PrecioInvalido
from app.turnos.estados import Actor, EstadoTurno

_E = EstadoTurno
_A = Actor

# Quién puede mover un turno de un estado a otro. Lo que no aparece aquí, no se permite.
TRANSICIONES: dict[tuple[EstadoTurno, EstadoTurno], frozenset[Actor]] = {
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


def esta_en_horario(
    momento_local: datetime, apertura: time, cierre: time, dias: frozenset[int]
) -> bool:
    """¿Se dan turnos por WhatsApp en este momento?

    `momento_local` va en hora de Bogotá y `dias` usa 1 = lunes ... 7 = domingo.
    Abre a la hora de apertura en punto; a la hora de cierre en punto ya no da turnos.
    """
    return momento_local.isoweekday() in dias and apertura <= momento_local.time() < cierre


# --- Llamados y avisos -----------------------------------------------------------------


@dataclass(frozen=True)
class TurnoEnEspera:
    """Lo que las reglas necesitan saber de cada turno de la fila de espera."""

    id: int
    estado: EstadoTurno
    avisado: bool  # ya se le pidió que saliera hacia el lavadero


@dataclass(frozen=True)
class Llamados:
    llamar: tuple[int, ...]
    # (turno, cuántos turnos tiene adelante), para el aviso anticipado.
    avisar: tuple[tuple[int, int], ...]


def decidir_llamados(
    espera: Sequence[TurnoEnEspera], puestos_libres: int, avisar_cuando_falten: int
) -> Llamados:
    """Quién pasa a Llamado y quién recibe el aviso anticipado.

    `espera` va en el orden de la fila. Cada puesto libre le corresponde a uno de los
    primeros de la fila: si ese turno sigue En fila, se llama y empieza su tiempo de
    gracia. Los que ya fueron llamados o ya llegaron no se llaman otra vez.

    A quien sigue En fila con `avisar_cuando_falten` turnos o menos adelante se le
    avisa, una sola vez, que salga hacia el lavadero.
    """
    primeros = espera[: max(puestos_libres, 0)]
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


# --- Espera estimada -------------------------------------------------------------------


@dataclass(frozen=True)
class Estimacion:
    # Cuándo entra a un puesto cada turno de la fila de espera, en el mismo orden.
    inicios: tuple[datetime, ...]
    # Cuándo entraría un turno nuevo, que llega al final. None si no hay puestos activos.
    siguiente: datetime | None


def estimar_fila(
    libres_desde: Sequence[datetime], duraciones: Sequence[timedelta], ahora: datetime
) -> Estimacion:
    """Simula los puestos para estimar cuándo le toca a cada turno.

    `libres_desde` dice cuándo queda libre cada puesto activo: ya, si está vacío, o
    cuando termine el vehículo que tiene. Cada turno de la fila, en orden, toma el
    puesto que se libere primero y lo ocupa lo que dure su servicio (`duraciones`, en
    el mismo orden de la fila).

    Es la regla del documento (sumar lo de adelante y repartirlo entre los puestos),
    pero contando también lo que les falta a los que ya se están lavando.
    """
    if not libres_desde:
        return Estimacion(inicios=(), siguiente=None)
    puestos = [max(momento, ahora) for momento in libres_desde]
    heapq.heapify(puestos)
    inicios = []
    for duracion in duraciones:
        inicio = heapq.heappop(puestos)
        inicios.append(inicio)
        heapq.heappush(puestos, inicio + duracion)
    return Estimacion(inicios=tuple(inicios), siguiente=puestos[0])


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
    limpio = (motivo or "").strip() or None
    if precio_cobrado != precio_menu and limpio is None:
        raise PrecioInvalido(
            f"Vas a cobrar {pesos(precio_cobrado)} y el menú dice {pesos(precio_menu)}: "
            "escribe el motivo."
        )
    return limpio
