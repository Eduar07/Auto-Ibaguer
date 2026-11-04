"""La tabla de comentarios (hito 7b)."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, lista_cerrada

# Lo más largo que se guarda de lo que escribió el cliente (la queja y lo que contó después).
LARGO_MAXIMO = 2000


class TipoComentario(StrEnum):
    QUEJA = "queja"
    FELICITACION = "felicitacion"
    QUIERE_HABLAR = "quiere_hablar"  # pidió hablar con una persona


class Comentario(Base):
    """Una queja, una felicitación o un cliente que quiere hablar con una persona.

    Se guarda el celular solo para que el administrador pueda responderle: el de un cliente
    que ya aceptó el aviso de datos, o el de alguien que dio permiso para eso (permiso_en).
    Sin ninguno de los dos, el comentario queda sin celular.
    """

    __tablename__ = "comentario"
    __table_args__ = (
        CheckConstraint(f"char_length(texto) <= {LARGO_MAXIMO}", name="texto_no_muy_largo"),
        CheckConstraint(
            "telefono IS NULL OR cliente_id IS NOT NULL OR permiso_en IS NOT NULL",
            name="telefono_con_permiso",
        ),
        CheckConstraint(
            "atendido_en IS NOT NULL OR atendido_por IS NULL", name="atendido_con_fecha"
        ),
        # La pantalla Comentarios y el número del menú cuentan los que faltan por atender.
        Index(
            "ix_comentario_por_atender",
            "creado_en",
            postgresql_where=text("atendido_en IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    tipo: Mapped[TipoComentario] = mapped_column(lista_cerrada(TipoComentario, "tipo"))
    texto: Mapped[str] = mapped_column(Text)
    cliente_id: Mapped[int | None] = mapped_column(ForeignKey("cliente.id"), index=True)
    telefono: Mapped[str | None] = mapped_column(String(15))
    permiso_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # El último turno del cliente cuando escribió: casi siempre, de eso habla.
    turno_id: Mapped[int | None] = mapped_column(ForeignKey("turno.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atendido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    atendido_por: Mapped[int | None] = mapped_column(ForeignKey("administrador.id"))
