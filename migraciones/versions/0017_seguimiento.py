"""El seguimiento de "lo pienso" (pieza 4 del checklist del bot).

- Tabla seguimiento: a quién volverle a escribir y cuándo, siempre dentro de las 24 horas
  en que WhatsApp deja escribir sin plantilla. Solo con su sí; se borra al mandarlo o si
  vuelve a escribir antes.

Revisión: 0017
Anterior: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seguimiento",
        sa.Column("telefono", sa.String(length=15), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=True),
        sa.Column("tipo_vehiculo_id", sa.Integer(), nullable=True),
        sa.Column("enviar_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "telefono ~ '^[0-9]{8,15}$'", name=op.f("ck_seguimiento_telefono_solo_digitos")
        ),
        sa.ForeignKeyConstraint(
            ["cliente_id"],
            ["cliente.id"],
            name=op.f("fk_seguimiento_cliente_id_cliente"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tipo_vehiculo_id"],
            ["tipo_vehiculo.id"],
            name=op.f("fk_seguimiento_tipo_vehiculo_id_tipo_vehiculo"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("telefono", name=op.f("pk_seguimiento")),
    )
    op.create_index(op.f("ix_seguimiento_enviar_en"), "seguimiento", ["enviar_en"])


def downgrade() -> None:
    op.drop_index(op.f("ix_seguimiento_enviar_en"), table_name="seguimiento")
    op.drop_table("seguimiento")
