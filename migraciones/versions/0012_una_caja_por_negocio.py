"""Una caja por negocio: la del parqueadero y, desde ahora, la del lavadero.

- caja_parqueadero pasa a llamarse caja, con sus reglas, sus llaves y su secuencia, y gana la
  columna negocio. Las cajas que ya había son todas del parqueadero: nada de lo registrado
  cambia, y las salidas, mensualidades y correcciones siguen apuntando a la misma caja.
- Una caja abierta por negocio (antes, una sola en todo el sistema).
- correccion_cobro.turno_id: la corrección del cobro de un turno del lavadero. Cada corrección
  es de un solo cobro: una salida, un pago de mensualidad o un turno.
- correccion_cobro.tipo acepta cobro_deshecho (lavadero: el turno vuelve a quedar por cobrar).
- pago.caja_id: la caja del lavadero a la que entró el cobro de un turno. Los cobros de antes
  de esta versión no tienen caja.

Revisión: 0012
Anterior: 0011
Creada: 2026-11-14 02:10:00-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGLAS_DE_LA_CAJA = (
    "abierta_sin_cierre",
    "base_no_negativa",
    "cerrada_con_conteo",
    "conteo_no_negativo",
)
LLAVES_DE_LA_CAJA = ("abierta_por", "cerrada_por")
APUNTAN_A_LA_CAJA = ("parqueo", "pago_mensualidad", "correccion_cobro", "correccion_de_caja")
TIPOS_DE_ANTES = ("corregido", "salida_deshecha", "pago_anulado")
TIPOS = (*TIPOS_DE_ANTES, "cobro_deshecho")


def _renombrar_la_caja(de: str, a: str) -> None:
    op.rename_table(de, a)
    op.execute(f"ALTER SEQUENCE {de}_id_seq RENAME TO {a}_id_seq")
    op.execute(f"ALTER TABLE {a} RENAME CONSTRAINT pk_{de} TO pk_{a}")
    for regla in REGLAS_DE_LA_CAJA:
        op.execute(f"ALTER TABLE {a} RENAME CONSTRAINT ck_{de}_{regla} TO ck_{a}_{regla}")
    for columna in LLAVES_DE_LA_CAJA:
        op.execute(
            f"ALTER TABLE {a} RENAME CONSTRAINT "
            f"fk_{de}_{columna}_administrador TO fk_{a}_{columna}_administrador"
        )
    for tabla in APUNTAN_A_LA_CAJA:
        op.execute(
            f"ALTER TABLE {tabla} RENAME CONSTRAINT fk_{tabla}_caja_id_{de} TO fk_{tabla}_caja_id_{a}"
        )


def _tipos_de_correccion(tipos: Sequence[str]) -> None:
    op.drop_constraint(op.f("ck_correccion_cobro_tipo_correccion"), "correccion_cobro")
    op.create_check_constraint(
        op.f("ck_correccion_cobro_tipo_correccion"),
        "correccion_cobro",
        "tipo IN (" + ", ".join(f"'{tipo}'" for tipo in tipos) + ")",
    )


def upgrade() -> None:
    # 1. La caja del parqueadero pasa a ser la caja de cualquier negocio.
    _renombrar_la_caja("caja_parqueadero", "caja")
    op.add_column(
        "caja",
        sa.Column("negocio", sa.String(20), nullable=False, server_default="parqueadero"),
    )
    op.alter_column("caja", "negocio", server_default=None)
    op.create_check_constraint(
        op.f("ck_caja_negocio"), "caja", "negocio IN ('lavadero', 'parqueadero')"
    )
    op.drop_index(
        "uq_caja_parqueadero_una_abierta", table_name="caja", postgresql_where=sa.text("abierta")
    )
    op.create_index(
        "uq_caja_una_abierta_por_negocio",
        "caja",
        ["negocio"],
        unique=True,
        postgresql_where=sa.text("abierta"),
    )

    # 2. Las correcciones de cobros también son del lavadero (el cobro de un turno).
    op.add_column("correccion_cobro", sa.Column("turno_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_correccion_cobro_turno_id_turno"),
        "correccion_cobro",
        "turno",
        ["turno_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_correccion_cobro_turno_id"), "correccion_cobro", ["turno_id"], unique=False
    )
    op.drop_constraint(op.f("ck_correccion_cobro_de_un_cobro"), "correccion_cobro")
    op.create_check_constraint(
        op.f("ck_correccion_cobro_de_un_cobro"),
        "correccion_cobro",
        "num_nonnulls(parqueo_id, mensualidad_id, turno_id) = 1",
    )
    _tipos_de_correccion(TIPOS)

    # 3. El cobro de un turno entra a la caja del lavadero.
    op.add_column("pago", sa.Column("caja_id", sa.Integer(), nullable=True))
    op.create_foreign_key(op.f("fk_pago_caja_id_caja"), "pago", "caja", ["caja_id"], ["id"])
    op.create_index(op.f("ix_pago_caja_id"), "pago", ["caja_id"], unique=False)


def downgrade() -> None:
    # Volver atrás solo se puede si el lavadero todavía no usó su caja: esas cajas, sus cobros
    # y sus correcciones no tendrían dónde quedar.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM caja WHERE negocio <> 'parqueadero')
               OR EXISTS (SELECT 1 FROM correccion_cobro WHERE turno_id IS NOT NULL) THEN
                RAISE EXCEPTION 'El lavadero ya tiene cajas: no se puede volver a la versión 0011.';
            END IF;
        END $$;
        """
    )
    op.drop_index(op.f("ix_pago_caja_id"), table_name="pago")
    op.drop_constraint(op.f("fk_pago_caja_id_caja"), "pago", type_="foreignkey")
    op.drop_column("pago", "caja_id")

    _tipos_de_correccion(TIPOS_DE_ANTES)
    op.drop_constraint(op.f("ck_correccion_cobro_de_un_cobro"), "correccion_cobro")
    op.create_check_constraint(
        op.f("ck_correccion_cobro_de_un_cobro"),
        "correccion_cobro",
        "(parqueo_id IS NULL) <> (mensualidad_id IS NULL)",
    )
    op.drop_index(op.f("ix_correccion_cobro_turno_id"), table_name="correccion_cobro")
    op.drop_constraint(
        op.f("fk_correccion_cobro_turno_id_turno"), "correccion_cobro", type_="foreignkey"
    )
    op.drop_column("correccion_cobro", "turno_id")

    op.drop_index(
        "uq_caja_una_abierta_por_negocio", table_name="caja", postgresql_where=sa.text("abierta")
    )
    op.create_index(
        "uq_caja_parqueadero_una_abierta",
        "caja",
        ["abierta"],
        unique=True,
        postgresql_where=sa.text("abierta"),
    )
    op.drop_constraint(op.f("ck_caja_negocio"), "caja")
    op.drop_column("caja", "negocio")
    _renombrar_la_caja("caja", "caja_parqueadero")
