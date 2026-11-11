"""El parqueadero: entradas y salidas por placa, mensualidades y su propia caja.

- parqueo: cada entrada; el id es el número del ticket. Guarda la tarifa con que entró y,
  al salir, el tiempo, lo que dio la tarifa, lo que se cobró y en qué caja.
- mensualidad y pago_mensualidad: los vehículos que pagan por mes y qué días cubre cada pago
  (sin caja y en 0, los días que ya traía pagados al crearla, por ejemplo con el dueño anterior).
- caja_parqueadero: los turnos de caja del parqueadero, aparte de la plata del lavadero.

Revisión: 0008
Anterior: 0007
Creada: 2026-11-10 21:32:49.180992-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mensualidad",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("placa", sa.String(length=7), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "carro", "moto", name="tipo", native_enum=False, create_constraint=True, length=20
            ),
            nullable=False,
        ),
        sa.Column("titular", sa.String(length=80), nullable=False),
        sa.Column("telefono", sa.String(length=15), nullable=True),
        sa.Column("valor_mensual", sa.Integer(), nullable=False),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("activa", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("autorizo_datos_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "creada_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "actualizada_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("placa ~ '^[A-Z0-9]{4,7}$'", name=op.f("ck_mensualidad_placa_limpia")),
        sa.CheckConstraint(
            "telefono IS NULL OR telefono ~ '^[0-9]{8,15}$'",
            name=op.f("ck_mensualidad_telefono_digitos"),
        ),
        sa.CheckConstraint("valor_mensual > 0", name=op.f("ck_mensualidad_valor_positivo")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mensualidad")),
    )
    op.create_index(
        "uq_mensualidad_placa_activa",
        "mensualidad",
        ["placa"],
        unique=True,
        postgresql_where=sa.text("activa"),
    )
    op.create_table(
        "caja_parqueadero",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("abierta", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("base", sa.Integer(), nullable=False),
        sa.Column(
            "abierta_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("abierta_por", sa.Integer(), nullable=True),
        sa.Column("nota_apertura", sa.Text(), nullable=True),
        sa.Column("cerrada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cerrada_por", sa.Integer(), nullable=True),
        sa.Column("efectivo_contado", sa.Integer(), nullable=True),
        sa.Column("efectivo_esperado", sa.Integer(), nullable=True),
        sa.Column("nota_cierre", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "abierta = (cerrada_en IS NULL)", name=op.f("ck_caja_parqueadero_abierta_sin_cierre")
        ),
        sa.CheckConstraint("base >= 0", name=op.f("ck_caja_parqueadero_base_no_negativa")),
        sa.CheckConstraint(
            "cerrada_en IS NULL OR (efectivo_contado IS NOT NULL AND efectivo_esperado IS NOT NULL)",
            name=op.f("ck_caja_parqueadero_cerrada_con_conteo"),
        ),
        sa.CheckConstraint(
            "efectivo_contado IS NULL OR efectivo_contado >= 0",
            name=op.f("ck_caja_parqueadero_conteo_no_negativo"),
        ),
        sa.ForeignKeyConstraint(
            ["abierta_por"],
            ["administrador.id"],
            name=op.f("fk_caja_parqueadero_abierta_por_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["cerrada_por"],
            ["administrador.id"],
            name=op.f("fk_caja_parqueadero_cerrada_por_administrador"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_caja_parqueadero")),
    )
    op.create_index(
        "uq_caja_parqueadero_una_abierta",
        "caja_parqueadero",
        ["abierta"],
        unique=True,
        postgresql_where=sa.text("abierta"),
    )
    op.create_table(
        "pago_mensualidad",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("mensualidad_id", sa.Integer(), nullable=False),
        sa.Column("desde", sa.Date(), nullable=False),
        sa.Column("hasta", sa.Date(), nullable=False),
        sa.Column("valor", sa.Integer(), nullable=False),
        sa.Column(
            "medio_pago",
            sa.Enum(
                "efectivo",
                "nequi",
                "daviplata",
                "tarjeta",
                name="medio_pago",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("caja_id", sa.Integer(), nullable=True),
        sa.Column("administrador_id", sa.Integer(), nullable=True),
        sa.Column(
            "fecha_hora",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(caja_id IS NULL) = (medio_pago IS NULL) AND (caja_id IS NULL) = (valor = 0)",
            name=op.f("ck_pago_mensualidad_cobro_en_caja"),
        ),
        sa.CheckConstraint("hasta >= desde", name=op.f("ck_pago_mensualidad_periodo_valido")),
        sa.CheckConstraint("valor >= 0", name=op.f("ck_pago_mensualidad_valor_no_negativo")),
        sa.ForeignKeyConstraint(
            ["administrador_id"],
            ["administrador.id"],
            name=op.f("fk_pago_mensualidad_administrador_id_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["caja_id"],
            ["caja_parqueadero.id"],
            name=op.f("fk_pago_mensualidad_caja_id_caja_parqueadero"),
        ),
        sa.ForeignKeyConstraint(
            ["mensualidad_id"],
            ["mensualidad.id"],
            name=op.f("fk_pago_mensualidad_mensualidad_id_mensualidad"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pago_mensualidad")),
    )
    op.create_index(
        op.f("ix_pago_mensualidad_caja_id"), "pago_mensualidad", ["caja_id"], unique=False
    )
    op.create_index(
        op.f("ix_pago_mensualidad_fecha_hora"), "pago_mensualidad", ["fecha_hora"], unique=False
    )
    op.create_index(
        op.f("ix_pago_mensualidad_mensualidad_id"),
        "pago_mensualidad",
        ["mensualidad_id"],
        unique=False,
    )
    op.create_table(
        "parqueo",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("placa", sa.String(length=7), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "carro", "moto", name="tipo", native_enum=False, create_constraint=True, length=20
            ),
            nullable=False,
        ),
        sa.Column(
            "estado",
            sa.Enum(
                "adentro",
                "salio",
                "anulado",
                name="estado",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="adentro",
            nullable=False,
        ),
        sa.Column(
            "entrada_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("salida_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valor_hora", sa.Integer(), nullable=False),
        sa.Column("valor_fraccion", sa.Integer(), nullable=True),
        sa.Column("gracia_minutos", sa.SmallInteger(), nullable=False),
        sa.Column("observaciones", sa.Text(), nullable=True),
        sa.Column("mensualidad_id", sa.Integer(), nullable=True),
        sa.Column("minutos", sa.Integer(), nullable=True),
        sa.Column("horas_cobradas", sa.SmallInteger(), nullable=True),
        sa.Column("valor_calculado", sa.Integer(), nullable=True),
        sa.Column("valor_cobrado", sa.Integer(), nullable=True),
        sa.Column("motivo_ajuste", sa.Text(), nullable=True),
        sa.Column(
            "medio_pago",
            sa.Enum(
                "efectivo",
                "nequi",
                "daviplata",
                "tarjeta",
                name="medio_pago",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column("recibido", sa.Integer(), nullable=True),
        sa.Column("caja_id", sa.Integer(), nullable=True),
        sa.Column("motivo_anulacion", sa.Text(), nullable=True),
        sa.Column("entrada_por", sa.Integer(), nullable=True),
        sa.Column("salida_por", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "estado <> 'anulado' OR btrim(coalesce(motivo_anulacion, '')) <> ''",
            name=op.f("ck_parqueo_anulado_con_motivo"),
        ),
        sa.CheckConstraint(
            "estado <> 'salio' OR (valor_calculado IS NOT NULL AND valor_cobrado IS NOT NULL)",
            name=op.f("ck_parqueo_salida_con_valor"),
        ),
        sa.CheckConstraint(
            "estado = 'adentro' OR salida_en IS NOT NULL", name=op.f("ck_parqueo_salida_con_hora")
        ),
        sa.CheckConstraint("placa ~ '^[A-Z0-9]{4,7}$'", name=op.f("ck_parqueo_placa_limpia")),
        sa.CheckConstraint(
            "valor_cobrado IS NULL OR valor_cobrado = valor_calculado OR btrim(coalesce(motivo_ajuste, '')) <> ''",
            name=op.f("ck_parqueo_motivo_si_cambia_el_valor"),
        ),
        sa.CheckConstraint(
            "coalesce(valor_cobrado, 0) = 0 OR (caja_id IS NOT NULL AND medio_pago IS NOT NULL)",
            name=op.f("ck_parqueo_cobro_en_caja"),
        ),
        sa.CheckConstraint(
            "gracia_minutos BETWEEN 0 AND 59", name=op.f("ck_parqueo_gracia_valida")
        ),
        sa.CheckConstraint(
            "recibido IS NULL OR recibido >= valor_cobrado",
            name=op.f("ck_parqueo_recibido_alcanza"),
        ),
        sa.CheckConstraint(
            "valor_cobrado IS NULL OR valor_cobrado >= 0",
            name=op.f("ck_parqueo_valor_cobrado_no_negativo"),
        ),
        sa.CheckConstraint(
            "valor_fraccion IS NULL OR valor_fraccion >= 0",
            name=op.f("ck_parqueo_fraccion_no_negativa"),
        ),
        sa.CheckConstraint("valor_hora > 0", name=op.f("ck_parqueo_valor_hora_positivo")),
        sa.ForeignKeyConstraint(
            ["caja_id"], ["caja_parqueadero.id"], name=op.f("fk_parqueo_caja_id_caja_parqueadero")
        ),
        sa.ForeignKeyConstraint(
            ["entrada_por"], ["administrador.id"], name=op.f("fk_parqueo_entrada_por_administrador")
        ),
        sa.ForeignKeyConstraint(
            ["mensualidad_id"],
            ["mensualidad.id"],
            name=op.f("fk_parqueo_mensualidad_id_mensualidad"),
        ),
        sa.ForeignKeyConstraint(
            ["salida_por"], ["administrador.id"], name=op.f("fk_parqueo_salida_por_administrador")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parqueo")),
    )
    op.create_index(op.f("ix_parqueo_caja_id"), "parqueo", ["caja_id"], unique=False)
    op.create_index(op.f("ix_parqueo_entrada_en"), "parqueo", ["entrada_en"], unique=False)
    op.create_index(op.f("ix_parqueo_salida_en"), "parqueo", ["salida_en"], unique=False)
    op.create_index(
        "uq_parqueo_placa_adentro",
        "parqueo",
        ["placa"],
        unique=True,
        postgresql_where=sa.text("estado = 'adentro'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_parqueo_placa_adentro",
        table_name="parqueo",
        postgresql_where=sa.text("estado = 'adentro'"),
    )
    op.drop_index(op.f("ix_parqueo_salida_en"), table_name="parqueo")
    op.drop_index(op.f("ix_parqueo_entrada_en"), table_name="parqueo")
    op.drop_index(op.f("ix_parqueo_caja_id"), table_name="parqueo")
    op.drop_table("parqueo")
    op.drop_index(op.f("ix_pago_mensualidad_mensualidad_id"), table_name="pago_mensualidad")
    op.drop_index(op.f("ix_pago_mensualidad_fecha_hora"), table_name="pago_mensualidad")
    op.drop_index(op.f("ix_pago_mensualidad_caja_id"), table_name="pago_mensualidad")
    op.drop_table("pago_mensualidad")
    op.drop_index(
        "uq_caja_parqueadero_una_abierta",
        table_name="caja_parqueadero",
        postgresql_where=sa.text("abierta"),
    )
    op.drop_table("caja_parqueadero")
    op.drop_index(
        "uq_mensualidad_placa_activa", table_name="mensualidad", postgresql_where=sa.text("activa")
    )
    op.drop_table("mensualidad")
