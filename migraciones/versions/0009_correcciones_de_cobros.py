"""Las correcciones de cobros del parqueadero.

- correccion_cobro: cada cobro equivocado que se corrigió mientras su caja estaba abierta
  (otro valor u otro medio de pago, una salida deshecha, un pago de mensualidad anulado), con
  cómo estaba, cómo quedó, el motivo y quién lo hizo. Solo se agregan filas.

Revisión: 0009
Anterior: 0008
Creada: 2026-11-13 08:05:35.235880-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "correccion_cobro",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "corregido",
                "salida_deshecha",
                "pago_anulado",
                name="tipo_correccion",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("caja_id", sa.Integer(), nullable=False),
        sa.Column("parqueo_id", sa.BigInteger(), nullable=True),
        sa.Column("mensualidad_id", sa.Integer(), nullable=True),
        sa.Column("pago_id", sa.BigInteger(), nullable=True),
        sa.Column("placa", sa.String(length=7), nullable=False),
        sa.Column("valor_antes", sa.Integer(), nullable=False),
        sa.Column("valor_despues", sa.Integer(), nullable=False),
        sa.Column(
            "medio_antes",
            sa.Enum(
                "efectivo",
                "nequi",
                "daviplata",
                "tarjeta",
                name="medio_antes",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column(
            "medio_despues",
            sa.Enum(
                "efectivo",
                "nequi",
                "daviplata",
                "tarjeta",
                name="medio_despues",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("detalle", sa.Text(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("administrador_id", sa.Integer(), nullable=True),
        sa.Column(
            "fecha_hora",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(motivo) <> ''", name=op.f("ck_correccion_cobro_con_motivo")),
        sa.CheckConstraint(
            "placa ~ '^[A-Z0-9]{4,7}$'", name=op.f("ck_correccion_cobro_placa_limpia")
        ),
        sa.CheckConstraint(
            "(parqueo_id IS NULL) <> (mensualidad_id IS NULL)",
            name=op.f("ck_correccion_cobro_de_un_cobro"),
        ),
        sa.CheckConstraint(
            "valor_antes >= 0 AND valor_despues >= 0",
            name=op.f("ck_correccion_cobro_valores_no_negativos"),
        ),
        sa.ForeignKeyConstraint(
            ["administrador_id"],
            ["administrador.id"],
            name=op.f("fk_correccion_cobro_administrador_id_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["caja_id"],
            ["caja_parqueadero.id"],
            name=op.f("fk_correccion_cobro_caja_id_caja_parqueadero"),
        ),
        sa.ForeignKeyConstraint(
            ["mensualidad_id"],
            ["mensualidad.id"],
            name=op.f("fk_correccion_cobro_mensualidad_id_mensualidad"),
        ),
        sa.ForeignKeyConstraint(
            ["parqueo_id"], ["parqueo.id"], name=op.f("fk_correccion_cobro_parqueo_id_parqueo")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_correccion_cobro")),
    )
    op.create_index(
        op.f("ix_correccion_cobro_caja_id"), "correccion_cobro", ["caja_id"], unique=False
    )
    op.create_index(
        op.f("ix_correccion_cobro_fecha_hora"), "correccion_cobro", ["fecha_hora"], unique=False
    )
    op.create_index(
        op.f("ix_correccion_cobro_parqueo_id"), "correccion_cobro", ["parqueo_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_correccion_cobro_parqueo_id"), table_name="correccion_cobro")
    op.drop_index(op.f("ix_correccion_cobro_fecha_hora"), table_name="correccion_cobro")
    op.drop_index(op.f("ix_correccion_cobro_caja_id"), table_name="correccion_cobro")
    op.drop_table("correccion_cobro")
