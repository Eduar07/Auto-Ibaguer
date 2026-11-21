"""Los videos de los servicios: el bot le manda al cliente el del servicio que escoge.

- video_del_servicio: un video por servicio (el archivo, su nombre, tipo y peso) y el id con
  que WhatsApp lo tiene guardado (le sirve 30 días; el reloj lo vuelve a subir antes).

Revisión: 0023
Anterior: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_del_servicio",
        sa.Column("servicio_id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(length=200), nullable=False),
        sa.Column("tipo", sa.String(length=40), nullable=False),
        sa.Column("peso", sa.Integer(), nullable=False),
        sa.Column("contenido", sa.LargeBinary(), nullable=False),
        sa.Column(
            "subido_en",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id_en_meta", sa.String(length=128), nullable=True),
        sa.Column("subido_a_meta_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("peso > 0", name=op.f("ck_video_del_servicio_peso_positivo")),
        sa.ForeignKeyConstraint(
            ["servicio_id"],
            ["servicio.id"],
            name=op.f("fk_video_del_servicio_servicio_id_servicio"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("servicio_id", name=op.f("pk_video_del_servicio")),
    )


def downgrade() -> None:
    op.drop_table("video_del_servicio")
