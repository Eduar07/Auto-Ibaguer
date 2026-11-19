"""El menú con que abre el lavadero: Básico y Encerado, para carros y motos.

Decisión del negocio (18 de noviembre de 2026): por ahora se ofrecen dos servicios. Esta
migración deja así el menú de la base que ya existe (en una base nueva, lo carga
preparar-base con app.catalogo.datos_iniciales):

- Carros. Básico: automóvil $30.000, camioneta $40.000. Encerado (con polichadora):
  automóvil $60.000, camioneta $70.000.
- Motos. Básico: $15.000. Encerado (a mano, sin polichadora): $20.000.
- Todos duran unos 45 minutos.
- Los demás servicios se ocultan (activo = false): no se borran, porque hay turnos y
  cobros que los usan, y se pueden volver a activar en Configuración.

Un precio que cambia no se sobrescribe: el anterior queda inactivo, como historia, y se
agrega el nuevo.

Revisión: 0021
Anterior: 0020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DURACION = 45
BASICO_CARRO = "Agua, champú Simoniz, aspirado por dentro, tapetes y llantina"
ENCERADO_CARRO = (
    "Básico + encerado con polichadora: cera 3M o Meguiar's, pasta para pulir 3M y "
    "Morita de B-Max, que humecta, restaura y da brillo a las partes negras"
)
BASICO_MOTO = "Agua, champú Simoniz y llantina"
ENCERADO_MOTO = "Básico + encerado a mano con cera 3M o Meguiar's (sin polichadora)"

# (fila, servicio, orden, descripción, {tipo de vehículo: precio})
MENU = (
    ("Carros", "Básico", 1, BASICO_CARRO, {"Automóvil": 30_000, "Camioneta": 40_000}),
    ("Carros", "Encerado", 2, ENCERADO_CARRO, {"Automóvil": 60_000, "Camioneta": 70_000}),
    ("Motos", "Básico", 1, BASICO_MOTO, {"Moto": 15_000}),
    ("Motos", "Encerado", 2, ENCERADO_MOTO, {"Moto": 20_000}),
)


def upgrade() -> None:
    conexion = op.get_bind()
    for fila, nombre, orden, descripcion, precios in MENU:
        datos = {"fila": fila, "nombre": nombre, "orden": orden, "descripcion": descripcion}
        # Si el servicio no existe en esa fila (el Encerado de motos), se crea.
        conexion.execute(
            sa.text(
                "INSERT INTO servicio (fila_id, nombre, descripcion, orden, activo) "
                "SELECT f.id, CAST(:nombre AS varchar), CAST(:descripcion AS text), CAST(:orden AS smallint), true FROM fila f "
                "WHERE f.nombre = :fila AND NOT EXISTS ("
                "  SELECT 1 FROM servicio s WHERE s.fila_id = f.id AND s.nombre = CAST(:nombre AS varchar))"
            ),
            datos,
        )
        conexion.execute(
            sa.text(
                "UPDATE servicio s SET descripcion = :descripcion, orden = :orden, "
                "activo = true FROM fila f "
                "WHERE s.fila_id = f.id AND f.nombre = :fila AND s.nombre = :nombre"
            ),
            datos,
        )
        for tipo, precio in precios.items():
            _poner_precio(conexion, fila, nombre, tipo, precio)
    # Lo que no está en el menú se oculta.
    conexion.execute(
        sa.text("UPDATE servicio SET activo = false WHERE nombre NOT IN ('Básico', 'Encerado')")
    )


def _poner_precio(conexion: sa.Connection, fila: str, nombre: str, tipo: str, precio: int) -> None:
    datos = {"fila": fila, "nombre": nombre, "tipo": tipo, "precio": precio, "duracion": DURACION}
    servicio_y_tipo = (
        "SELECT s.id AS servicio_id, t.id AS tipo_id FROM servicio s "
        "JOIN fila f ON f.id = s.fila_id JOIN tipo_vehiculo t ON t.nombre = :tipo "
        "WHERE f.nombre = :fila AND s.nombre = :nombre"
    )
    # El precio vigente que ya no es el de ahora queda como historia.
    conexion.execute(
        sa.text(
            f"UPDATE servicio_precio p SET activo = false FROM ({servicio_y_tipo}) x "
            "WHERE p.servicio_id = x.servicio_id AND p.tipo_vehiculo_id = x.tipo_id "
            "AND p.activo AND (p.precio <> :precio OR p.duracion_min <> :duracion)"
        ),
        datos,
    )
    conexion.execute(
        sa.text(
            "INSERT INTO servicio_precio (servicio_id, tipo_vehiculo_id, precio, duracion_min, "
            f"activo) SELECT x.servicio_id, x.tipo_id, CAST(:precio AS integer), CAST(:duracion AS smallint), true "
            f"FROM ({servicio_y_tipo}) x WHERE NOT EXISTS ("
            "  SELECT 1 FROM servicio_precio p WHERE p.servicio_id = x.servicio_id "
            "  AND p.tipo_vehiculo_id = x.tipo_id AND p.activo)"
        ),
        datos,
    )


def downgrade() -> None:
    # Es un cambio de datos del negocio: al volver atrás, el menú queda como esté. Los
    # servicios ocultos se activan otra vez en Configuración.
    pass
