"""La bandeja de salida: cada aviso sale una sola vez y a cada celular en orden; si
WhatsApp no lo acepta, se reintenta cada vez más tarde, y si ya no sirve, no se envía.

El WhatsApp de estas pruebas es de mentira. El de verdad (app.integraciones.whatsapp) se
prueba en test_whatsapp.py, con un Meta de mentira.
"""

import logging
import threading
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.integraciones import bandeja_salida
from app.integraciones.bandeja_salida import EnvioFallido, Resultado
from app.integraciones.tablas import EstadoMensaje, MensajeSaliente
from app.reloj import proceso, tareas
from app.turnos import avisos as avisos_de_la_fila
from app.turnos import casos_de_uso as casos
from app.turnos import reglas
from app.turnos.estados import Actor, EstadoTurno, MedioPago, TipoAviso
from pruebas.fabricas import (
    LUNES_8AM,
    avisos,
    debe_rechazar,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    turno_listo,
)

E = EstadoTurno


class WhatsAppDeMentira:
    """Contesta lo que se le pida: un id de WhatsApp, o un error."""

    def __init__(self, *respuestas: str | Exception) -> None:
        self.respuestas = list(respuestas)
        self.enviados: list[int] = []

    def enviar(self, _sesion: Session, mensaje: MensajeSaliente) -> str:
        self.enviados.append(mensaje.id)
        respuesta = self.respuestas.pop(0) if self.respuestas else f"wamid.{mensaje.id}"
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta


def siempre_sirve(_sesion: Session, _mensaje: MensajeSaliente, _ahora: datetime) -> bool:
    return True


def dejar_mensaje(
    sesion: Session, *, ahora: datetime = LUNES_8AM, telefono: str = "573001112233", **datos: Any
) -> MensajeSaliente:
    mensaje = bandeja_salida.encolar(
        sesion,
        telefono=telefono,
        tipo=datos.get("tipo", "prueba"),
        contenido=datos.get("contenido", {}),
        ahora=ahora,
        turno_id=datos.get("turno_id"),
    )
    sesion.flush()
    return mensaje


def enviar(
    sesion: Session, whatsapp: WhatsAppDeMentira, ahora: datetime = LUNES_8AM, sirve=siempre_sirve
) -> Resultado | None:
    return bandeja_salida.enviar_siguiente(sesion, whatsapp, ahora=ahora, sigue_vigente=sirve)


# --- Enviar -------------------------------------------------------------------------------


def test_sin_mensajes_por_enviar_no_hace_nada(sesion: Session):
    assert enviar(sesion, WhatsAppDeMentira()) is None


def test_sale_primero_el_mas_antiguo_y_queda_anotado(sesion: Session):
    nuevo = dejar_mensaje(sesion, telefono="573001112233")
    antiguo = dejar_mensaje(sesion, ahora=LUNES_8AM - minutos(1), telefono="573004445566")
    whatsapp = WhatsAppDeMentira("wamid.ABC")

    assert enviar(sesion, whatsapp) == Resultado.ENVIADO

    assert whatsapp.enviados == [antiguo.id]
    assert antiguo.estado == EstadoMensaje.ENVIADO
    assert (antiguo.id_meta, antiguo.enviado_en, antiguo.intentos) == ("wamid.ABC", LUNES_8AM, 1)
    assert nuevo.estado == EstadoMensaje.PENDIENTE


def test_a_cada_celular_le_llegan_sus_mensajes_en_orden(sesion: Session):
    primero = dejar_mensaje(sesion)
    segundo = dejar_mensaje(sesion)
    de_otro = dejar_mensaje(sesion, telefono="573004445566")
    whatsapp = WhatsAppDeMentira(EnvioFallido("WhatsApp no responde", reintentar=True))

    assert enviar(sesion, whatsapp) == Resultado.REINTENTO  # el primero espera reintento...
    assert enviar(sesion, whatsapp) == Resultado.ENVIADO
    assert enviar(sesion, whatsapp) is None  # ...y el segundo lo espera a él

    assert whatsapp.enviados == [primero.id, de_otro.id]  # a otro celular no lo atrasa
    ahora = primero.proximo_intento_en
    assert [enviar(sesion, whatsapp, ahora), enviar(sesion, whatsapp, ahora)] == [
        Resultado.ENVIADO,
        Resultado.ENVIADO,
    ]
    assert whatsapp.enviados[2:] == [primero.id, segundo.id]


