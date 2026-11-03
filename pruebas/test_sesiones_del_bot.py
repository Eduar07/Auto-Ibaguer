"""Sesiones del bot que vencen: tras 30 minutos sin respuesta, el bot vuelve al menú."""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app.bot import sesiones
from app.bot.tablas import SesionBot
from app.clientes.tablas import Cliente
from app.reloj import proceso, tareas
from pruebas.fabricas import LUNES_8AM, minutos, nuevo_cliente


def sesion_del_bot(
    sesion: Session,
    *,
    expira_en: datetime,
    cliente: Cliente | None = None,
    telefono: str = "573009998877",
    paso: str = "eligiendo_servicio",
    datos: dict[str, Any] | None = None,
) -> SesionBot:
    conversacion = SesionBot(
        telefono=cliente.telefono if cliente else telefono,
        cliente_id=cliente.id if cliente else None,
        paso_actual=paso,
        datos_temporales={"placa": "ABC123"} if datos is None else datos,
        expira_en=expira_en,
    )
    sesion.add(conversacion)
    sesion.flush()
    return conversacion


def test_la_de_un_cliente_vuelve_al_menu_y_olvida_lo_que_iba_respondiendo(sesion: Session):
    cliente = nuevo_cliente(sesion)
    conversacion = sesion_del_bot(sesion, cliente=cliente, expira_en=LUNES_8AM)

    assert sesiones.vencer(sesion, ahora=LUNES_8AM) == 1

    sesion.refresh(conversacion)
    assert conversacion.paso_actual == sesiones.PASO_INICIAL == "menu"
    assert conversacion.datos_temporales == {}
    # Se conserva: dice que ese celular ya le escribe al bot.
    assert conversacion.cliente_id == cliente.id


def test_la_de_quien_nunca_acepto_el_aviso_de_datos_se_borra_con_su_celular(sesion: Session):
    sesion_del_bot(sesion, expira_en=LUNES_8AM - minutos(1))

    assert sesiones.vencer(sesion, ahora=LUNES_8AM) == 1

    assert sesion.scalars(select(SesionBot)).all() == []


def test_una_sesion_que_no_ha_vencido_no_se_toca(sesion: Session):
    cliente = nuevo_cliente(sesion)
    conversacion = sesion_del_bot(sesion, cliente=cliente, expira_en=LUNES_8AM + minutos(1))
    sesion_del_bot(sesion, expira_en=LUNES_8AM + minutos(1))  # sin aceptar todavía

    assert sesiones.vencer(sesion, ahora=LUNES_8AM) == 0

    sesion.refresh(conversacion)
    assert conversacion.paso_actual == "eligiendo_servicio"
    assert len(sesion.scalars(select(SesionBot)).all()) == 2


def test_una_que_ya_estaba_en_el_menu_no_se_vuelve_a_contar(sesion: Session):
    cliente = nuevo_cliente(sesion)
    sesion_del_bot(sesion, cliente=cliente, paso="menu", datos={}, expira_en=LUNES_8AM)

    assert sesiones.vencer(sesion, ahora=LUNES_8AM) == 0


def test_el_reloj_las_vence_y_lo_anota_sin_datos_personales(
    sesion: Session, conexion: Connection, caplog
):
    caplog.set_level(logging.INFO, logger="app.reloj")
    sesion_del_bot(sesion, expira_en=LUNES_8AM)
    sesion_del_bot(sesion, cliente=nuevo_cliente(sesion), expira_en=LUNES_8AM)
    reloj = proceso.Reloj(
        [tareas.SESIONES_DEL_BOT],
        lambda: Session(bind=conexion, join_transaction_mode="create_savepoint"),
    )

    reloj.una_vuelta(LUNES_8AM + minutos(1))

    assert caplog.messages == [
        "Reloj: 2 sesiones del bot vencieron sin respuesta: el bot vuelve al menú."
    ]
