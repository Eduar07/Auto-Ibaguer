"""Pedidos especiales y aviso al dueño.

- comentario.tipo acepta "pedido_especial": lo que decide el dueño y no el bot (una flota,
  un convenio, un precio especial). El bot se lo pasa en vez de negociar solo.
- comentario.resumen: qué pide, en una línea (lo escribe Gemini), para leerlo de un
  vistazo en Comentarios y en el correo.
- comentario.avisado_en: cuándo salió el correo que le avisa al dueño de una queja, un
  pedido de hablar con alguien o un pedido especial.

Revisión: 0016
Anterior: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIPOS_ANTES = "tipo IN ('queja', 'felicitacion', 'quiere_hablar', 'pregunta')"
TIPOS_AHORA = "tipo IN ('queja', 'felicitacion', 'quiere_hablar', 'pregunta', 'pedido_especial')"


def upgrade() -> None:
    op.drop_constraint("tipo", "comentario", type_="check")
    op.create_check_constraint("tipo", "comentario", TIPOS_AHORA)
    op.add_column("comentario", sa.Column("resumen", sa.String(length=200), nullable=True))
    op.add_column("comentario", sa.Column("avisado_en", sa.DateTime(timezone=True), nullable=True))
    # Lo que ya estaba no se avisa: el dueño ya lo vio en Comentarios.
    op.execute("UPDATE comentario SET avisado_en = creado_en")


def downgrade() -> None:
    # La versión anterior no conoce "pedido_especial": quedan como pedidos de hablar con
    # alguien, que es lo que son (alguien espera que el dueño le responda).
    op.execute("UPDATE comentario SET tipo = 'quiere_hablar' WHERE tipo = 'pedido_especial'")
    op.drop_column("comentario", "avisado_en")
    op.drop_column("comentario", "resumen")
    op.drop_constraint("tipo", "comentario", type_="check")
    op.create_check_constraint("tipo", "comentario", TIPOS_ANTES)