def test_un_mensaje_que_todavia_no_toca_espera(sesion: Session):
    dejar_mensaje(sesion, ahora=LUNES_8AM + minutos(1))

    assert enviar(sesion, WhatsAppDeMentira()) is None


def test_si_whatsapp_falla_un_momento_se_reintenta_cada_vez_mas_tarde(sesion: Session):
    mensaje = dejar_mensaje(sesion)
    whatsapp = WhatsAppDeMentira(*[EnvioFallido("WhatsApp no responde", reintentar=True)] * 6)
    ahora, esperas, resultados = LUNES_8AM, [], []

    for _ in range(6):
        resultados.append(enviar(sesion, whatsapp, ahora))
        esperas.append(mensaje.proximo_intento_en - ahora)
        ahora = mensaje.proximo_intento_en

    assert resultados == [Resultado.REINTENTO] * 5 + [Resultado.FALLIDO]
    assert esperas[:5] == list(bandeja_salida.ESPERAS)
    assert [int(espera.total_seconds()) for espera in bandeja_salida.ESPERAS] == [
        30,
        60,
        120,
        300,
        600,
    ]
    assert mensaje.estado == EstadoMensaje.FALLIDO
    assert mensaje.intentos == 6
    assert mensaje.ultimo_error == "WhatsApp no responde"


def test_si_el_problema_no_se_arregla_solo_no_se_insiste(sesion: Session):
    mensaje = dejar_mensaje(sesion)
    whatsapp = WhatsAppDeMentira(EnvioFallido("El número no tiene WhatsApp", reintentar=False))

    assert enviar(sesion, whatsapp) == Resultado.FALLIDO
    assert (mensaje.estado, mensaje.intentos) == (EstadoMensaje.FALLIDO, 1)


def test_un_error_del_programa_no_tranca_la_bandeja(sesion: Session, caplog):
    roto = dejar_mensaje(sesion, ahora=LUNES_8AM - minutos(1))
    otro = dejar_mensaje(sesion)  # para el mismo celular
    whatsapp = WhatsAppDeMentira(KeyError("texto_llamado"))

    assert enviar(sesion, whatsapp) == Resultado.FALLIDO  # reintentar no lo arregla
    assert enviar(sesion, whatsapp) == Resultado.ENVIADO  # el siguiente sale igual

    assert whatsapp.enviados == [roto.id, otro.id]
    assert (roto.estado, roto.intentos) == (EstadoMensaje.FALLIDO, 1)
    assert "KeyError" in roto.ultimo_error
    assert f"Error inesperado al enviar el mensaje {roto.id}." in caplog.messages


def test_un_aviso_que_ya_no_sirve_se_descarta_sin_enviarlo(sesion: Session):
    mensaje = dejar_mensaje(sesion)
    whatsapp = WhatsAppDeMentira()

    assert enviar(sesion, whatsapp, sirve=lambda *_: False) == Resultado.DESCARTADO

    assert whatsapp.enviados == []
    assert (mensaje.estado, mensaje.intentos) == (EstadoMensaje.DESCARTADO, 0)


def test_la_base_no_acepta_un_mensaje_enviado_sin_fecha(sesion: Session):
    mensaje = dejar_mensaje(sesion)
    with debe_rechazar(sesion, "enviado_con_fecha"):
        mensaje.estado = EstadoMensaje.ENVIADO


def test_el_reloj_envia_lo_pendiente_y_resume_como_le_fue(sesion: Session, conexion, caplog):
    caplog.set_level(logging.INFO, logger="app.reloj")
    for telefono in ("573001112233", "573004445566", "573007778899"):
        dejar_mensaje(sesion, telefono=telefono)
    whatsapp = WhatsAppDeMentira("wamid.1", EnvioFallido("caído", reintentar=True))
    reloj = proceso.Reloj(
        [tareas.enviar_mensajes(whatsapp)],
        lambda: Session(bind=conexion, join_transaction_mode="create_savepoint"),
    )

    reloj.una_vuelta(LUNES_8AM)

    assert caplog.messages == [
        "Reloj: 2 mensajes enviados.",
        "Reloj: 1 mensaje no salió: se reintenta.",
    ]


