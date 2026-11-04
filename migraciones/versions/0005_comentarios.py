"""Comentarios de los clientes (hito 7b).

- Tabla comentario: las quejas, las felicitaciones y quién pidió hablar con una persona,
  como llegan por WhatsApp. El administrador los ve y los marca como atendidos en el
  dashboard.
- Regla: el celular se guarda solo de un cliente que aceptó el aviso de datos o de
  alguien que dio permiso para que le escriban (permiso_en).

Revisión: 0005
Anterior: 0004
Creada: 2026-11-04 01:48:10-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comentario",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "queja",
                "felicitacion",
                "quiere_hablar",
                name="tipo",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=True),
        sa.Column("telefono", sa.String(length=15), nullable=True),
        sa.Column("permiso_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("turno_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "creado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("atendido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("atendido_por", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "atendido_en IS NOT NULL OR atendido_por IS NULL",
            name=op.f("ck_comentario_atendido_con_fecha"),
        ),
        sa.CheckConstraint(
            "char_length(texto) <= 2000", name=op.f("ck_comentario_texto_no_muy_largo")
        ),
        sa.CheckConstraint(
            "telefono IS NULL OR cliente_id IS NOT NULL OR permiso_en IS NOT NULL",
            name=op.f("ck_comentario_telefono_con_permiso"),
        ),
        sa.ForeignKeyConstraint(
            ["atendido_por"],
            ["administrador.id"],
            name=op.f("fk_comentario_atendido_por_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_comentario_cliente_id_cliente")
        ),
        sa.ForeignKeyConstraint(
            ["turno_id"], ["turno.id"], name=op.f("fk_comentario_turno_id_turno")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comentario")),
    )
    op.create_index(op.f("ix_comentario_cliente_id"), "comentario", ["cliente_id"], unique=False)
    op.create_index(
        "ix_comentario_por_atender",
        "comentario",
        ["creado_en"],
        unique=False,
        postgresql_where=sa.text("atendido_en IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_comentario_por_atender",
        table_name="comentario",
        postgresql_where=sa.text("atendido_en IS NULL"),
    )
    op.drop_index(op.f("ix_comentario_cliente_id"), table_name="comentario")
    op.drop_table("comentario")
