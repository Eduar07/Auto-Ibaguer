"""Las listas cerradas de la caja: de qué negocio es, qué dato de ella se corrigió y qué se le
hizo a un cobro equivocado."""

from enum import StrEnum


class Negocio(StrEnum):
    """Cada negocio tiene su caja, aparte: la plata del lavadero no se mezcla con la del
    parqueadero."""

    LAVADERO = "lavadero"
    PARQUEADERO = "parqueadero"


NOMBRE_DEL_NEGOCIO = {Negocio.LAVADERO: "Lavadero", Negocio.PARQUEADERO: "Parqueadero"}


class DatoDeCaja(StrEnum):
    """Lo que se puede corregir de la caja misma, aparte de sus cobros."""

    BASE = "base"  # la plata para vueltos con que se abrió
    CONTEO = "conteo"  # el efectivo que se contó al cerrar


class TipoCorreccion(StrEnum):
    """Qué se le hizo a un cobro equivocado. El cobro de antes queda anotado en la corrección."""

    CORREGIDO = "corregido"  # cambió el valor o el medio de pago
    SALIDA_DESHECHA = "salida_deshecha"  # parqueadero: el vehículo volvió a quedar adentro
    PAGO_ANULADO = "pago_anulado"  # parqueadero: el pago de la mensualidad se quitó
    COBRO_DESHECHO = "cobro_deshecho"  # lavadero: el turno volvió a quedar por cobrar
