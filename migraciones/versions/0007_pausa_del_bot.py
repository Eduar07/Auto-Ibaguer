"""El bot en pausa mientras una persona le responde a un cliente (hito 7b, parte 3).

- Tabla pausa_del_bot: con qué celular el bot no contesta lo que escriban, hasta qué hora
  y por cuál comentario. La crea el administrador al responder desde Comentarios; vence
  sola y el reloj la borra.

Revisión: 0007
Anterior: 0006
Creada: 2026-11-04 06:07:36.038407-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pausa_del_bot",
        sa.Column("telefono", sa.String(length=15), nullable=False),
        sa.Column("hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("comentario_id", sa.BigInteger(), nullable=True),
        sa.Column("administrador_id", sa.Integer(), nullable=False),
        sa.Column(
            "creada_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "telefono ~ '^[0-9]{8,15}$'", name=op.f("ck_pausa_del_bot_telefono_solo_digitos")
        ),
        sa.ForeignKeyConstraint(
            ["administrador_id"],
            ["administrador.id"],
            name=op.f("fk_pausa_del_bot_administrador_id_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["comentario_id"],
            ["comentario.id"],
            name=op.f("fk_pausa_del_bot_comentario_id_comentario"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("telefono", name=op.f("pk_pausa_del_bot")),
    )


def downgrade() -> None:
    op.drop_table("pausa_del_bot")
