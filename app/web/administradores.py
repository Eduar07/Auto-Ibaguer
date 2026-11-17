"""Los usuarios del dashboard: crearlos, cambiarles la contraseña, el rol o el negocio."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.caja.estados import Negocio
from app.errores import ReglaDelNegocio
from app.web.claves import cifrar_clave
from app.web.tablas import Administrador, Rol

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


# --- Los usuarios desde el dashboard (pantalla Usuarios) -------------------------------------


class UsuarioInvalido(ReglaDelNegocio):
    pass


def _rol_y_negocio(rol: Rol | str, negocio: Negocio | str | None) -> tuple[Rol, Negocio | None]:
    try:
        rol = Rol(rol)
    except ValueError:
        raise UsuarioInvalido("Elige si es administrador o cajero.") from None
    if rol is Rol.ADMINISTRADOR:
        return rol, None  # un administrador entra a los dos negocios
    try:
        return rol, Negocio(negocio) if negocio else None
    except ValueError:
        raise UsuarioInvalido("Elige a qué negocio entra.") from None


def crear_usuario(
    sesion: Session, usuario: str, clave: str, rol: Rol | str, negocio: Negocio | str | None
) -> Administrador:
    rol, negocio = _rol_y_negocio(rol, negocio)
    limpio = usuario.strip().lower()
    if sesion.scalars(select(Administrador).filter_by(usuario=limpio)).first() is not None:
        raise UsuarioInvalido(f"Ya hay un usuario {limpio}: elige otro nombre.")
    try:
        nuevo, _ = guardar_administrador(sesion, limpio, clave)
    except ValueError as error:
        raise UsuarioInvalido(str(error)) from error
    nuevo.rol, nuevo.negocio = rol, negocio
    sesion.flush()
    return nuevo


def _administradores_activos(sesion: Session) -> int:
    return len(
        sesion.scalars(
            select(Administrador).where(
                Administrador.activo, Administrador.rol == Rol.ADMINISTRADOR
            )
        ).all()
    )


def cambiar_usuario(
    sesion: Session,
    quien: Administrador,
    usuario_id: int,
    *,
    rol: Rol | str,
    negocio: Negocio | str | None,
    activo: bool,
    clave: str = "",
) -> Administrador:
    """Cambia el rol, el negocio, si está activo y, si se escribe, la contraseña. Nadie se
    quita a sí mismo el acceso, y siempre queda al menos un administrador activo."""
    rol, negocio = _rol_y_negocio(rol, negocio)
    usuario = sesion.get(Administrador, usuario_id, with_for_update=True)
    if usuario is None:
        raise UsuarioInvalido("Ese usuario no existe.")
    if usuario.id == quien.id and (not activo or rol is not Rol.ADMINISTRADOR):
        raise UsuarioInvalido("No puedes quitarte a ti mismo el acceso de administrador.")
    era_administrador_activo = usuario.activo and usuario.es_administrador
    usuario.rol, usuario.negocio, usuario.activo = rol, negocio, activo
    if era_administrador_activo and not (activo and rol is Rol.ADMINISTRADOR):
        sesion.flush()
        if _administradores_activos(sesion) == 0:
            raise UsuarioInvalido("Tiene que quedar al menos un administrador activo.")
    if clave:
        try:
            usuario.clave_cifrada = cifrar_clave(clave)  # cierra sus sesiones abiertas
        except ValueError as error:
            raise UsuarioInvalido(str(error)) from error
    sesion.flush()
    return usuario
