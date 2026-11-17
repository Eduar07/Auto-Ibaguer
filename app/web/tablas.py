"""Tabla de los usuarios del dashboard: los administradores y los cajeros."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Identity, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.caja.estados import Negocio
from app.db.base import Base, lista_cerrada


class Rol(StrEnum):
    """Qué puede hacer un usuario.

    - Administrador: todo, en los dos negocios (también los usuarios y el resultado del mes).
    - Cajero: el día a día de su negocio (la fila o las entradas, cobrar, la caja, anotar
      salidas), pero no cambia precios, tarifas ni configuración, no corrige cobros ni cajas
      ya cobradas, y no ve el Balance ni el resultado del mes.
    """

    ADMINISTRADOR = "administrador"
    CAJERO = "cajero"


NOMBRE_DEL_ROL = {Rol.ADMINISTRADOR: "Administrador", Rol.CAJERO: "Cajero"}


class Administrador(Base):
    """Quién puede entrar al dashboard (la tabla conserva su nombre de siempre).

    La contraseña nunca se guarda tal cual: se guarda cifrada con Argon2, de forma que
    ni leyendo la base de datos se puede saber cuál es.
    """

    __tablename__ = "administrador"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    usuario: Mapped[str] = mapped_column(String(40), unique=True)
    clave_cifrada: Mapped[str] = mapped_column(String(255))
    activo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rol: Mapped[Rol] = mapped_column(
        lista_cerrada(Rol, "rol"), default=Rol.ADMINISTRADOR, server_default="administrador"
    )
    # A qué negocio entra. Vacío: a los dos.
    negocio: Mapped[Negocio | None] = mapped_column(lista_cerrada(Negocio, "negocio"))

    @property
    def es_administrador(self) -> bool:
        return self.rol is Rol.ADMINISTRADOR

    def entra_a(self, negocio: Negocio) -> bool:
        return self.negocio is None or self.negocio is negocio
