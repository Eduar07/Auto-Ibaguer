"""Las migraciones suben y bajan sin errores, en una base aparte."""

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from app.cli import configuracion_alembic, migrar
from pruebas.base_de_pruebas import URL_PRUEBAS, borrar_base, recrear_base


# Pide la base de pruebas primero: si PostgreSQL está apagado, se detiene con un mensaje claro.
@pytest.mark.usefixtures("motor")
def test_la_migracion_sube_baja_y_vuelve_a_subir():
    url = (
        make_url(URL_PRUEBAS)
        .set(database="auto_ibaguer_pruebas_migraciones")
        .render_as_string(hide_password=False)
    )
    recrear_base(url)
    motor = create_engine(url)

    def tablas() -> set[str]:
        return set(inspect(motor).get_table_names()) - {"alembic_version"}

    try:
        migrar(url, configurar_logs=False)
        assert len(tablas()) == 16

        command.downgrade(configuracion_alembic(url, configurar_logs=False), "base")
        assert tablas() == set()

        migrar(url, configurar_logs=False)
        assert len(tablas()) == 16
    finally:
        motor.dispose()
        borrar_base(url)
