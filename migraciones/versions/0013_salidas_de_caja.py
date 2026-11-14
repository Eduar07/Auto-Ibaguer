"""Salidas de caja: la plata que sale del cajón (gastos, retiros y consignaciones).

- salida_de_caja: cada salida, de qué caja, de qué tipo (y en qué se gastó, si es un gasto),
  cuánto, por qué y quién la registró; si se anuló, cuándo, quién y por qué. No se borran.
- correccion_de_caja: ahora también anota una salida anulada (dato "salida"), con su detalle.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
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


def _datos_de_caja(datos: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_correccion_de_caja_dato"), "correccion_de_caja")
    op.create_check_constraint(
        op.f("ck_correccion_de_caja_dato"),
        "correccion_de_caja",
        "dato IN (" + ", ".join(f"'{dato}'" for dato in datos) + ")",
    )


def upgrade() -> None:
    op.create_table(
        "salida_de_caja",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("caja_id", sa.Integer(), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "gasto",
                "retiro",
                "consignacion",
                name="tipo_de_salida",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "categoria",
            sa.Enum(
                *CATEGORIAS,
                name="categoria_de_gasto",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("valor", sa.Integer(), nullable=False),
        sa.Column(
            "registrada_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("registrada_por", sa.Integer(), nullable=True),
        sa.Column("anulada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anulada_por", sa.Integer(), nullable=True),
        sa.Column("motivo_anulacion", sa.Text(), nullable=True),
        sa.CheckConstraint("valor > 0", name=op.f("ck_salida_de_caja_valor_positivo")),
        sa.CheckConstraint(
            "btrim(descripcion) <> ''", name=op.f("ck_salida_de_caja_con_descripcion")
        ),
        sa.CheckConstraint(
            "(tipo = 'gasto') = (categoria IS NOT NULL)",
            name=op.f("ck_salida_de_caja_categoria_del_gasto"),
        ),
        sa.CheckConstraint(
            "(anulada_en IS NULL) = (motivo_anulacion IS NULL)",
            name=op.f("ck_salida_de_caja_anulada_con_motivo"),
        ),
        sa.CheckConstraint(
            "motivo_anulacion IS NULL OR btrim(motivo_anulacion) <> ''",
            name=op.f("ck_salida_de_caja_motivo_no_vacio"),
        ),
        sa.ForeignKeyConstraint(
            ["caja_id"], ["caja.id"], name=op.f("fk_salida_de_caja_caja_id_caja")
        ),
        sa.ForeignKeyConstraint(
            ["registrada_por"],
            ["administrador.id"],
            name=op.f("fk_salida_de_caja_registrada_por_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["anulada_por"],
            ["administrador.id"],
            name=op.f("fk_salida_de_caja_anulada_por_administrador"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_salida_de_caja")),
    )
    op.create_index(op.f("ix_salida_de_caja_caja_id"), "salida_de_caja", ["caja_id"], unique=False)
    op.create_index(
        op.f("ix_salida_de_caja_registrada_en"), "salida_de_caja", ["registrada_en"], unique=False
    )
    op.add_column("correccion_de_caja", sa.Column("detalle", sa.Text(), nullable=True))
    _datos_de_caja(("base", "conteo", "salida"))


def downgrade() -> None:
    hay = op.get_bind().execute(
        sa.text(
            "SELECT (SELECT count(*) FROM salida_de_caja)"
            " + (SELECT count(*) FROM correccion_de_caja WHERE dato = 'salida')"
        )
    )
    if hay.scalar_one():
        raise RuntimeError(
            "Hay salidas de caja registradas: bajar de versión las borraría y las cajas dejarían"
            " de cuadrar. Si de verdad hay que bajar, sácale copia a la base primero."
        )
    _datos_de_caja(("base", "conteo"))
    op.drop_column("correccion_de_caja", "detalle")
    op.drop_index(op.f("ix_salida_de_caja_registrada_en"), table_name="salida_de_caja")
    op.drop_index(op.f("ix_salida_de_caja_caja_id"), table_name="salida_de_caja")
    op.drop_table("salida_de_caja")
