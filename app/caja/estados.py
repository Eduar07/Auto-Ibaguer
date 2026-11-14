"""Las listas cerradas de la caja: de qué negocio es, qué dato de ella se corrigió, qué se le
hizo a un cobro equivocado y qué plata salió del cajón."""

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
    SALIDA = "salida"  # una salida de plata que se anuló (no salió, o no era de esta caja)


class TipoCorreccion(StrEnum):
    """Qué se le hizo a un cobro equivocado. El cobro de antes queda anotado en la corrección."""

    CORREGIDO = "corregido"  # cambió el valor o el medio de pago
    SALIDA_DESHECHA = "salida_deshecha"  # parqueadero: el vehículo volvió a quedar adentro
    PAGO_ANULADO = "pago_anulado"  # parqueadero: el pago de la mensualidad se quitó
    COBRO_DESHECHO = "cobro_deshecho"  # lavadero: el turno volvió a quedar por cobrar


class TipoDeSalida(StrEnum):
    """Plata que sale del cajón. No es un cobro al revés: es lo que se paga, se lleva el dueño o
    se consigna, y el cuadre la resta de lo que debe haber al cerrar."""

    GASTO = "gasto"  # se pagó algo con plata del cajón: bolsas, un repuesto, el almuerzo del turno
    RETIRO = "retiro"  # un jefe se llevó plata
    CONSIGNACION = "consignacion"  # se llevó plata al banco


NOMBRE_DE_LA_SALIDA = {
    TipoDeSalida.GASTO: "Gasto",
    TipoDeSalida.RETIRO: "Retiro",
    TipoDeSalida.CONSIGNACION: "Consignación",
}


class CategoriaDeGasto(StrEnum):
    """En qué se gastó: así el resultado del mes dice cuánto se fue en cada cosa."""

    INSUMOS = "insumos"  # jabón, cera, bolsas, tickets
    ASEO = "aseo"
    SERVICIOS = "servicios"  # agua, luz, internet
    NOMINA = "nomina"  # pagos a trabajadores
    MANTENIMIENTO = "mantenimiento"  # repuestos, arreglos
    ARRIENDO = "arriendo"
    VIGILANCIA = "vigilancia"
    TRANSPORTE = "transporte"
    OTRO = "otro"


NOMBRE_DE_LA_CATEGORIA = {
    CategoriaDeGasto.INSUMOS: "Insumos",
    CategoriaDeGasto.ASEO: "Aseo",
    CategoriaDeGasto.SERVICIOS: "Servicios públicos",
    CategoriaDeGasto.NOMINA: "Nómina",
    CategoriaDeGasto.MANTENIMIENTO: "Mantenimiento",
    CategoriaDeGasto.ARRIENDO: "Arriendo",
    CategoriaDeGasto.VIGILANCIA: "Vigilancia",
    CategoriaDeGasto.TRANSPORTE: "Transporte",
    CategoriaDeGasto.OTRO: "Otro",
}
