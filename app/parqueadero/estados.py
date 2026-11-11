"""Las listas cerradas del parqueadero: tipos de vehículo y en qué va cada parqueo."""

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
