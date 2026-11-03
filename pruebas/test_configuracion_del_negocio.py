"""Configuración del negocio desde el dashboard: parámetros, precios, servicios y puestos."""

from datetime import time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalogo import configuracion
from app.catalogo.configuracion import ConfiguracionInvalida
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import Puesto, ServicioPrecio
from app.turnos import casos_de_uso as casos
from app.turnos import consultas, datos
from app.turnos.errores import PuestoOcupado
from app.turnos.estados import EstadoTurno, MedioPago
from pruebas.fabricas import (
    LUNES_8AM,
    en_el_local,
    estado,
    fila,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    puesto,
    servicio,
    tipo_vehiculo,
)

FORMULARIO = {
    "nombre_negocio": "  Auto   Ibaguer ",
    "telefono_contacto": "322 755 6850",
    "hora_apertura": "06:00",
    "hora_cierre": "18:30",
    "dias_atencion": ["1", "2", "3", "4", "5", "6"],
    "bot_activo": True,
    "gracia_minutos": "20",
    "avisar_cuando_falten": "3",
    "sesion_bot_minutos": "45",
    "aviso_datos": "Guardamos tu nombre, celular y placa solo para atenderte. ¿Aceptas?",
    "gemini_activo": True,
    "modelo_gemini": " Gemini-3.6-Flash ",
}


# --- Parámetros -------------------------------------------------------------------------------


def test_guardar_parametros_los_deja_listos_para_el_bot_y_la_fila(sesion: Session):
    cambiaron = configuracion.guardar_parametros(sesion, FORMULARIO)

    parametros = leer_parametros(sesion)
    assert parametros.nombre_negocio == "Auto Ibaguer"
    assert parametros.telefono_contacto == "573227556850"
    assert (parametros.hora_apertura, parametros.hora_cierre) == (time(6), time(18, 30))
    assert parametros.dias_atencion == {1, 2, 3, 4, 5, 6}
    assert parametros.gracia == timedelta(minutes=20)
    assert parametros.avisar_cuando_falten == 3
    assert parametros.sesion_bot == timedelta(minutes=45)
    assert (parametros.gemini_activo, parametros.modelo_gemini) == (True, "gemini-3.6-flash")
    assert "hora_apertura" in cambiaron and "nombre_negocio" not in cambiaron
    assert configuracion.guardar_parametros(sesion, FORMULARIO) == []  # nada nuevo


def test_todos_los_errores_se_dicen_juntos_y_no_se_guarda_nada(sesion: Session):
    malo = {
        **FORMULARIO,
        "hora_cierre": "05:00",
        "dias_atencion": [],
        "gracia_minutos": "0",
        "aviso_datos": "corto",
        "telefono_contacto": "123",
        "modelo_gemini": "gemini/../../otra-cosa",
    }

    with pytest.raises(ConfiguracionInvalida) as error:
        configuracion.guardar_parametros(sesion, malo)

    assert set(error.value.errores) == {
        "hora_cierre",
        "dias_atencion",
        "gracia_minutos",
        "aviso_datos",
        "telefono_contacto",
        "modelo_gemini",
    }
    assert leer_parametros(sesion).hora_apertura == time(5, 30)


@pytest.mark.parametrize("dias", [["8"], ["12"], ["0"], ["lunes"]])
def test_solo_dias_de_la_semana(sesion: Session, dias: list[str]):
    with pytest.raises(ConfiguracionInvalida, match="revisa"):
        configuracion.guardar_parametros(sesion, {**FORMULARIO, "dias_atencion": dias})


# --- Precios y servicios ---------------------------------------------------------------------


def test_un_precio_nuevo_apaga_el_viejo_y_no_cambia_los_cobros_anteriores(sesion: Session):
    basico = servicio(sesion, "Básico").id
    automovil = tipo_vehiculo(sesion, "Automóvil").id
    nuevo = en_el_local(sesion, placa="ABC123", servicio_id=basico)
    casos.pasar_a_puesto(sesion, nuevo.turno.id, puesto(sesion, "C1").id, ahora=LUNES_8AM)
    casos.marcar_listo(sesion, nuevo.turno.id, ahora=LUNES_8AM + minutos(30))
    pago = casos.cobrar(sesion, nuevo.turno.id, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM)

    cambio = configuracion.cambiar_precio(sesion, basico, automovil, precio=45_000, duracion_min=50)

    assert cambio is True
    assert pago.precio_cobrado == 40_000  # el cobro de antes guarda su copia
    precios = sesion.scalars(
        select(ServicioPrecio).where(
            ServicioPrecio.servicio_id == basico, ServicioPrecio.tipo_vehiculo_id == automovil
        )
    ).all()
    assert sorted((p.precio, p.activo) for p in precios) == [(40_000, False), (45_000, True)]
    assert datos.precio_vigente(sesion, basico, automovil).duracion_min == 50


