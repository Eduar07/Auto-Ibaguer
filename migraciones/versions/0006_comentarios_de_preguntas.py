"""Preguntas que el bot no supo responder (hito 7b, parte 2).

- comentario.tipo acepta "pregunta": lo que un cliente preguntó y la ficha del negocio no
  decía. Le llega al administrador en Comentarios, para responderle y completar la ficha.

Revisión: 0006
Anterior: 0005
Creada: 2026-11-04 05:45:05-05:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIPOS_ANTES = "tipo IN ('queja', 'felicitacion', 'quiere_hablar')"
TIPOS_AHORA = "tipo IN ('queja', 'felicitacion', 'quiere_hablar', 'pregunta')"


def upgrade() -> None:
    # Como los estados de la bandeja de salida (0004): agregar un tipo es cambiar la regla.
    op.drop_constraint("tipo", "comentario", type_="check")
    op.create_check_constraint("tipo", "comentario", TIPOS_AHORA)


def downgrade() -> None:
    # La versión anterior no conoce "pregunta": quedan como pedidos de hablar con alguien,
    # que es lo que son (alguien espera una respuesta).
    op.execute("UPDATE comentario SET tipo = 'quiere_hablar' WHERE tipo = 'pregunta'")
    op.drop_constraint("tipo", "comentario", type_="check")
    op.create_check_constraint("tipo", "comentario", TIPOS_ANTES)
