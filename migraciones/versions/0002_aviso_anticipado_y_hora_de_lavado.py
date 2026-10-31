"""Aviso anticipado y hora de lavado (hito 3).

- turno.aviso_anticipado_en: cuándo se le pidió al cliente salir hacia el lavadero, para
  no repetir el aviso de "faltan 2 turnos".
- turno.en_lavado_desde: cuándo entró al puesto. Sirve para estimar cuándo se libera el
  puesto y para medir la duración real de cada lavado.
- Regla: un turno En lavado siempre tiene su hora de entrada.
- Índice para que el reloj encuentre rápido los llamados con el tiempo de gracia vencido.

Revisión: 0002
Anterior: 0001
Creada: 2026-10-30 19:45:10.929583-05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "turno", sa.Column("aviso_anticipado_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("turno", sa.Column("en_lavado_desde", sa.DateTime(timezone=True), nullable=True))
    # Si ya hubiera vehículos en un puesto, se toma la última actualización como su hora
    # de entrada, para que la regla nueva no rechace los datos existentes.
    op.execute(
        "UPDATE turno SET en_lavado_desde = actualizado_en "
        "WHERE estado = 'en_lavado' AND en_lavado_desde IS NULL"
    )
    op.create_check_constraint(
        "en_lavado_con_hora", "turno", "estado <> 'en_lavado' OR en_lavado_desde IS NOT NULL"
    )
    op.create_index(
        "ix_turno_gracia_por_vencer",
        "turno",
        ["gracia_hasta"],
        unique=False,
        postgresql_where=sa.text("estado = 'llamado'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_turno_gracia_por_vencer",
        table_name="turno",
        postgresql_where=sa.text("estado = 'llamado'"),
    )
    op.drop_constraint("en_lavado_con_hora", "turno", type_="check")
    op.drop_column("turno", "en_lavado_desde")
    op.drop_column("turno", "aviso_anticipado_en")
