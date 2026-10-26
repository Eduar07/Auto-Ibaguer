"""Tablas iniciales de la Fase 1: 13 del negocio y 3 técnicas.

Generada con `alembic revision --autogenerate` y revisada a mano. Cada regla del negocio
que vive en la base de datos (placa única, estados válidos, un vehículo por puesto...)
aparece aquí como una restricción con nombre propio.

Revisión: 0001
Anterior:
Creada: 2026-10-25 23:47:25.588789-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "administrador",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("usuario", sa.String(length=40), nullable=False),
        sa.Column("clave_cifrada", sa.String(length=255), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_administrador")),
        sa.UniqueConstraint("usuario", name=op.f("uq_administrador_usuario")),
    )
    op.create_table(
        "cliente",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("telefono", sa.String(length=15), nullable=True),
        sa.Column("nombre", sa.String(length=80), nullable=False),
        sa.Column("acepta_datos", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fecha_aceptacion", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "telefono ~ '^[0-9]{8,15}$'", name=op.f("ck_cliente_telefono_solo_digitos")
        ),
        sa.CheckConstraint(
            "NOT acepta_datos OR fecha_aceptacion IS NOT NULL",
            name=op.f("ck_cliente_aceptacion_con_fecha"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cliente")),
        sa.UniqueConstraint("telefono", name=op.f("uq_cliente_telefono")),
    )
    op.create_table(
        "configuracion",
        sa.Column("clave", sa.String(length=60), nullable=False),
        sa.Column("valor", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("clave", name=op.f("pk_configuracion")),
    )
    op.create_table(
        "fila",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("nombre", sa.String(length=30), nullable=False),
        sa.Column("prefijo", sa.String(length=1), nullable=False),
        sa.CheckConstraint("prefijo ~ '^[A-Z]$'", name=op.f("ck_fila_prefijo_una_letra")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fila")),
        sa.UniqueConstraint("nombre", name=op.f("uq_fila_nombre")),
        sa.UniqueConstraint("prefijo", name=op.f("uq_fila_prefijo")),
    )
    op.create_table(
        "mensaje_entrante",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("id_meta", sa.String(length=128), nullable=False),
        sa.Column("telefono", sa.String(length=15), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("contenido", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "recibido_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("procesado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "telefono ~ '^[0-9]{8,15}$'", name=op.f("ck_mensaje_entrante_telefono_solo_digitos")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mensaje_entrante")),
        sa.UniqueConstraint("id_meta", name=op.f("uq_mensaje_entrante_id_meta")),
    )
    op.create_index(
        "ix_mensaje_entrante_por_procesar",
        "mensaje_entrante",
        ["recibido_en"],
        unique=False,
        postgresql_where=sa.text("procesado_en IS NULL"),
    )
    op.create_table(
        "puesto",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("fila_id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(length=10), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["fila_id"], ["fila.id"], name=op.f("fk_puesto_fila_id_fila")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_puesto")),
        sa.UniqueConstraint("nombre", name=op.f("uq_puesto_nombre")),
    )
    op.create_index(op.f("ix_puesto_fila_id"), "puesto", ["fila_id"], unique=False)
    op.create_table(
        "servicio",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("fila_id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(length=40), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("orden", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["fila_id"], ["fila.id"], name=op.f("fk_servicio_fila_id_fila")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_servicio")),
        sa.UniqueConstraint("fila_id", "nombre", name=op.f("uq_servicio_fila_id_nombre")),
    )
    op.create_index(op.f("ix_servicio_fila_id"), "servicio", ["fila_id"], unique=False)
    op.create_table(
        "sesion_bot",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("telefono", sa.String(length=15), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=True),
        sa.Column("paso_actual", sa.String(length=40), nullable=False),
        sa.Column(
            "datos_temporales",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expira_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "telefono ~ '^[0-9]{8,15}$'", name=op.f("ck_sesion_bot_telefono_solo_digitos")
        ),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_sesion_bot_cliente_id_cliente")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sesion_bot")),
        sa.UniqueConstraint("telefono", name=op.f("uq_sesion_bot_telefono")),
    )
    op.create_table(
        "tipo_vehiculo",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("nombre", sa.String(length=30), nullable=False),
        sa.Column("fila_id", sa.Integer(), nullable=False),
        sa.Column("patron_placa", sa.String(length=60), nullable=False),
        sa.ForeignKeyConstraint(
            ["fila_id"], ["fila.id"], name=op.f("fk_tipo_vehiculo_fila_id_fila")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tipo_vehiculo")),
        sa.UniqueConstraint("nombre", name=op.f("uq_tipo_vehiculo_nombre")),
    )
    op.create_index(op.f("ix_tipo_vehiculo_fila_id"), "tipo_vehiculo", ["fila_id"], unique=False)
    op.create_table(
        "consulta",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=True),
        sa.Column(
            "tipo",
            sa.Enum(
                "precios",
                "fila",
                name="tipo_consulta",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("tipo_vehiculo_id", sa.Integer(), nullable=True),
        sa.Column(
            "fecha_hora",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_consulta_cliente_id_cliente")
        ),
        sa.ForeignKeyConstraint(
            ["tipo_vehiculo_id"],
            ["tipo_vehiculo.id"],
            name=op.f("fk_consulta_tipo_vehiculo_id_tipo_vehiculo"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consulta")),
    )
    op.create_index(op.f("ix_consulta_cliente_id"), "consulta", ["cliente_id"], unique=False)
    op.create_index(op.f("ix_consulta_fecha_hora"), "consulta", ["fecha_hora"], unique=False)
    op.create_table(
        "servicio_precio",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("servicio_id", sa.Integer(), nullable=False),
        sa.Column("tipo_vehiculo_id", sa.Integer(), nullable=False),
        sa.Column("precio", sa.Integer(), nullable=False),
        sa.Column("duracion_min", sa.SmallInteger(), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("duracion_min > 0", name=op.f("ck_servicio_precio_duracion_positiva")),
        sa.CheckConstraint("precio >= 0", name=op.f("ck_servicio_precio_precio_no_negativo")),
        sa.ForeignKeyConstraint(
            ["servicio_id"], ["servicio.id"], name=op.f("fk_servicio_precio_servicio_id_servicio")
        ),
        sa.ForeignKeyConstraint(
            ["tipo_vehiculo_id"],
            ["tipo_vehiculo.id"],
            name=op.f("fk_servicio_precio_tipo_vehiculo_id_tipo_vehiculo"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_servicio_precio")),
    )
    op.create_index(
        op.f("ix_servicio_precio_servicio_id"), "servicio_precio", ["servicio_id"], unique=False
    )
    op.create_index(
        op.f("ix_servicio_precio_tipo_vehiculo_id"),
        "servicio_precio",
        ["tipo_vehiculo_id"],
        unique=False,
    )
    op.create_index(
        "uq_servicio_precio_vigente",
        "servicio_precio",
        ["servicio_id", "tipo_vehiculo_id"],
        unique=True,
        postgresql_where=sa.text("activo"),
    )
    op.create_table(
        "vehiculo",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=False),
        sa.Column("tipo_vehiculo_id", sa.Integer(), nullable=False),
        sa.Column("placa", sa.String(length=7), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("placa ~ '^[A-Z0-9]{5,7}$'", name=op.f("ck_vehiculo_placa_formato")),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_vehiculo_cliente_id_cliente")
        ),
        sa.ForeignKeyConstraint(
            ["tipo_vehiculo_id"],
            ["tipo_vehiculo.id"],
            name=op.f("fk_vehiculo_tipo_vehiculo_id_tipo_vehiculo"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehiculo")),
        sa.UniqueConstraint("placa", name=op.f("uq_vehiculo_placa")),
    )
    op.create_index(op.f("ix_vehiculo_cliente_id"), "vehiculo", ["cliente_id"], unique=False)
    op.create_table(
        "turno",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("fila_id", sa.Integer(), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("consecutivo", sa.SmallInteger(), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=False),
        sa.Column("vehiculo_id", sa.Integer(), nullable=False),
        sa.Column("servicio_id", sa.Integer(), nullable=True),
        sa.Column("puesto_id", sa.Integer(), nullable=True),
        sa.Column(
            "estado",
            sa.Enum(
                "en_fila",
                "llamado",
                "llego",
                "en_lavado",
                "listo",
                "pagado",
                "cancelado",
                "no_llego",
                name="estado",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="en_fila",
            nullable=False,
        ),
        sa.Column(
            "origen",
            sa.Enum(
                "whatsapp",
                "dashboard",
                name="origen",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("en_camino", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "intentos_sin_llegar", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column(
            "en_fila_desde",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("llamado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gracia_hasta", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "actualizado_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "estado <> 'en_lavado' OR puesto_id IS NOT NULL",
            name=op.f("ck_turno_en_lavado_con_puesto"),
        ),
        sa.CheckConstraint(
            "estado <> 'llamado' OR (llamado_en IS NOT NULL AND gracia_hasta IS NOT NULL)",
            name=op.f("ck_turno_llamado_con_hora_limite"),
        ),
        sa.CheckConstraint("consecutivo > 0", name=op.f("ck_turno_consecutivo_positivo")),
        sa.CheckConstraint(
            "intentos_sin_llegar BETWEEN 0 AND 2", name=op.f("ck_turno_intentos_entre_0_y_2")
        ),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_turno_cliente_id_cliente")
        ),
        sa.ForeignKeyConstraint(["fila_id"], ["fila.id"], name=op.f("fk_turno_fila_id_fila")),
        sa.ForeignKeyConstraint(
            ["puesto_id"], ["puesto.id"], name=op.f("fk_turno_puesto_id_puesto")
        ),
        sa.ForeignKeyConstraint(
            ["servicio_id"], ["servicio.id"], name=op.f("fk_turno_servicio_id_servicio")
        ),
        sa.ForeignKeyConstraint(
            ["vehiculo_id"], ["vehiculo.id"], name=op.f("fk_turno_vehiculo_id_vehiculo")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turno")),
        sa.UniqueConstraint(
            "fila_id", "fecha", "consecutivo", name=op.f("uq_turno_fila_id_fecha_consecutivo")
        ),
    )
    op.create_index(op.f("ix_turno_cliente_id"), "turno", ["cliente_id"], unique=False)
    op.create_index(
        "ix_turno_fila_de_espera",
        "turno",
        ["fila_id", "en_fila_desde"],
        unique=False,
        postgresql_where=sa.text("estado IN ('en_fila', 'llamado', 'llego')"),
    )
    op.create_index(op.f("ix_turno_vehiculo_id"), "turno", ["vehiculo_id"], unique=False)
    op.create_index(
        "uq_turno_puesto_en_lavado",
        "turno",
        ["puesto_id"],
        unique=True,
        postgresql_where=sa.text("estado = 'en_lavado'"),
    )
    op.create_table(
        "mensaje_saliente",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("telefono", sa.String(length=15), nullable=False),
        sa.Column("cliente_id", sa.Integer(), nullable=True),
        sa.Column("turno_id", sa.BigInteger(), nullable=True),
        sa.Column("tipo", sa.String(length=40), nullable=False),
        sa.Column("contenido", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "estado",
            sa.Enum(
                "pendiente",
                "enviado",
                "fallido",
                name="estado",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="pendiente",
            nullable=False,
        ),
        sa.Column("intentos", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("id_meta", sa.String(length=128), nullable=True),
        sa.Column("ultimo_error", sa.Text(), nullable=True),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "proximo_intento_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("enviado_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "telefono ~ '^[0-9]{8,15}$'", name=op.f("ck_mensaje_saliente_telefono_solo_digitos")
        ),
        sa.CheckConstraint("intentos >= 0", name=op.f("ck_mensaje_saliente_intentos_no_negativos")),
        sa.ForeignKeyConstraint(
            ["cliente_id"], ["cliente.id"], name=op.f("fk_mensaje_saliente_cliente_id_cliente")
        ),
        sa.ForeignKeyConstraint(
            ["turno_id"], ["turno.id"], name=op.f("fk_mensaje_saliente_turno_id_turno")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mensaje_saliente")),
    )
    op.create_index(
        "ix_mensaje_saliente_por_enviar",
        "mensaje_saliente",
        ["proximo_intento_en"],
        unique=False,
        postgresql_where=sa.text("estado = 'pendiente'"),
    )
    op.create_index(
        op.f("ix_mensaje_saliente_turno_id"), "mensaje_saliente", ["turno_id"], unique=False
    )
    op.create_table(
        "pago",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("turno_id", sa.BigInteger(), nullable=False),
        sa.Column("precio_menu", sa.Integer(), nullable=False),
        sa.Column("precio_cobrado", sa.Integer(), nullable=False),
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
            nullable=False,
        ),
        sa.Column("administrador_id", sa.Integer(), nullable=True),
        sa.Column(
            "fecha_hora",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "precio_cobrado = precio_menu OR btrim(coalesce(motivo_ajuste, '')) <> ''",
            name=op.f("ck_pago_motivo_si_cambia_el_precio"),
        ),
        sa.CheckConstraint("precio_cobrado >= 0", name=op.f("ck_pago_precio_cobrado_no_negativo")),
        sa.CheckConstraint("precio_menu >= 0", name=op.f("ck_pago_precio_menu_no_negativo")),
        sa.ForeignKeyConstraint(
            ["administrador_id"],
            ["administrador.id"],
            name=op.f("fk_pago_administrador_id_administrador"),
        ),
        sa.ForeignKeyConstraint(["turno_id"], ["turno.id"], name=op.f("fk_pago_turno_id_turno")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pago")),
        sa.UniqueConstraint("turno_id", name=op.f("uq_pago_turno_id")),
    )
    op.create_index(op.f("ix_pago_fecha_hora"), "pago", ["fecha_hora"], unique=False)
    op.create_table(
        "turno_historial",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("turno_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "estado_anterior",
            sa.Enum(
                "en_fila",
                "llamado",
                "llego",
                "en_lavado",
                "listo",
                "pagado",
                "cancelado",
                "no_llego",
                name="estado_anterior",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
        sa.Column(
            "estado_nuevo",
            sa.Enum(
                "en_fila",
                "llamado",
                "llego",
                "en_lavado",
                "listo",
                "pagado",
                "cancelado",
                "no_llego",
                name="estado_nuevo",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor",
            sa.Enum(
                "cliente",
                "administrador",
                "sistema",
                name="actor",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("administrador_id", sa.Integer(), nullable=True),
        sa.Column("detalle", sa.Text(), nullable=True),
        sa.Column(
            "fecha_hora",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["administrador_id"],
            ["administrador.id"],
            name=op.f("fk_turno_historial_administrador_id_administrador"),
        ),
        sa.ForeignKeyConstraint(
            ["turno_id"], ["turno.id"], name=op.f("fk_turno_historial_turno_id_turno")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turno_historial")),
    )
    op.create_index(
        op.f("ix_turno_historial_turno_id"), "turno_historial", ["turno_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_turno_historial_turno_id"), table_name="turno_historial")
    op.drop_table("turno_historial")
    op.drop_index(op.f("ix_pago_fecha_hora"), table_name="pago")
    op.drop_table("pago")
    op.drop_index(op.f("ix_mensaje_saliente_turno_id"), table_name="mensaje_saliente")
    op.drop_index(
        "ix_mensaje_saliente_por_enviar",
        table_name="mensaje_saliente",
        postgresql_where=sa.text("estado = 'pendiente'"),
    )
    op.drop_table("mensaje_saliente")
    op.drop_index(
        "uq_turno_puesto_en_lavado",
        table_name="turno",
        postgresql_where=sa.text("estado = 'en_lavado'"),
    )
    op.drop_index(op.f("ix_turno_vehiculo_id"), table_name="turno")
    op.drop_index(
        "ix_turno_fila_de_espera",
        table_name="turno",
        postgresql_where=sa.text("estado IN ('en_fila', 'llamado', 'llego')"),
    )
    op.drop_index(op.f("ix_turno_cliente_id"), table_name="turno")
    op.drop_table("turno")
    op.drop_index(op.f("ix_vehiculo_cliente_id"), table_name="vehiculo")
    op.drop_table("vehiculo")
    op.drop_index(
        "uq_servicio_precio_vigente",
        table_name="servicio_precio",
        postgresql_where=sa.text("activo"),
    )
    op.drop_index(op.f("ix_servicio_precio_tipo_vehiculo_id"), table_name="servicio_precio")
    op.drop_index(op.f("ix_servicio_precio_servicio_id"), table_name="servicio_precio")
    op.drop_table("servicio_precio")
    op.drop_index(op.f("ix_consulta_fecha_hora"), table_name="consulta")
    op.drop_index(op.f("ix_consulta_cliente_id"), table_name="consulta")
    op.drop_table("consulta")
    op.drop_index(op.f("ix_tipo_vehiculo_fila_id"), table_name="tipo_vehiculo")
    op.drop_table("tipo_vehiculo")
    op.drop_table("sesion_bot")
    op.drop_index(op.f("ix_servicio_fila_id"), table_name="servicio")
    op.drop_table("servicio")
    op.drop_index(op.f("ix_puesto_fila_id"), table_name="puesto")
    op.drop_table("puesto")
    op.drop_index(
        "ix_mensaje_entrante_por_procesar",
        table_name="mensaje_entrante",
        postgresql_where=sa.text("procesado_en IS NULL"),
    )
    op.drop_table("mensaje_entrante")
    op.drop_table("fila")
    op.drop_table("configuracion")
    op.drop_table("cliente")
    op.drop_table("administrador")
