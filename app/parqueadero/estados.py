"""Las listas cerradas del parqueadero: tipos de vehículo, en qué va cada parqueo y qué
clase de corrección se le hizo a un cobro."""

from enum import StrEnum


class TipoParqueo(StrEnum):
    """Lo que se parquea. Una camioneta paga como carro."""

    CARRO = "carro"
    MOTO = "moto"


NOMBRE_DEL_TIPO = {TipoParqueo.CARRO: "Carro", TipoParqueo.MOTO: "Moto"}


class EstadoParqueo(StrEnum):
    ADENTRO = "adentro"  # entró y no ha salido
    SALIO = "salio"  # ya salió: se cobró (o era mensualista al día)
    ANULADO = "anulado"  # se registró por error (placa equivocada, entrada repetida)


class TipoCorreccion(StrEnum):
    """Qué se le hizo a un cobro equivocado. El cobro de antes queda anotado en la corrección."""

    CORREGIDO = "corregido"  # cambió el valor o el medio de pago
    SALIDA_DESHECHA = "salida_deshecha"  # el vehículo volvió a quedar adentro
    PAGO_ANULADO = "pago_anulado"  # el pago de la mensualidad se quitó: esos días se deben
