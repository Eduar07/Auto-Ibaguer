"""Borrar los datos de un cliente a pedido suyo (hito 4).

- cliente.datos_borrados_en: cuándo se borraron sus datos personales (Ley 1581). El
  registro queda sin nombre real, sin celular y sin placas; sus turnos y cobros siguen
  en la contabilidad.
- Regla: un cliente con los datos borrados no tiene celular.

Revisión: 0003
Anterior: 0002
Creada: 2026-11-02 20:30:00-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cliente", sa.Column("datos_borrados_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "borrado_sin_telefono", "cliente", "datos_borrados_en IS NULL OR telefono IS NULL"
    )


def downgrade() -> None:
    op.drop_constraint("borrado_sin_telefono", "cliente", type_="check")
    op.drop_column("cliente", "datos_borrados_en")
