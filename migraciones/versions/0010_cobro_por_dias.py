"""El cobro por días del parqueadero.

- parqueo.valor_dia: lo máximo que paga un día (24 horas), copiado de las tarifas al entrar.
- parqueo.valor_dia_siguiente: lo que vale cada día después del primero.

Vacíos (como quedan las entradas de antes), todo se sigue cobrando por horas.

Revisión: 0010
Anterior: 0009
Creada: 2026-11-13 09:52:49.234028-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("parqueo", sa.Column("valor_dia", sa.Integer(), nullable=True))
    op.add_column("parqueo", sa.Column("valor_dia_siguiente", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_parqueo_dia_valido"), "parqueo", "valor_dia IS NULL OR valor_dia >= valor_hora"
    )
    op.create_check_constraint(
        op.f("ck_parqueo_dia_siguiente_valido"),
        "parqueo",
        "valor_dia_siguiente IS NULL OR (valor_dia IS NOT NULL AND valor_dia_siguiente > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_parqueo_dia_siguiente_valido"), "parqueo", type_="check")
    op.drop_constraint(op.f("ck_parqueo_dia_valido"), "parqueo", type_="check")
    op.drop_column("parqueo", "valor_dia_siguiente")
    op.drop_column("parqueo", "valor_dia")
