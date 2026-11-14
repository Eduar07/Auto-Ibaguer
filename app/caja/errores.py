"""Lo que las reglas de la caja no permiten. El mensaje se le muestra tal cual a la persona."""

from app.errores import ReglaDelNegocio


class SinCajaAbierta(ReglaDelNegocio):
    """Todo cobro entra a una caja: sin caja abierta, la plata no tendría dónde contarse."""


class CajaYaAbierta(ReglaDelNegocio):
    pass


class CobroNoCorregible(ReglaDelNegocio):
    """Un cobro que ya no se puede corregir: ya se deshizo o se anuló, no entró a ninguna caja,
    o su caja se cerró hace demasiado."""


class CajaNoCorregible(ReglaDelNegocio):
    """Una caja que no se puede corregir así: el conteo de una abierta (todavía no se contó) o
    una que se cerró hace demasiado."""


class ValorInvalido(ReglaDelNegocio):
    """Un valor o un motivo que no cuadra: negativo, sin motivo, un pago que no alcanza."""
