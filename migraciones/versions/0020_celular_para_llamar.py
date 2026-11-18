"""El celular para llamar a quien escribe por WhatsApp sin mostrar su número.

- cliente.celular: a quien tiene nombre de usuario en WhatsApp, Meta no le muestra el
  número al negocio. El bot se lo pide (puede decir que ahora no) para poder llamarlo.
  Solo dígitos, y se borra con los demás datos si pide borrarlos (Ley 1581).

Revisión: 0020
Anterior: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cliente", sa.Column("celular", sa.String(length=15), nullable=True))
    op.create_check_constraint(
        op.f("ck_cliente_celular_solo_digitos"), "cliente", "celular ~ '^[0-9]{8,15}$'"
    )
    op.create_check_constraint(
        op.f("ck_cliente_borrado_sin_celular"),
        "cliente",
        "datos_borrados_en IS NULL OR celular IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_cliente_borrado_sin_celular"), "cliente", type_="check")
    op.drop_constraint(op.f("ck_cliente_celular_solo_digitos"), "cliente", type_="check")
    op.drop_column("cliente", "celular")
