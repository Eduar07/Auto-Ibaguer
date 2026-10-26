"""Contraseñas del dashboard: se cifran con Argon2 y nunca se guardan tal cual."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

LARGO_MINIMO = 10

_cifrador = PasswordHasher()


def cifrar_clave(clave: str) -> str:
    """Devuelve la contraseña cifrada. Rechaza contraseñas cortas."""
    if len(clave) < LARGO_MINIMO:
        raise ValueError(f"La contraseña debe tener al menos {LARGO_MINIMO} caracteres.")
    return _cifrador.hash(clave)


def verificar_clave(clave_cifrada: str, clave: str) -> bool:
    """¿La contraseña escrita corresponde a la guardada?"""
    try:
        return _cifrador.verify(clave_cifrada, clave)
    except (VerificationError, InvalidHashError):
        return False
