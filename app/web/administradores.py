"""Crear administradores del dashboard o cambiarles la contraseña."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.web.claves import cifrar_clave
from app.web.tablas import Administrador

FORMATO_USUARIO = re.compile(r"[a-z0-9._-]{3,40}")


def guardar_administrador(sesion: Session, usuario: str, clave: str) -> tuple[Administrador, bool]:
    """Crea el administrador o, si ya existe, le cambia la contraseña y lo reactiva.

    Devuelve (administrador, ¿se creó?).
    """
    usuario = usuario.strip().lower()
    if not FORMATO_USUARIO.fullmatch(usuario):
        raise ValueError(
            "El usuario debe tener de 3 a 40 caracteres: "
            "letras, números, punto, guion o guion bajo."
        )
    clave_cifrada = cifrar_clave(clave)

    administrador = sesion.scalars(select(Administrador).filter_by(usuario=usuario)).one_or_none()
    creado = administrador is None
    if administrador is None:
        administrador = Administrador(usuario=usuario, clave_cifrada=clave_cifrada)
        sesion.add(administrador)
    else:
        administrador.clave_cifrada = clave_cifrada
        administrador.activo = True
    sesion.flush()
    return administrador, creado