# --- Dos relojes a la vez ---------------------------------------------------------------------


@pytest.fixture
def base(motor: Engine) -> Iterator[Engine]:
    """Para pruebas que confirman de verdad: al terminar se borra lo que crearon."""
    yield motor
    with motor.begin() as conexion:
        conexion.execute(text("TRUNCATE cliente, administrador, mensaje_entrante CASCADE"))


def test_un_mensaje_que_otro_reloj_esta_enviando_no_se_envia_dos_veces(base: Engine):
    with Session(base) as sesion, sesion.begin():
        dejar_mensaje(sesion)
    dentro, soltar = threading.Event(), threading.Event()

    class WhatsAppLento(WhatsAppDeMentira):
        def enviar(self, sesion: Session, mensaje: MensajeSaliente) -> str:
            dentro.set()
            soltar.wait(timeout=10)
            return super().enviar(sesion, mensaje)

    lento, rapido = WhatsAppLento(), WhatsAppDeMentira()
    resultados: list[Resultado | None] = []

    def primer_reloj() -> None:
        with Session(base) as sesion, sesion.begin():
            resultados.append(enviar(sesion, lento))

    hilo = threading.Thread(target=primer_reloj)
    hilo.start()
    assert dentro.wait(timeout=10)  # el primero lo tiene bloqueado mientras lo envía
    with Session(base) as sesion, sesion.begin():
        assert enviar(sesion, rapido) is None  # el segundo se lo salta
    soltar.set()
    hilo.join(timeout=10)

    assert resultados == [Resultado.ENVIADO]
    assert rapido.enviados == []


def test_mientras_otro_reloj_envia_un_mensaje_el_siguiente_del_mismo_celular_espera(base: Engine):
    with Session(base) as sesion, sesion.begin():
        primero = dejar_mensaje(sesion).id
        segundo = dejar_mensaje(sesion).id
        de_otro = dejar_mensaje(sesion, telefono="573004445566").id
    dentro, soltar = threading.Event(), threading.Event()

    class WhatsAppLento(WhatsAppDeMentira):
        def enviar(self, sesion: Session, mensaje: MensajeSaliente) -> str:
            dentro.set()
            soltar.wait(timeout=10)
            return super().enviar(sesion, mensaje)

    lento, rapido = WhatsAppLento(), WhatsAppDeMentira()

    def primer_reloj() -> None:
        with Session(base) as sesion, sesion.begin():
            enviar(sesion, lento)

    hilo = threading.Thread(target=primer_reloj)
    hilo.start()
    assert dentro.wait(timeout=10)  # el primero está saliendo
    with Session(base) as sesion, sesion.begin():
        assert enviar(sesion, rapido) == Resultado.ENVIADO  # el de otro celular sale
        assert enviar(sesion, rapido) is None  # el segundo espera a que salga el primero
    soltar.set()
    hilo.join(timeout=10)
    with Session(base) as sesion, sesion.begin():
        assert enviar(sesion, rapido) == Resultado.ENVIADO

    assert lento.enviados == [primero]
    assert rapido.enviados == [de_otro, segundo]


# --- ¿Todavía sirve el aviso? -----------------------------------------------------------------


def aviso_de(sesion: Session, turno_id: int, tipo: TipoAviso) -> MensajeSaliente:
    return avisos(sesion, turno_id=turno_id, tipo=tipo)[-1]


def sirve(sesion: Session, mensaje: MensajeSaliente, ahora: datetime) -> bool:
    return avisos_de_la_fila.sigue_vigente(sesion, mensaje, ahora)


def llamado_con_aviso(sesion: Session) -> int:
    """Un turno que se llamó con aviso (no al pedirlo): esperaba y se liberó un puesto."""
    lavando = ocupar_puestos(sesion, "C1", "C2")[0]
    turno = por_whatsapp(sesion).turno.id
    casos.marcar_listo(sesion, lavando, ahora=LUNES_8AM)
    return turno


