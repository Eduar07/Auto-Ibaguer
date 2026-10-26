"""Tabla de sesiones del bot."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SesionBot(Base):
    """En qué paso de la conversación va cada número de WhatsApp.

    Si el cliente responde horas después, el bot sabe dónde iba. Se identifica por el
    teléfono y no por el cliente, porque la conversación empieza antes de que la persona
    acepte el aviso de datos (y sin aceptarlo no se crea el cliente).
    """

    __tablename__ = "sesion_bot"
    __table_args__ = (CheckConstraint("telefono ~ '^[0-9]{8,15}$'", name="telefono_solo_digitos"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    telefono: Mapped[str] = mapped_column(String(15), unique=True)
    # Vacío mientras la persona no ha aceptado el aviso de datos. Un cliente tiene una sola
    # sesión: si dos números quedaran apuntando al mismo cliente, la base lo rechaza.
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"), unique=True)
    paso_actual: Mapped[str] = mapped_column(String(40))
    # Lo que el cliente va respondiendo antes de confirmar (vehículo, placa, servicio...).
    datos_temporales: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Pasados 30 minutos sin respuesta, la sesión vence y el bot vuelve al menú.
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
