"""Lo que las reglas del parqueadero no permiten. El mensaje se le muestra tal cual a la persona."""

from app.caja.errores import (  # noqa: F401  (las de la caja son comunes a los dos negocios)
    CajaNoCorregible,
    CajaYaAbierta,
    CobroNoCorregible,
    SinCajaAbierta,
    ValorInvalido,
)
from app.errores import ReglaDelNegocio


class PlacaInvalida(ReglaDelNegocio):
    pass


class YaEstaAdentro(ReglaDelNegocio):
    """Una placa no puede tener dos entradas abiertas: sería cobrar dos veces el mismo carro."""


class NoEstaAdentro(ReglaDelNegocio):
    """Se pidió la salida de un vehículo que ya salió, se anuló o no existe."""


class MensualidadInvalida(ReglaDelNegocio):
    pass


class TarifaInvalida(ReglaDelNegocio):
    pass
