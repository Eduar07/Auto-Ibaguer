"""Las reglas de plata del parqueadero, sin base de datos: cuánto se cobra y cómo cuadra la caja.

Es la parte que más pruebas tiene: cada caso que falló en el sistema de parqueadero que se
revisó (cobrar todo por minuto, sumar el billete en vez de lo cobrado, cobrar dos veces)
tiene aquí su prueba (pruebas/test_parqueadero_reglas.py).
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.clientes.reglas import normalizar_placa
from app.formato import pesos
from app.parqueadero.errores import (
    MensualidadInvalida,
    PlacaInvalida,
    TarifaInvalida,
    ValorInvalido,
)
from app.turnos.estados import MedioPago

# Los primeros minutos de la primera hora que se pueden cobrar como fracción (Bucaramanga:
# desde el minuto 16 de la primera hora se cobra la hora completa).
FRACCION_MINUTOS = 15

# 30 días calendario seguidos, contando el día en que empieza: del 10 de noviembre al 9 de
# diciembre. Así la define el decreto de tarifas de Bucaramanga.
DIAS_DE_LA_MENSUALIDAD = 30

# El mes siguiente se puede cobrar desde estos días antes de que se acabe lo pagado (y en la
# lista sale en naranja). Antes de eso está al día: cobrarle sería adelantar un mes.
DIAS_PARA_COBRAR_ANTES = 3

# Una placa limpia: letras y números, con al menos una de cada una. Cabe la de carro
# (ABC123), la de moto (ABC12D), la de moto vieja (ABC12) y las especiales (R12345).
_PLACA = re.compile(r"(?=.*[A-Z])(?=.*\d)[A-Z0-9]{4,7}")


@dataclass(frozen=True)
class Tarifa:
    """Lo que se le cobra a un tipo de vehículo. Se copia en cada entrada: si mañana sube el
    precio, el que ya estaba adentro paga el de cuando entró."""

    valor_hora: int
    gracia_minutos: int
    # Lo que valen los primeros 15 minutos. None: desde el primer minuto se cobra la hora.
    valor_fraccion: int | None = None


@dataclass(frozen=True)
class Cobro:
    minutos: int  # el tiempo que estuvo adentro, en minutos completos
    horas: int  # las horas cobradas (0 si se cobró solo la fracción)
    valor: int
    fraccion: bool = False


def validar_placa(texto: str) -> str:
    """ " abc-123 " -> "ABC123". Rechaza lo que no puede ser una placa ("123", "ABCDEF")."""
    placa = normalizar_placa(texto)
    if _PLACA.fullmatch(placa) is None:
        raise PlacaInvalida(
            f"{placa or 'Eso'} no parece una placa: escribe letras y números, como ABC123 o ABC12D."
        )
    return placa


def validar_tarifa(tarifa: Tarifa) -> Tarifa:
    if tarifa.valor_hora <= 0:
        raise TarifaInvalida("El valor de la hora debe ser mayor que cero.")
    if not 0 <= tarifa.gracia_minutos <= 59:
        raise TarifaInvalida("Los minutos de gracia van de 0 a 59.")
    if tarifa.valor_fraccion is not None and tarifa.valor_fraccion < 0:
        raise TarifaInvalida("El valor de los primeros 15 minutos no puede ser negativo.")
    return tarifa


def minutos_adentro(entrada: datetime, salida: datetime) -> int:
    """Los minutos completos entre la entrada y la salida (1 h 15 min 50 s -> 75)."""
    return max(0, int((salida - entrada).total_seconds() // 60))


def horas_a_cobrar(minutos: int, gracia_minutos: int) -> int:
    """Hora o fracción, con gracia: después de cada hora completa hay `gracia_minutos` antes
    de cobrar la siguiente. Mínimo una hora.

    Con 15 de gracia: hasta 1 h 15 min es 1 hora; desde 1 h 16 min, 2 horas; desde
    2 h 16 min, 3 horas. Es como la tolerancia de un cine: llegar 5 minutos tarde no
    cambia la función que te toca.
    """
    sobre_la_gracia = max(0, minutos) - gracia_minutos
    return max(1, -(-sobre_la_gracia // 60))  # división hacia arriba, sin decimales


def calcular_cobro(minutos: int, tarifa: Tarifa) -> Cobro:
    """Lo que paga un vehículo por `minutos` adentro."""
    minutos = max(0, minutos)
    if tarifa.valor_fraccion is not None and minutos <= FRACCION_MINUTOS:
        return Cobro(minutos=minutos, horas=0, valor=tarifa.valor_fraccion, fraccion=True)
    horas = horas_a_cobrar(minutos, tarifa.gracia_minutos)
    return Cobro(minutos=minutos, horas=horas, valor=horas * tarifa.valor_hora)


def motivo_del_ajuste(calculado: int, cobrado: int, motivo: str | None) -> str | None:
    """Se puede cobrar distinto a la tarifa (un descuento, una noche entera), pero con motivo:
    así el cierre de caja muestra cuánto se fue en descuentos."""
    if cobrado < 0:
        raise ValorInvalido("El valor cobrado no puede ser negativo.")
    limpio = (motivo or "").strip() or None
    if cobrado != calculado and limpio is None:
        raise ValorInvalido(
            f"Vas a cobrar {pesos(cobrado)} y la tarifa da {pesos(calculado)}: escribe el motivo."
        )
    return limpio


def revisar_recibido(cobrado: int, medio: MedioPago, recibido: int | None) -> int | None:
    """Lo que entregó el cliente en efectivo, solo para calcular y anotar el vuelto.

    Nunca entra a la caja: si pagan $6.000 con un billete de $50.000, a la caja entran
    $6.000. Contar el billete fue uno de los errores del otro sistema.
    """
    if recibido is None or medio is not MedioPago.EFECTIVO:
        return None
    if recibido < cobrado:
        raise ValorInvalido(f"Con {pesos(recibido)} no alcanza: el cobro es de {pesos(cobrado)}.")
    return recibido


def fin_de_la_mensualidad(desde: date) -> date:
    """Hasta qué día va una mensualidad que empieza `desde` (el 10 de noviembre -> el 9 de
    diciembre)."""
    return desde + timedelta(days=DIAS_DE_LA_MENSUALIDAD - 1)


def revisar_periodo(desde: date, hasta: date) -> None:
    if hasta < desde:
        raise ValorInvalido("La fecha final del periodo no puede ser antes de la inicial.")
    if (hasta - desde).days > 366:
        raise ValorInvalido("Un pago de mensualidad cubre máximo un año.")


def cubierta_hasta(periodos: Iterable[tuple[date, date]], dia: date) -> date | None:
    """Si algún periodo pagado cubre `dia`, hasta cuándo va lo pagado sin cortes: un periodo
    que empieza justo después del anterior lo alarga. None: ese día no está pagado."""
    hasta = None
    for desde, fin in sorted(periodos):
        if hasta is None:
            if desde <= dia <= fin:
                hasta = fin
        elif desde <= hasta + timedelta(days=1):
            hasta = max(hasta, fin)
    return hasta


def se_puede_cobrar(al_dia_hasta: date | None, hoy: date) -> bool:
    """¿Ya toca cobrarle el mes siguiente? Sí, si hoy no está pagado o lo pagado se acaba en
    3 días o menos. Si está al día por más tiempo, cobrarle es adelantar un mes."""
    return al_dia_hasta is None or (al_dia_hasta - hoy).days <= DIAS_PARA_COBRAR_ANTES


def cobrar_desde(al_dia_hasta: date) -> date:
    """El primer día en que se puede cobrar el mes siguiente sin que sea un adelanto."""
    return al_dia_hasta - timedelta(days=DIAS_PARA_COBRAR_ANTES)


def periodo_siguiente(ultimo_pagado: date | None, hoy: date) -> tuple[date, date]:
    """El siguiente mes por cobrar.

    Sigue al último día pagado aunque pague tarde: si se le venció el 10 y paga el 13, el mes
    va del 11 al 10 del otro mes (esos días también tuvo el puesto; si no, cada atraso le
    regalaría días). Si ese mes ya ni siquiera cubre hoy (lleva más de un mes sin pagar, o
    nunca ha pagado), empieza hoy.
    """
    if ultimo_pagado is not None:
        desde = ultimo_pagado + timedelta(days=1)
        if fin_de_la_mensualidad(desde) >= hoy:
            return desde, fin_de_la_mensualidad(desde)
    return hoy, fin_de_la_mensualidad(hoy)


def revisar_dias_traidos(hasta: date, hoy: date) -> None:
    """Hasta qué día ya traía pagado un mensualista nuevo (por ejemplo, con el dueño anterior).
    Cubre desde hoy: una fecha que ya pasó no sirve (ese mes se le cobra)."""
    if hasta < hoy:
        raise MensualidadInvalida(
            "Esa fecha ya pasó: deja vacío lo que ya traía pagado y cóbrale el mes."
        )
    revisar_periodo(hoy, hasta)


def placa_o_ticket(texto: str) -> tuple[str | None, int | None]:
    """Lo que se escribe para buscar una salida: "123" es el ticket 123; "abc123", una placa."""
    limpio = texto.strip().lstrip("#")
    if limpio.isdigit():
        return None, int(limpio)
    placa = normalizar_placa(limpio)
    return (placa or None), None


NOMBRE_DEL_MEDIO = {
    MedioPago.EFECTIVO: "Efectivo",
    MedioPago.NEQUI: "Nequi",
    MedioPago.DAVIPLATA: "Daviplata",
    MedioPago.TARJETA: "Tarjeta",
}


def como_se_pago(medio: MedioPago | None) -> str:
    """Para una frase: "en efectivo", "por Nequi", "por Daviplata", "con tarjeta" o "sin pago"."""
    if medio is None:
        return "sin pago"
    if medio is MedioPago.EFECTIVO:
        return "en efectivo"
    if medio is MedioPago.TARJETA:
        return "con tarjeta"
    return f"por {NOMBRE_DEL_MEDIO[medio]}"


@dataclass(frozen=True)
class Cuadre:
    """Lo que debería haber en una caja: la base más lo que entró en efectivo. Lo que entró
    por Nequi, Daviplata o tarjeta se muestra aparte: no está en el cajón."""

    base: int
    parqueos: Mapping[MedioPago, int]
    mensualidades: Mapping[MedioPago, int]

    def por_medio(self, medio: MedioPago) -> int:
        return self.parqueos.get(medio, 0) + self.mensualidades.get(medio, 0)

    @property
    def total(self) -> int:
        """Todo lo que entró en el turno, por cualquier medio (sin la base)."""
        return sum(self.por_medio(medio) for medio in MedioPago)

    @property
    def efectivo_esperado(self) -> int:
        return self.base + self.por_medio(MedioPago.EFECTIVO)


def sumar_por_medio(cobros: Iterable[tuple[MedioPago, int]]) -> dict[MedioPago, int]:
    totales: dict[MedioPago, int] = {}
    for medio, valor in cobros:
        totales[medio] = totales.get(medio, 0) + valor
    return totales
