"""Clientes que escriben por WhatsApp sin mostrar su celular.

Desde 2026, a quien tiene nombre de usuario en WhatsApp Meta le puede mandar al bot sin su
celular: solo un id de usuario de la empresa (BSUID, como "CO.13491208655302741918"). Con
ese id se le contesta igual. Donde se guarda el "teléfono" de una conversación de
WhatsApp, ahora cabe el celular o ese id:

- columnas `telefono` de 15 a 140 caracteres;
- la regla "solo dígitos" pasa a "dígitos, o país, punto y letras y números".

Revisión: 0018
Anterior: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOLO_DIGITOS = "telefono ~ '^[0-9]{8,15}$'"
TELEFONO_O_USUARIO = "telefono ~ '^([0-9]{8,15}|[A-Z]{2}\\.[A-Za-z0-9]{1,128})$'"
CON_REGLA = (
    "cliente",
    "mensaje_entrante",
    "mensaje_saliente",
    "sesion_bot",
    "pausa_del_bot",
    "seguimiento",
)
NULLABLE = {"cliente": True, "comentario": True}


def _columna(tabla: str, largo: int) -> None:
    op.alter_column(
        tabla,
        "telefono",
        type_=sa.String(length=largo),
        existing_nullable=NULLABLE.get(tabla, False),
    )


def upgrade() -> None:
    for tabla in CON_REGLA:
        op.drop_constraint(op.f(f"ck_{tabla}_telefono_solo_digitos"), tabla, type_="check")
        _columna(tabla, 140)
        op.create_check_constraint(
            op.f(f"ck_{tabla}_telefono_o_usuario"), tabla, TELEFONO_O_USUARIO
        )
    _columna("comentario", 140)


def downgrade() -> None:
    # La versión anterior no sabe atender a quien no muestra su celular: sus
    # conversaciones se borran (lo de la caja y los turnos no depende de esto).
    for tabla in (
        "mensaje_entrante",
        "mensaje_saliente",
        "sesion_bot",
        "pausa_del_bot",
        "seguimiento",
    ):
        op.execute(f"DELETE FROM {tabla} WHERE telefono !~ '^[0-9]{{8,15}}$'")
    op.execute("UPDATE cliente SET telefono = NULL WHERE telefono !~ '^[0-9]{8,15}$'")
    op.execute("UPDATE comentario SET telefono = NULL WHERE telefono !~ '^[0-9]{8,15}$'")
    _columna("comentario", 15)
    for tabla in CON_REGLA:
        op.drop_constraint(op.f(f"ck_{tabla}_telefono_o_usuario"), tabla, type_="check")
        _columna(tabla, 15)
        op.create_check_constraint(op.f(f"ck_{tabla}_telefono_solo_digitos"), tabla, SOLO_DIGITOS)
