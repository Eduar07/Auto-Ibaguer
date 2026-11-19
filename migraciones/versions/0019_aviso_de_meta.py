"""El diagnóstico de WhatsApp: cada aviso que Meta manda al webhook y qué se hizo con él.

- Tabla aviso_de_meta: hora, si la firma era de Meta, para qué número era, cuántos
  mensajes y estados traía y, de cada uno, el celular a medias, el tipo, los nombres de
  los campos y por qué se descartó. Nada de lo que escribió el cliente.

Revisión: 0019
Anterior: 0018
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "aviso_de_meta",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column(
            "recibido_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("firma_valida", sa.Boolean(), nullable=False),
        sa.Column("para_el_numero", sa.String(length=40), nullable=True),
        sa.Column("mensajes", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("estados", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("detalle", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("problema", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_aviso_de_meta")),
    )
    op.create_index(op.f("ix_aviso_de_meta_recibido_en"), "aviso_de_meta", ["recibido_en"])


def downgrade() -> None:
    op.drop_index(op.f("ix_aviso_de_meta_recibido_en"), table_name="aviso_de_meta")
    op.drop_table("aviso_de_meta")