def test_el_mismo_precio_no_crea_otro_registro(sesion: Session):
    basico = servicio(sesion, "Básico").id
    moto = tipo_vehiculo(sesion, "Moto").id
    basico_moto = servicio(sesion, "Básico", "Motos").id

    assert (
        configuracion.cambiar_precio(sesion, basico_moto, moto, precio=18_000, duracion_min=60)
        is False
    )
    with pytest.raises(ConfiguracionInvalida):
        configuracion.cambiar_precio(sesion, basico, moto, precio=-1, duracion_min=60)
    with pytest.raises(ConfiguracionInvalida):
        configuracion.cambiar_precio(sesion, basico_moto, moto, precio=18_000, duracion_min=0)


def test_agregar_un_servicio_con_precio_para_cada_vehiculo_de_la_fila(sesion: Session):
    carros = fila(sesion, "Carros").id
    automovil, camioneta = (
        tipo_vehiculo(sesion, "Automóvil").id,
        tipo_vehiculo(sesion, "Camioneta").id,
    )

    nuevo = configuracion.agregar_servicio(
        sesion,
        fila_id=carros,
        nombre=" lavado de motor ",
        descripcion="Desengrase y brillo del motor",
        precios={automovil: 30_000, camioneta: 35_000},
        duracion_min=40,
    )

    assert nuevo.nombre == "Lavado de motor"
    assert datos.precio_vigente(sesion, nuevo.id, camioneta).precio == 35_000
    with pytest.raises(ConfiguracionInvalida, match="revisa") as repetido:
        configuracion.agregar_servicio(
            sesion,
            fila_id=carros,
            nombre="LAVADO DE MOTOR",
            descripcion="",
            precios={automovil: 1, camioneta: 1},
            duracion_min=40,
        )
    assert "nombre" in repetido.value.errores


def test_un_servicio_nuevo_necesita_todos_sus_precios(sesion: Session):
    carros = fila(sesion, "Carros").id
    automovil = tipo_vehiculo(sesion, "Automóvil").id

    with pytest.raises(ConfiguracionInvalida):
        configuracion.agregar_servicio(
            sesion,
            fila_id=carros,
            nombre="Polichado",
            descripcion="",
            precios={automovil: 80_000},  # falta el de camioneta
            duracion_min=90,
        )


# --- Puestos ----------------------------------------------------------------------------------


def test_un_puesto_nuevo_llama_de_una_vez_al_que_espera(sesion: Session):
    ocupar_puestos(sesion, "C1", "C2")
    espera = por_whatsapp(sesion)
    assert estado(sesion, espera.turno.id) == EstadoTurno.EN_FILA

    nuevo = casos.agregar_puesto(sesion, fila(sesion, "Carros").id, ahora=LUNES_8AM)

    assert nuevo.nombre == "C3" and nuevo.activo
    assert estado(sesion, espera.turno.id) == EstadoTurno.LLAMADO


def test_no_se_apaga_un_puesto_con_un_vehiculo_lavandose(sesion: Session):
    ocupar_puestos(sesion, "C1")

    with pytest.raises(PuestoOcupado, match="C1"):
        casos.cambiar_puesto(sesion, puesto(sesion, "C1").id, activo=False)


def test_apagar_y_prender_un_puesto(sesion: Session):
    c2 = puesto(sesion, "C2")
    ocupar_puestos(sesion, "C1")

    assert casos.cambiar_puesto(sesion, c2.id, activo=False, ahora=LUNES_8AM) is True
    espera = por_whatsapp(sesion)  # sin puestos libres: queda en fila
    assert estado(sesion, espera.turno.id) == EstadoTurno.EN_FILA
    assert [p.nombre for p in datos.puestos_activos(sesion, c2.fila_id)] == ["C1"]

    assert casos.cambiar_puesto(sesion, c2.id, activo=True, ahora=LUNES_8AM) is True
    assert estado(sesion, espera.turno.id) == EstadoTurno.LLAMADO
    assert casos.cambiar_puesto(sesion, c2.id, activo=True) is False  # ya estaba prendido
    assert sesion.get(Puesto, c2.id).activo


# --- Duración real -----------------------------------------------------------------------------


def test_la_duracion_real_va_del_puesto_a_listo(sesion: Session):
    encerado = servicio(sesion, "Encerado").id
    for duracion in (40, 60):
        nuevo = en_el_local(sesion, servicio_id=encerado)
        casos.pasar_a_puesto(sesion, nuevo.turno.id, puesto(sesion, "C1").id, ahora=LUNES_8AM)
        casos.marcar_listo(sesion, nuevo.turno.id, ahora=LUNES_8AM + minutos(duracion))

    reales = consultas.duracion_real_por_servicio(sesion, desde=LUNES_8AM - timedelta(days=30))

    assert reales[encerado] == (50, 2)
    assert consultas.duracion_real_por_servicio(sesion, desde=LUNES_8AM + timedelta(days=1)) == {}
