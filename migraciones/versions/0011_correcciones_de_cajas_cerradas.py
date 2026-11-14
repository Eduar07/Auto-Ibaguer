"""Corregir una caja ya cerrada (hasta 30 días después) y su base o su conteo.

- correccion_cobro.despues_del_cierre: la corrección se hizo con la caja ya cerrada (cambió
  su cuadre después de contado). Las de antes de esta versión fueron todas con la caja
  abierta: quedan en false.
- correccion_de_caja: la base o el efectivo contado que se corrigieron, con cómo estaban, el
  motivo y quién. Solo se agregan filas.

Revisión: 0011
Anterior: 0010
Creada: 2026-11-13 20:27:03.612561-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "correccion_de_caja",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "dato",
            sa.Enum(
                "base", "conteo", name="dato", native_enum=False, create_constraint=True, length=20
            ),
            nullable=False,
        ),
        sa.Column("caja_id", sa.Integer(), nullable=False),
        sa.Column(
            "despues_del_cierre", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("valor_antes", sa.Integer(), nullable=False),
        sa.Column("valor_despues", sa.Integer(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("administrador_id", sa.Integer(), nullable=True),
        sa.Column(
            "fecha_hora",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(motivo) <> ''", name=op.f("ck_correccion_de_caja_con_motivo")),
        sa.CheckConstraint(
            "dato <> 'conteo' OR despues_del_cierre",
            name=op.f("ck_correccion_de_caja_conteo_ya_cerrada"),
        ),
        sa.CheckConstraint(
            "valor_antes <> valor_despues", name=op.f("ck_correccion_de_caja_algo_cambio")
        ),
        sa.CheckConstraint(
            "valor_antes >= 0 AND valor_despues >= 0",
            name=op.f("ck_correccion_de_caja_valores_no_negativos"),
        ),
        sa.ForeignKeyConstraint(
            ["administrador_id"],
            ["administrador.id"],
            name=op.f("fk_correccion_de_caja_administrador_id_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["caja_id"],
            ["caja_parqueadero.id"],
            name=op.f("fk_correccion_de_caja_caja_id_caja_parqueadero"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_correccion_de_caja")),
    )
    op.create_index(
        op.f("ix_correccion_de_caja_caja_id"), "correccion_de_caja", ["caja_id"], unique=False
    )
    op.create_index(
        op.f("ix_correccion_de_caja_fecha_hora"), "correccion_de_caja", ["fecha_hora"], unique=False
    )
    op.add_column(
        "correccion_cobro",
        sa.Column(
            "despues_del_cierre", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("correccion_cobro", "despues_del_cierre")
    op.drop_index(op.f("ix_correccion_de_caja_fecha_hora"), table_name="correccion_de_caja")
    op.drop_index(op.f("ix_correccion_de_caja_caja_id"), table_name="correccion_de_caja")
    op.drop_table("correccion_de_caja")
