"""El resultado y el cierre del mes: los gastos que no salen del cajón y los meses entregados
al contador.

- gasto_fuera_de_caja: un gasto pagado por fuera del cajón (transferencia, Nequi), de un
  negocio, con su fecha, categoría y valor; se anula, no se borra.
- cierre_de_mes: el mes de un negocio entregado al contador, con las cifras tal como se
  entregaron. Desde entonces ese mes no se cambia.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIAS = (
    "insumos",
    "aseo",
    "servicios",
    "nomina",
    "mantenimiento",
    "arriendo",
    "vigilancia",
    "transporte",
    "otro",
)


def _negocio() -> sa.Enum:
    return sa.Enum(
        "lavadero",
        "parqueadero",
        name="negocio",
        native_enum=False,
        create_constraint=True,
        length=20,
    )


def upgrade() -> None:
    op.create_table(
        "gasto_fuera_de_caja",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("negocio", _negocio(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column(
            "categoria",
            sa.Enum(
                *CATEGORIAS,
                name="categoria_de_gasto",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("valor", sa.Integer(), nullable=False),
        sa.Column(
            "registrado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("registrado_por", sa.Integer(), nullable=True),
        sa.Column("anulado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anulado_por", sa.Integer(), nullable=True),
        sa.Column("motivo_anulacion", sa.Text(), nullable=True),
        sa.CheckConstraint("valor > 0", name=op.f("ck_gasto_fuera_de_caja_valor_positivo")),
        sa.CheckConstraint(
            "btrim(descripcion) <> ''", name=op.f("ck_gasto_fuera_de_caja_con_descripcion")
        ),
        sa.CheckConstraint(
            "(anulado_en IS NULL) = (motivo_anulacion IS NULL)",
            name=op.f("ck_gasto_fuera_de_caja_anulado_con_motivo"),
        ),
        sa.CheckConstraint(
            "motivo_anulacion IS NULL OR btrim(motivo_anulacion) <> ''",
            name=op.f("ck_gasto_fuera_de_caja_motivo_no_vacio"),
        ),
        sa.ForeignKeyConstraint(
            ["registrado_por"],
            ["administrador.id"],
            name=op.f("fk_gasto_fuera_de_caja_registrado_por_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["anulado_por"],
            ["administrador.id"],
            name=op.f("fk_gasto_fuera_de_caja_anulado_por_administrador"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gasto_fuera_de_caja")),
    )
    op.create_index(
        op.f("ix_gasto_fuera_de_caja_fecha"), "gasto_fuera_de_caja", ["fecha"], unique=False
    )
    op.create_table(
        "cierre_de_mes",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("negocio", _negocio(), nullable=False),
        sa.Column("mes", sa.Date(), nullable=False),
        sa.Column(
            "cerrado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cerrado_por", sa.Integer(), nullable=True),
        sa.Column("cifras", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "extract(day FROM mes) = 1", name=op.f("ck_cierre_de_mes_primer_dia_del_mes")
        ),
        sa.ForeignKeyConstraint(
            ["cerrado_por"],
            ["administrador.id"],
            name=op.f("fk_cierre_de_mes_cerrado_por_administrador"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cierre_de_mes")),
    )
    op.create_index(
        "uq_cierre_de_mes_negocio_mes", "cierre_de_mes", ["negocio", "mes"], unique=True
    )


def downgrade() -> None:
    hay = op.get_bind().execute(
        sa.text(
            "SELECT (SELECT count(*) FROM gasto_fuera_de_caja)"
            " + (SELECT count(*) FROM cierre_de_mes)"
        )
    )
    if hay.scalar_one():
        raise RuntimeError(
            "Hay gastos por fuera de la caja o meses cerrados: bajar de versión los borraría."
            " Si de verdad hay que bajar, sácale copia a la base primero."
        )
    op.drop_index("uq_cierre_de_mes_negocio_mes", table_name="cierre_de_mes")
    op.drop_table("cierre_de_mes")
    op.drop_index(op.f("ix_gasto_fuera_de_caja_fecha"), table_name="gasto_fuera_de_caja")
    op.drop_table("gasto_fuera_de_caja")
