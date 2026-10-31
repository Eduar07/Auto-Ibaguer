"""Reglas de clientes y vehículos, sin base de datos: teléfonos, nombres y placas."""

import re

from app.clientes.errores import NombreInvalido, PlacaInvalida, TelefonoInvalido

LARGO_MAXIMO_NOMBRE = 80


def normalizar_telefono(texto: str) -> str:
    """ "+57 322 755 6850" o "3227556850" -> "573227556850".

    Se guarda como lo trae WhatsApp: formato internacional, solo dígitos. A un celular
    colombiano de 10 dígitos se le agrega el 57.
    """
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) == 10 and digitos.startswith("3"):
        digitos = "57" + digitos
    if not 8 <= len(digitos) <= 15:
        raise TelefonoInvalido(f"El teléfono {texto.strip()!r} no es válido.")
    return digitos


def nombre_bonito(texto: str) -> str:
    """ "  eduar   GUERRERO " -> "Eduar Guerrero": mayúscula inicial en cada palabra."""
    palabras = texto.split()
    if not palabras:
        raise NombreInvalido("Falta el nombre.")
    nombre = " ".join(palabra[:1].upper() + palabra[1:].lower() for palabra in palabras)
    if len(nombre) > LARGO_MAXIMO_NOMBRE:
        raise NombreInvalido(f"El nombre puede tener máximo {LARGO_MAXIMO_NOMBRE} caracteres.")
    return nombre


def normalizar_placa(texto: str) -> str:
    """ " abc-123 " -> "ABC123": sin espacios, guiones ni puntos, y en mayúsculas."""
    return re.sub(r"[\s.\-]", "", texto).upper()


def validar_placa(texto: str, patron: str, tipo_vehiculo: str) -> str:
    """Devuelve la placa limpia si tiene el formato del tipo de vehículo.

    Carro: 3 letras y 3 números (ABC123). Moto: 3 letras, 2 números y 1 letra (ABC12D).
    """
    placa = normalizar_placa(texto)
    if re.fullmatch(patron, placa) is None:
        raise PlacaInvalida(
            f"La placa {placa or texto!r} no tiene el formato de placa de {tipo_vehiculo.lower()}."
        )
    return placa
