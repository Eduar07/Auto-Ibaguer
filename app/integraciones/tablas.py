"""Bandejas de entrada y de salida de los mensajes de WhatsApp.

Funcionan como las bandejas de una oficina: todo mensaje queda anotado antes de
atenderlo o de enviarlo. Si el servidor se cae a mitad de camino, nada se pierde
ni se procesa dos veces.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, lista_cerrada


class MensajeEntrante(Base):
    """Cada mensaje que llega de WhatsApp, guardado antes de procesarlo."""

    __tablename__ = "mensaje_entrante"
    __table_args__ = (
        CheckConstraint("telefono ~ '^[0-9]{8,15}$'", name="telefono_solo_digitos"),
        # Los que faltan por procesar se buscan rápido.
        Index(
            "ix_mensaje_entrante_por_procesar",
            "recibido_en",
            postgresql_where=text("procesado_en IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Id que pone Meta. Es único: si Meta reenvía el mismo mensaje, el repetido se rechaza.
    id_meta: Mapped[str] = mapped_column(String(128), unique=True)
    telefono: Mapped[str] = mapped_column(String(15))
    tipo: Mapped[str] = mapped_column(String(30))
    contenido: Mapped[dict[str, Any]] = mapped_column(JSONB)
    recibido_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    procesado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class EstadoMensaje(StrEnum):
    PENDIENTE = "pendiente"
    ENVIADO = "enviado"
    FALLIDO = "fallido"


class MensajeSaliente(Base):
    """Cada mensaje que el bot debe enviar.

    Se guarda en la misma operación que el cambio que lo produjo (por ejemplo, "llamado"):
    si el cambio quedó guardado, el aviso sale; si WhatsApp falla, se reintenta.
    """

    __tablename__ = "mensaje_saliente"
    __table_args__ = (
        CheckConstraint("telefono ~ '^[0-9]{8,15}$'", name="telefono_solo_digitos"),
        CheckConstraint("intentos >= 0", name="intentos_no_negativos"),
        Index(
            "ix_mensaje_saliente_por_enviar",
            "proximo_intento_en",
            postgresql_where=text("estado = 'pendiente'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    telefono: Mapped[str] = mapped_column(String(15))
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"))
    turno_id: Mapped[int | None] = mapped_column(ForeignKey("turno.id"), index=True)
    # Qué aviso es: llamado, vehiculo_listo, aviso_anticipado... (app.turnos.estados.TipoAviso)
    tipo: Mapped[str] = mapped_column(String(40))
    # Los datos del aviso (número de turno, hora límite...). El texto final se arma con los
    # textos del bot al momento de enviarlo.
    contenido: Mapped[dict[str, Any]] = mapped_column(JSONB)
    estado: Mapped[EstadoMensaje] = mapped_column(
        lista_cerrada(EstadoMensaje, "estado"),
        default=EstadoMensaje.PENDIENTE,
        server_default=EstadoMensaje.PENDIENTE.value,
    )
    intentos: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    # Id que devuelve Meta cuando acepta el mensaje.
    id_meta: Mapped[str | None] = mapped_column(String(128))
    ultimo_error: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    proximo_intento_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
