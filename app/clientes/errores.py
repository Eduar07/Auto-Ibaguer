"""Lo que las reglas de clientes y vehículos no permiten."""

from app.errores import ReglaDelNegocio


class FaltaAutorizacion(ReglaDelNegocio):
    """Ley 1581: sin aceptar el aviso de datos no se guarda nada de la persona."""


class TelefonoInvalido(ReglaDelNegocio):
    pass


class NombreInvalido(ReglaDelNegocio):
    pass


class PlacaInvalida(ReglaDelNegocio):
    pass


class PlacaDeOtroCliente(ReglaDelNegocio):
    """Dos vehículos nunca comparten placa, y una placa es de un solo cliente."""


class NoExiste(ReglaDelNegocio):
    """Se pidió un cliente, vehículo o tipo de vehículo que no está en la base."""


class TelefonoDeOtroCliente(ReglaDelNegocio):
    """Un celular identifica a un solo cliente."""


class TieneTurnoActivo(ReglaDelNegocio):
    """Mientras su vehículo esté en la fila, no se borran los datos del cliente."""
