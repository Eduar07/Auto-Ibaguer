"""Bandeja de salida con reintentos (hito 6).

- mensaje_saliente.estado acepta "descartado": un aviso que ya no servía cuando le tocó
  salir (por ejemplo, el cliente llegó mientras WhatsApp estaba caído).
- Regla: un mensaje enviado tiene fecha de envío.

Revisión: 0004
Anterior: 0003
Creada: 2026-11-02 23:30:00-05:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ESTADOS_ANTES = "estado IN ('pendiente', 'enviado', 'fallido')"
ESTADOS_AHORA = "estado IN ('pendiente', 'enviado', 'fallido', 'descartado')"


def upgrade() -> None:
    # Por eso los estados se guardan como texto con una regla CHECK y no como ENUM de
    # PostgreSQL: agregar uno es cambiar la regla.
    op.drop_constraint("estado", "mensaje_saliente", type_="check")
    op.create_check_constraint("estado", "mensaje_saliente", ESTADOS_AHORA)
    op.create_check_constraint(
        "enviado_con_fecha", "mensaje_saliente", "estado <> 'enviado' OR enviado_en IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint("enviado_con_fecha", "mensaje_saliente", type_="check")
    # La versión anterior no conoce "descartado": esos avisos quedan como no enviados.
    op.execute(
        "UPDATE mensaje_saliente SET estado = 'fallido', "
        "ultimo_error = coalesce(ultimo_error, 'Descartado: ya no servía') "
        "WHERE estado = 'descartado'"
    )
    op.drop_constraint("estado", "mensaje_saliente", type_="check")
    op.create_check_constraint("estado", "mensaje_saliente", ESTADOS_ANTES)
