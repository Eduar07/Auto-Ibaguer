"""Reservar un día y una hora, y el horario de 6:30 a.m. a 12 de la noche.

- turno.estado acepta "reservado" (también en el historial): el cliente reservó un día y
  una hora; a esa hora entra a la fila de primero.
- turno.hora_reservada: el día y la hora que reservó. Un turno Reservado siempre la tiene.
- turno.prioridad: ya le llegó la hora de su reserva y va de primero en la fila.
- turno.recordado_en: cuándo se le recordó la reserva.
- Índice para que el reloj encuentre rápido las reservas a las que les llegó la hora.
- Horario del negocio (decisión del 19 de noviembre de 2026): de 6:30 a.m. a 12 de la
  noche. Solo se cambia si todavía estaba el de antes (5:30 a.m. a 7:30 p.m.): si el
  administrador ya había puesto otro en Configuración, se respeta.

Revisión: 0022
Anterior: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANTES = ("en_fila", "llamado", "llego", "en_lavado", "listo", "pagado", "cancelado", "no_llego")
AHORA = ("reservado", *ANTES)
# (tabla, columna, nombre de la regla): las tres columnas que guardan un estado del turno.
CON_ESTADO = (
    ("turno", "estado", "estado"),
    ("turno_historial", "estado_anterior", "estado_anterior"),
    ("turno_historial", "estado_nuevo", "estado_nuevo"),
)
HORARIO_ANTES = {"hora_apertura": "05:30", "hora_cierre": "19:30"}
HORARIO_AHORA = {"hora_apertura": "06:30", "hora_cierre": "00:00"}


def _lista(estados: tuple[str, ...]) -> str:
    return ", ".join(f"'{estado}'" for estado in estados)


def _reglas_de_estado(estados: tuple[str, ...]) -> None:
    for tabla, columna, regla in CON_ESTADO:
        op.drop_constraint(regla, tabla, type_="check")
        op.create_check_constraint(regla, tabla, f"{columna} IN ({_lista(estados)})")


def _horario(de: dict[str, str], a: dict[str, str]) -> None:
    for clave, antes in de.items():
        op.execute(
            sa.text(
                "UPDATE configuracion SET valor = to_jsonb(CAST(:ahora AS text)), "
                "actualizado_en = now() WHERE clave = :clave AND valor = to_jsonb(CAST(:antes "
                "AS text))"
            ).bindparams(clave=clave, antes=antes, ahora=a[clave])
        )


def upgrade() -> None:
    _reglas_de_estado(AHORA)
    op.add_column("turno", sa.Column("hora_reservada", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "turno",
        sa.Column("prioridad", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("turno", sa.Column("recordado_en", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "reservado_con_hora", "turno", "estado <> 'reservado' OR hora_reservada IS NOT NULL"
    )
    op.create_index(
        "ix_turno_reservas",
        "turno",
        ["hora_reservada"],
        postgresql_where=sa.text("estado = 'reservado'"),
    )
    _horario(HORARIO_ANTES, HORARIO_AHORA)


def downgrade() -> None:
    # La versión anterior no sabe de reservas: las que no han llegado se cancelan (queda en
    # el historial por qué), y su historial pasa a "en fila".
    op.execute(
        "INSERT INTO turno_historial (turno_id, estado_anterior, estado_nuevo, actor, detalle, "
        "fecha_hora) SELECT id, 'en_fila', 'cancelado', 'sistema', "
        "'Se volvió a una versión sin reservas', now() FROM turno WHERE estado = 'reservado'"
    )
    op.execute("UPDATE turno SET estado = 'cancelado' WHERE estado = 'reservado'")
    op.execute(
        "UPDATE turno_historial SET estado_anterior = 'en_fila' WHERE estado_anterior = 'reservado'"
    )
    op.execute(
        "UPDATE turno_historial SET estado_nuevo = 'en_fila' WHERE estado_nuevo = 'reservado'"
    )
    _horario(HORARIO_AHORA, HORARIO_ANTES)
    op.drop_index("ix_turno_reservas", table_name="turno")
    op.drop_constraint("reservado_con_hora", "turno", type_="check")
    op.drop_column("turno", "recordado_en")
    op.drop_column("turno", "prioridad")
    op.drop_column("turno", "hora_reservada")
    _reglas_de_estado(ANTES)
