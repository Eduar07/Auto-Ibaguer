"""Tabla de administradores del dashboard."""

from datetime import datetime

from sqlalchemy import DateTime, Identity, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Administrador(Base):
    """Quién puede entrar al dashboard.

    La contraseña nunca se guarda tal cual: se guarda cifrada con Argon2, de forma que
    ni leyendo la base de datos se puede saber cuál es.
    """

    __tablename__ = "administrador"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    usuario: Mapped[str] = mapped_column(String(40), unique=True)
    clave_cifrada: Mapped[str] = mapped_column(String(255))
    activo: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
