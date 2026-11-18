"""Tablas del bot: en qué va cada conversación y con quién está en pausa."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.clientes.reglas import CONTACTO_VALIDO, LARGO_DEL_CONTACTO
from app.db.base import Base


class SesionBot(Base):
    """En qué paso de la conversación va cada número de WhatsApp.

    Si el cliente responde horas después, el bot sabe dónde iba. Se identifica por el
    teléfono y no por el cliente, porque la conversación empieza antes de que la persona
    acepte el aviso de datos (y sin aceptarlo no se crea el cliente).
    """

    __tablename__ = "sesion_bot"
    __table_args__ = (CheckConstraint(CONTACTO_VALIDO, name="telefono_o_usuario"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    telefono: Mapped[str] = mapped_column(String(LARGO_DEL_CONTACTO), unique=True)
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


class PausaDelBot(Base):
    """Mientras una persona del lavadero le responde a un cliente, el bot no le contesta.

    Se crea (o se alarga) cada vez que el administrador le escribe desde Comentarios, y
    vence sola (app.bot.pausa). Los botones, los avisos de la fila y los mensajes graves
    siguen funcionando igual.
    """

    __tablename__ = "pausa_del_bot"
    __table_args__ = (CheckConstraint(CONTACTO_VALIDO, name="telefono_o_usuario"),)

    telefono: Mapped[str] = mapped_column(String(LARGO_DEL_CONTACTO), primary_key=True)
    hasta: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # El comentario que se está respondiendo: si el cliente escribe, vuelve a quedar por
    # atender. Si alguien lo borra, la pausa sigue sin él.
    comentario_id: Mapped[int | None] = mapped_column(
        ForeignKey("comentario.id", ondelete="SET NULL")
    )
    administrador_id: Mapped[int] = mapped_column(ForeignKey("administrador.id"))
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Seguimiento(Base):
    """Volver a escribirle a quien dijo "lo pienso" y aceptó que le escribiéramos.

    Solo con su sí ("Sí, escríbeme"), una sola vez y dentro de las 24 horas en que WhatsApp
    deja escribir sin plantilla (app.bot.seguimiento). Se borra al mandarlo, o antes si
    vuelve a escribir o pide turno: el celular no se guarda más de lo necesario.
    """

    __tablename__ = "seguimiento"
    __table_args__ = (CheckConstraint(CONTACTO_VALIDO, name="telefono_o_usuario"),)

    telefono: Mapped[str] = mapped_column(String(LARGO_DEL_CONTACTO), primary_key=True)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id", ondelete="CASCADE"))
    # La fila que estaba mirando (los precios de moto...), para contarle cómo va esa.
    tipo_vehiculo_id: Mapped[int | None] = mapped_column(
        ForeignKey("tipo_vehiculo.id", ondelete="SET NULL")
    )
    enviar_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