def test_el_aviso_del_llamado_no_sale_si_el_cliente_ya_llego(sesion: Session):
    turno = llamado_con_aviso(sesion)
    llamado = aviso_de(sesion, turno, TipoAviso.LLAMADO)
    assert sirve(sesion, llamado, LUNES_8AM + minutos(1))

    casos.marcar_llegada(sesion, turno, actor=Actor.CLIENTE, ahora=LUNES_8AM + minutos(2))

    assert not sirve(sesion, llamado, LUNES_8AM + minutos(3))


def test_el_aviso_del_llamado_no_sale_despues_de_la_hora_limite(sesion: Session):
    turno = llamado_con_aviso(sesion)  # tiene hasta las 8:15
    llamado = aviso_de(sesion, turno, TipoAviso.LLAMADO)

    assert sirve(sesion, llamado, LUNES_8AM + minutos(14))
    assert not sirve(sesion, llamado, LUNES_8AM + minutos(15))


def test_el_aviso_de_un_llamado_anterior_no_sale(sesion: Session):
    turno = llamado_con_aviso(sesion)  # llamado hasta las 8:15
    primer_llamado = aviso_de(sesion, turno, TipoAviso.LLAMADO)
    # No llega: pasa al final y, como no hay nadie más, lo llaman otra vez hasta las 8:30.
    casos.vencer_gracias(sesion, ahora=LUNES_8AM + minutos(15))
    segundo_llamado = aviso_de(sesion, turno, TipoAviso.LLAMADO)
    paso_al_final = aviso_de(sesion, turno, TipoAviso.PASA_AL_FINAL)

    ahora = LUNES_8AM + minutos(16)
    assert not sirve(sesion, primer_llamado, ahora)  # decía "hasta las 8:15"
    assert sirve(sesion, segundo_llamado, ahora)
    assert sirve(sesion, paso_al_final, ahora)


@pytest.mark.usefixtures("caja_del_lavadero")
def test_el_aviso_de_vehiculo_listo_sirve_hasta_que_se_cobra(sesion: Session):
    turno = turno_listo(sesion)
    listo = aviso_de(sesion, turno, TipoAviso.VEHICULO_LISTO)
    assert sirve(sesion, listo, LUNES_8AM + minutos(55))

    casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=LUNES_8AM + minutos(56))

    assert not sirve(sesion, listo, LUNES_8AM + minutos(57))


def test_un_mensaje_que_no_es_de_la_fila_siempre_sirve(sesion: Session):
    assert sirve(sesion, dejar_mensaje(sesion, tipo="respuesta_del_bot"), LUNES_8AM)


@pytest.mark.parametrize(
    ("tipo", "estado", "sirve_todavia"),
    [
        (TipoAviso.AVISO_ANTICIPADO, E.EN_FILA, True),
        (TipoAviso.AVISO_ANTICIPADO, E.LLAMADO, False),  # ya le llegó el del llamado
        (TipoAviso.PASA_AL_FINAL, E.EN_FILA, True),
        (TipoAviso.PASA_AL_FINAL, E.LLAMADO, True),  # nadie más esperaba: lo llamaron otra vez
        (TipoAviso.PASA_AL_FINAL, E.LLEGO, False),
        (TipoAviso.NO_LLEGO, E.NO_LLEGO, True),
        (TipoAviso.VEHICULO_LISTO, E.LISTO, True),
        (TipoAviso.VEHICULO_LISTO, E.PAGADO, False),
        (TipoAviso.LLAMADO, E.CANCELADO, False),
    ],
)
def test_cada_aviso_sirve_mientras_el_turno_siga_donde_estaba(tipo, estado, sirve_todavia):
    gracia = LUNES_8AM + minutos(15)
    assert (
        reglas.aviso_sigue_vigente(
            tipo, estado, gracia_hasta=gracia, gracia_del_aviso=gracia, ahora=LUNES_8AM
        )
        is sirve_todavia
    )


def test_cada_tipo_de_aviso_dice_en_que_estados_sirve():
    # Si se agrega un aviso nuevo y nadie dice cuándo sirve, la bandeja no sabría qué
    # hacer con él: esta prueba lo avisa antes.
    assert set(reglas.ESTADOS_DONDE_SIRVE_EL_AVISO) == set(TipoAviso)
