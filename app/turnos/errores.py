"""Lo que las reglas de la fila no permiten."""

from app.errores import ReglaDelNegocio


class TurnoNoExiste(ReglaDelNegocio):
    def __init__(self, turno_id: int) -> None:
        super().__init__(f"No existe el turno {turno_id}.")


class FueraDeHorario(ReglaDelNegocio):
    """Por WhatsApp solo se dan turnos dentro del horario de atención."""


class YaTieneTurnoActivo(ReglaDelNegocio):
    """Por WhatsApp, un teléfono tiene máximo un turno activo en cada fila."""

    def __init__(self, turno_id: int, numero: str) -> None:
        super().__init__(f"Ya tienes el turno {numero} activo en esta fila.")
        self.turno_id = turno_id
        self.numero = numero


class CambioNoPermitido(ReglaDelNegocio):
    """El turno no puede pasar a ese estado desde el estado en que está."""


class PuestoNoDisponible(ReglaDelNegocio):
    """El puesto está ocupado, apagado o es de la otra fila."""


class ServicioNoDisponible(ReglaDelNegocio):
    """El servicio no existe, está apagado o no tiene precio para ese vehículo."""


class VehiculoDeOtraFila(ReglaDelNegocio):
    """Un carro no puede quedar en la fila de motos, ni al revés."""


class FaltaPlaca(ReglaDelNegocio):
    """No se cobra sin placa: sin ella el vehículo no acumula en la promoción."""


class FaltaServicio(ReglaDelNegocio):
    """No se cobra con el servicio por definir: sin él no hay precio."""


class PrecioInvalido(ReglaDelNegocio):
    """Precio negativo, o distinto al del menú sin escribir el motivo."""
