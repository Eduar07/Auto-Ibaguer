"""Usuarios y permisos: cada usuario es administrador o cajero, y entra a un negocio o a los
dos. Los que ya había quedan como administradores de los dos.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "administrador",
        sa.Column(
            "rol",
            sa.Enum(
                "administrador",
                "cajero",
                name="rol",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            server_default="administrador",
            nullable=False,
        ),
    )
    op.add_column(
        "administrador",
        sa.Column(
            "negocio",
            sa.Enum(
                "lavadero",
                "parqueadero",
                name="negocio",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    hay = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM administrador WHERE rol <> 'administrador' OR negocio IS NOT NULL"
        )
    )
    if hay.scalar_one():
        raise RuntimeError(
            "Hay cajeros o usuarios de un solo negocio: bajar de versión los volvería"
            " administradores de los dos. Cámbialos primero."
        )
    op.drop_constraint(op.f("ck_administrador_negocio"), "administrador")
    op.drop_constraint(op.f("ck_administrador_rol"), "administrador")
    op.drop_column("administrador", "negocio")
    op.drop_column("administrador", "rol")
