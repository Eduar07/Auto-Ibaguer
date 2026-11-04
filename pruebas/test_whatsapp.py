"""WhatsApp, con mensajes de Meta de mentira: lo que llega al webhook, lo que el bot
contesta y cómo sale por la Cloud API.

Meta no se toca: los avisos que "manda Meta" se arman aquí y se firman con una clave de
prueba, y lo que el bot "envía" lo recibe un Meta de mentira (httpx.MockTransport) que
anota cada mensaje. Todo lo demás es de verdad: la base, el bot y la bandeja de salida.
"""

import hashlib
import hmac
import itertools
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app import cli
from app.bot import conversacion
from app.bot.mensajes import Lista, Mensaje, Opcion
from app.comentarios.tablas import Comentario
from app.config import obtener_entorno
from app.integraciones import bandeja_entrada, bandeja_salida, whatsapp
from app.integraciones.bandeja_salida import Resultado
from app.integraciones.tablas import EstadoMensaje, MensajeEntrante, MensajeSaliente
from app.main import app
from app.reloj import proceso, tareas
from app.turnos import avisos
from app.turnos import casos_de_uso as casos
from app.turnos.estados import EstadoTurno, OrigenTurno
from app.turnos.tablas import Turno
from app.web import webhook
from pruebas.fabricas import (
    LUNES_8AM,
    cliente_con_vehiculo,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    tipo_vehiculo,
)
from pruebas.navegador import HTMX

CELULAR = "573001234567"
ID_DEL_NUMERO = "109876543210"
ID_DE_LA_CUENTA = "205551234567890"
SECRETO = "secreto-de-la-app-de-prueba"
_ids = itertools.count(1)


class MetaDeMentira:
    """Hace de la Cloud API: anota lo que le envían y contesta lo que se le pida."""

    def __init__(self) -> None:
        self.enviados: list[dict[str, Any]] = []
        self.respuestas: list[httpx.Response | Exception] = []

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        assert peticion.headers["Authorization"] == "Bearer token-de-prueba"
        assert peticion.url.path == f"/v26.0/{ID_DEL_NUMERO}/messages"
        if self.respuestas:
            respuesta = self.respuestas.pop(0)
            if isinstance(respuesta, Exception):
                raise respuesta
            return respuesta
        self.enviados.append(json.loads(peticion.content))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(self.enviados)}"}]})

    @property
    def textos(self) -> list[str]:
        return [_cuerpo(enviado) for enviado in self.enviados]


def _cuerpo(enviado: dict[str, Any]) -> str:
    if enviado["type"] == "text":
        return enviado["text"]["body"]
    return enviado["interactive"]["body"]["text"]


def error_de_meta(estado: int, codigo: int, detalle: str = "") -> httpx.Response:
    error = {"message": "error", "code": codigo, "error_data": {"details": detalle}}
    return httpx.Response(estado, json={"error": error})


@pytest.fixture
def meta() -> MetaDeMentira:
    return MetaDeMentira()


@pytest.fixture
def abrir(conexion: Connection) -> Callable[[], Session]:
    return lambda: Session(bind=conexion, join_transaction_mode="create_savepoint")


def _poner_en_el_entorno(monkeypatch: pytest.MonkeyPatch, **valores: str) -> None:
    entorno = obtener_entorno()
    for campo, valor in valores.items():
        monkeypatch.setattr(entorno, campo, valor)


@pytest.fixture
def conectado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Como si el .env tuviera los datos de WhatsApp (de mentira)."""
    _poner_en_el_entorno(
        monkeypatch,
        whatsapp_token="token-de-prueba",
        whatsapp_id_numero=ID_DEL_NUMERO,
        whatsapp_id_cuenta=ID_DE_LA_CUENTA,
        whatsapp_secreto_app=SECRETO,
        whatsapp_token_verificacion="palabra-de-prueba",
        whatsapp_version_api="v26.0",
    )


@pytest.fixture
def desconectado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Como si el .env no tuviera nada de WhatsApp (así está hasta que llegue la SIM)."""
    _poner_en_el_entorno(
        monkeypatch,
        whatsapp_token="",
        whatsapp_id_numero="",
        whatsapp_id_cuenta="",
        whatsapp_secreto_app="",
        whatsapp_token_verificacion="",
    )


@pytest.fixture
def enviador(conectado, meta: MetaDeMentira) -> whatsapp.WhatsApp:
    return whatsapp.WhatsApp(
        obtener_entorno(), http=httpx.Client(transport=httpx.MockTransport(meta))
    )


@pytest.fixture
def webhook_listo(web: TestClient, enviador, abrir) -> Iterator[TestClient]:
    """El webhook del servidor, con el Meta de mentira y la base de la prueba."""
    app.dependency_overrides[webhook.obtener_enviador] = lambda: enviador
    app.dependency_overrides[webhook.obtener_abrir] = lambda: abrir
    yield web


def aviso_de_meta(
    *mensajes: dict[str, Any], estados: list[dict] | None = None, numero=ID_DEL_NUMERO
):
    valor: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "573227556850", "phone_number_id": numero},
    }
    if mensajes:
        valor["contacts"] = [{"profile": {"name": "Ana"}, "wa_id": CELULAR}]
        valor["messages"] = list(mensajes)
    if estados:
        valor["statuses"] = estados
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": valor}]}],
    }


def escribe(texto: str, telefono: str = CELULAR, id_meta: str | None = None) -> dict[str, Any]:
    return {
        "from": telefono,
        "id": id_meta or f"wamid.entrante.{next(_ids)}",
        "timestamp": "1793000000",
        "type": "text",
        "text": {"body": texto},
    }


def toca(id_del_boton: str, titulo: str = "", lista: bool = False) -> dict[str, Any]:
    tipo = "list_reply" if lista else "button_reply"
    return {
        "from": CELULAR,
        "id": f"wamid.entrante.{next(_ids)}",
        "timestamp": "1793000000",
        "type": "interactive",
        "interactive": {"type": tipo, tipo: {"id": id_del_boton, "title": titulo}},
    }


def firmar(cuerpo: bytes, secreto: str = SECRETO) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def mandar(web: TestClient, aviso: dict[str, Any], *, firma: str | None = None) -> int:
    cuerpo = json.dumps(aviso).encode()
    encabezados = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": firma or firmar(cuerpo),
    }
    return web.post("/webhook/whatsapp", content=cuerpo, headers=encabezados).status_code


def entrantes(sesion: Session) -> list[MensajeEntrante]:
    consulta = select(MensajeEntrante).order_by(MensajeEntrante.id)
    return list(sesion.scalars(consulta.execution_options(populate_existing=True)))


def salientes(sesion: Session) -> list[MensajeSaliente]:
    consulta = select(MensajeSaliente).order_by(MensajeSaliente.id)
    return list(sesion.scalars(consulta.execution_options(populate_existing=True)))


# --- La puerta de entrada ----------------------------------------------------------------------


def test_meta_verifica_el_webhook_con_el_token(web: TestClient, conectado):
    bien = {
        "hub.mode": "subscribe",
        "hub.verify_token": "palabra-de-prueba",
        "hub.challenge": "8812",
    }

    respuesta = web.get("/webhook/whatsapp", params=bien)
    assert (respuesta.status_code, respuesta.text) == (200, "8812")
    otro_token = {**bien, "hub.verify_token": "otra"}
    assert web.get("/webhook/whatsapp", params=otro_token).status_code == 403


def test_un_aviso_sin_la_firma_de_meta_se_descarta(webhook_listo: TestClient, sesion: Session):
    aviso = aviso_de_meta(escribe("hola"))

    assert mandar(webhook_listo, aviso, firma="sha256=" + "0" * 64) == 403
    assert mandar(webhook_listo, aviso, firma=firmar(json.dumps(aviso).encode(), "otra")) == 403
    assert entrantes(sesion) == []


def test_sin_whatsapp_configurado_el_webhook_no_recibe(web: TestClient, desconectado):
    assert mandar(web, aviso_de_meta(escribe("hola"))) == 503
    verificar = {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}
    assert web.get("/webhook/whatsapp", params=verificar).status_code == 403


def test_un_aviso_mal_armado_o_gigante_se_rechaza(webhook_listo: TestClient, sesion: Session):
    def mandar_crudo(cuerpo: bytes) -> int:
        encabezados = {"Content-Type": "application/json", "X-Hub-Signature-256": firmar(cuerpo)}
        return webhook_listo.post(
            "/webhook/whatsapp", content=cuerpo, headers=encabezados
        ).status_code

    assert mandar_crudo(b"no es json") == 400
    assert mandar_crudo(b"[1, 2, 3]") == 400
    assert mandar_crudo(b" " * (webhook.TAMANO_MAXIMO_DEL_AVISO + 1)) == 413
    assert entrantes(sesion) == []


def test_lo_que_no_es_para_nuestro_numero_se_ignora(webhook_listo: TestClient, sesion, meta):
    assert mandar(webhook_listo, aviso_de_meta(escribe("hola"), numero="999")) == 200
    assert entrantes(sesion) == [] and meta.enviados == []


# --- Conversar por WhatsApp -------------------------------------------------------------------


def test_el_cliente_escribe_hola_y_el_bot_le_contesta_por_whatsapp(
    webhook_listo: TestClient, sesion: Session, meta: MetaDeMentira
):
    assert mandar(webhook_listo, aviso_de_meta(escribe("hola"))) == 200

    [recibido] = entrantes(sesion)
    assert (recibido.telefono, recibido.contenido) == (CELULAR, {"texto": "hola"})
    assert recibido.procesado_en == LUNES_8AM
    [enviado] = meta.enviados
    assert enviado["to"] == CELULAR and enviado["type"] == "interactive"
    assert enviado["interactive"]["body"]["text"].startswith("¡Hola! Bienvenido a Auto Ibaguer.")
    assert [b["reply"] for b in enviado["interactive"]["action"]["buttons"]] == [
        {"id": "menu.pedir", "title": "Pedir turno"},
        {"id": "menu.fila", "title": "Ver la fila"},
        {"id": "menu.precios", "title": "Servicios y precios"},
    ]
    [respuesta] = salientes(sesion)
    assert (respuesta.tipo, respuesta.estado, respuesta.id_meta) == (
        "respuesta",
        EstadoMensaje.ENVIADO,
        "wamid.1",
    )


def test_un_turno_completo_desde_whatsapp_solo_con_botones(
    webhook_listo: TestClient, sesion: Session, meta: MetaDeMentira
):
    automovil = tipo_vehiculo(sesion, "Automóvil").id
    pasos = [
        escribe("hola"),
        toca("menu.pedir", "Pedir turno"),
        toca("datos.si", "Acepto"),
        escribe("Ana Ruiz"),
        toca(f"vehiculo.tipo.{automovil}", "Automóvil"),
        toca("placa.no", "Ahora no"),
        toca("servicio.local", "Lo decido en el local", lista=True),
        toca("confirmar.si", "Confirmar turno"),
    ]
    for paso in pasos:
        assert mandar(webhook_listo, aviso_de_meta(paso)) == 200

    lista = meta.enviados[5]
    assert lista["interactive"]["type"] == "list"
    assert lista["interactive"]["action"]["button"] == "Ver servicios"
    filas = lista["interactive"]["action"]["sections"][0]["rows"]
    assert filas[0]["title"] == "Básico" and filas[0]["description"].startswith("$40.000")
    assert "Tu turno es *C-01*." in meta.textos[-1]
    [turno] = sesion.scalars(select(Turno)).all()
    assert (turno.origen, turno.estado) == (OrigenTurno.WHATSAPP, EstadoTurno.LLAMADO)
    assert all(m.procesado_en is not None for m in entrantes(sesion))


def test_si_meta_repite_un_mensaje_se_atiende_una_sola_vez(
    webhook_listo: TestClient, sesion: Session, meta: MetaDeMentira
):
    mensaje = escribe("hola", id_meta="wamid.repetido")

    for _ in range(2):
        assert mandar(webhook_listo, aviso_de_meta(mensaje)) == 200

    assert len(entrantes(sesion)) == 1
    assert len(meta.enviados) == 1


def test_dos_mensajes_seguidos_se_contestan_en_orden(
    webhook_listo: TestClient, sesion: Session, meta: MetaDeMentira
):
    mandar(webhook_listo, aviso_de_meta(toca("menu.precios"), escribe("carro")))

    assert meta.textos == [
        "¿Para qué vehículo quieres ver los precios?",
        "¿Automóvil o camioneta?",
    ]


def test_una_foto_recibe_el_menu_y_no_se_guarda_nada_de_ella(
    webhook_listo: TestClient, sesion: Session, meta: MetaDeMentira
):
    foto = {
        "from": CELULAR,
        "id": "wamid.foto",
        "type": "image",
        "image": {"id": "media-123", "mime_type": "image/jpeg"},
    }

    mandar(webhook_listo, aviso_de_meta(foto))

    [recibido] = entrantes(sesion)
    assert (recibido.tipo, recibido.contenido) == ("image", {})
    assert "solo entiendo mensajes escritos y los botones" in meta.textos[0]


def test_una_reaccion_no_se_contesta(webhook_listo: TestClient, sesion: Session, meta):
    reaccion = {"from": CELULAR, "id": "wamid.r", "type": "reaction", "reaction": {"emoji": "👍"}}

    mandar(webhook_listo, aviso_de_meta(reaccion))

    [recibido] = entrantes(sesion)
    assert recibido.procesado_en is not None
    assert meta.enviados == []


def test_un_mensaje_que_hace_fallar_al_bot_no_se_queda_atascado(
    webhook_listo: TestClient, sesion: Session, meta: MetaDeMentira, monkeypatch, caplog
):
    atender_de_verdad = conversacion.atender
    llamadas = itertools.count()

    def falla_la_primera(*argumentos, **opciones):
        if next(llamadas) == 0:
            raise RuntimeError("se rompió algo")
        return atender_de_verdad(*argumentos, **opciones)

    monkeypatch.setattr(conversacion, "atender", falla_la_primera)

    mandar(webhook_listo, aviso_de_meta(escribe("hola"), escribe("mi turno")))

    primero, segundo = entrantes(sesion)
    assert "se rompió algo" in primero.error and primero.procesado_en is not None
    assert segundo.error is None and segundo.procesado_en is not None
    assert meta.textos[0].startswith("Tuvimos un problema para atenderte.")
    assert meta.textos[1] == "No tienes turnos activos. ¿Quieres pedir uno?"
    assert "El bot no pudo atender un mensaje de WhatsApp." in caplog.messages


def test_lo_que_quedo_sin_atender_lo_retoma_el_reloj(
    sesion: Session, abrir, enviador, meta: MetaDeMentira, reloj, caplog
):
    caplog.set_level(logging.INFO, logger="app.reloj")
    recibido = whatsapp.Recibido("wamid.olvidado", CELULAR, "text", texto="hola")
    bandeja_entrada.guardar(sesion, [recibido], ahora=LUNES_8AM)  # el servidor se cayó aquí
    el_reloj = proceso.Reloj(tareas.del_servidor(enviador), abrir)

    el_reloj.una_vuelta(LUNES_8AM + minutos(0))
    assert meta.enviados == []  # todavía lo puede estar atendiendo el webhook
    reloj["ahora"] = LUNES_8AM + minutos(2)
    el_reloj.una_vuelta(LUNES_8AM + minutos(2))

    assert meta.textos[0].startswith("¡Hola! Bienvenido a Auto Ibaguer.")
    [olvidado] = entrantes(sesion)
    assert olvidado.procesado_en == LUNES_8AM + minutos(2)
    assert (
        "Reloj: 1 mensaje de WhatsApp que se habían quedado sin atender ya se atendieron."
        in caplog.messages
    )


# --- Los avisos de la fila y los errores de WhatsApp ----------------------------------------------


def aviso_de_llamado(sesion: Session) -> Turno:
    """Un cliente que pidió turno por WhatsApp y al que llaman cuando se libera un puesto."""
    sesion.add(
        MensajeEntrante(
            id_meta="wamid.x", telefono=CELULAR, tipo="text", contenido={}, recibido_en=LUNES_8AM
        )
    )
    lavando = ocupar_puestos(sesion, "C1", "C2")[0]
    nuevo = por_whatsapp(sesion, vehiculo=cliente_con_vehiculo(sesion, telefono=CELULAR))
    casos.marcar_listo(sesion, lavando, ahora=LUNES_8AM + minutos(30))
    return sesion.get(Turno, nuevo.turno.id)


def enviar(abrir, enviador) -> dict[Resultado, int]:
    cuenta = bandeja_salida.enviar_pendientes(
        abrir, enviador, ahora=LUNES_8AM + minutos(30), sigue_vigente=avisos.sigue_vigente
    )
    return dict(cuenta)


def avisos_a(sesion: Session, telefono: str = CELULAR) -> list[MensajeSaliente]:
    return [m for m in salientes(sesion) if m.telefono == telefono]


def test_el_aviso_de_llamado_sale_con_sus_botones(sesion: Session, abrir, enviador, meta, reloj):
    reloj["ahora"] = LUNES_8AM + minutos(30)
    turno = aviso_de_llamado(sesion)

    enviar(abrir, enviador)

    [llamado] = [e for e in meta.enviados if e["to"] == CELULAR]
    assert llamado["interactive"]["body"]["text"].startswith("¡Es tu turno! Ya hay un puesto libre")
    assert "Tienes hasta las 8:45 a.m. para llegar" in llamado["interactive"]["body"]["text"]
    assert [b["reply"]["title"] for b in llamado["interactive"]["action"]["buttons"]] == [
        "En camino",
        "Ya estoy aquí",
    ]
    assert turno.estado == EstadoTurno.LLAMADO


def test_sin_escribir_en_24_horas_whatsapp_no_deja_escribirle(
    sesion: Session, abrir, enviador, meta, reloj
):
    reloj["ahora"] = LUNES_8AM + minutos(30)
    aviso_de_llamado(sesion)
    reloj["ahora"] = LUNES_8AM + minutos(30) + minutos(24 * 60)  # ya pasaron más de 24 horas

    enviar(abrir, enviador)

    [aviso] = avisos_a(sesion)
    assert aviso.estado == EstadoMensaje.FALLIDO
    assert aviso.ultimo_error.startswith("No le ha escrito al bot en las últimas 24 horas")
    assert [e for e in meta.enviados if e["to"] == CELULAR] == []


def test_si_whatsapp_falla_un_momento_se_reintenta(sesion: Session, abrir, enviador, meta, reloj):
    reloj["ahora"] = LUNES_8AM + minutos(30)
    aviso_de_llamado(sesion)
    meta.respuestas = [error_de_meta(503, 131016, "Service unavailable")]

    enviar(abrir, enviador)

    [aviso] = avisos_a(sesion)
    assert aviso.estado == EstadoMensaje.PENDIENTE and aviso.intentos == 1
    assert aviso.ultimo_error == "WhatsApp respondió 503 (error 131016): Service unavailable"


def test_un_numero_sin_whatsapp_no_se_reintenta(sesion: Session, abrir, enviador, meta, reloj):
    reloj["ahora"] = LUNES_8AM + minutos(30)
    aviso_de_llamado(sesion)
    meta.respuestas = [error_de_meta(400, 131026, "Message undeliverable")]

    enviar(abrir, enviador)

    [aviso] = avisos_a(sesion)
    assert aviso.estado == EstadoMensaje.FALLIDO
    assert "131026" in aviso.ultimo_error


def test_si_meta_no_contesta_a_tiempo_se_reintenta(sesion: Session, abrir, enviador, meta, reloj):
    reloj["ahora"] = LUNES_8AM + minutos(30)
    aviso_de_llamado(sesion)
    meta.respuestas = [httpx.ReadTimeout("lento")]

    enviar(abrir, enviador)

    [aviso] = avisos_a(sesion)
    assert (aviso.estado, aviso.ultimo_error) == (
        EstadoMensaje.PENDIENTE,
        "WhatsApp no respondió a tiempo.",
    )


def test_un_token_vencido_se_reintenta_y_se_avisa(sesion, abrir, enviador, meta, reloj, caplog):
    reloj["ahora"] = LUNES_8AM + minutos(30)
    aviso_de_llamado(sesion)
    meta.respuestas = [error_de_meta(401, 190, "Error validating access token")]

    enviar(abrir, enviador)

    [aviso] = avisos_a(sesion)
    assert aviso.estado == EstadoMensaje.PENDIENTE
    assert any("Cámbialo en WHATSAPP_TOKEN" in mensaje for mensaje in caplog.messages)


def test_meta_avisa_que_un_mensaje_no_se_entrego(webhook_listo: TestClient, sesion: Session):
    mensaje = bandeja_salida.encolar(
        sesion, telefono=CELULAR, tipo="respuesta", contenido={"texto": "hola"}, ahora=LUNES_8AM
    )
    mensaje.estado, mensaje.id_meta, mensaje.enviado_en = (
        EstadoMensaje.ENVIADO,
        "wamid.77",
        LUNES_8AM,
    )
    sesion.flush()
    fallo = {
        "id": "wamid.77",
        "status": "failed",
        "recipient_id": CELULAR,
        "errors": [{"code": 131047, "title": "Re-engagement message"}],
    }

    assert mandar(webhook_listo, aviso_de_meta(estados=[fallo])) == 200

    sesion.refresh(mensaje)
    assert mensaje.estado == EstadoMensaje.FALLIDO
    assert (
        mensaje.ultimo_error == "WhatsApp no pudo entregarlo (error 131047: Re-engagement message)."
    )


# --- Responderle desde Comentarios (hito 7b) ----------------------------------------------------


def test_el_administrador_le_responde_por_whatsapp_y_el_bot_no_se_mete(
    webhook_listo: TestClient, adentro: TestClient, sesion: Session, abrir, enviador, meta
):
    vehiculo = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    nuevo = por_whatsapp(sesion, vehiculo=vehiculo)  # llamado hasta las 8:15
    mandar(webhook_listo, aviso_de_meta(escribe("me atendieron mal")))
    assert meta.textos[-1].startswith("Qué pena, Ana, con lo que pasó.")
    [queja] = sesion.scalars(select(Comentario)).all()

    # El administrador le responde desde Comentarios y el reloj lo manda, como texto.
    adentro.post(
        f"/comentarios/{queja.id}/responder",
        data={"texto": "Hola Ana, te habla Eduar. ¿Qué pasó?"},
        headers=HTMX,
    )
    enviar(abrir, enviador)
    assert meta.enviados[-1]["type"] == "text"
    assert meta.textos[-1] == "Hola Ana, te habla Eduar. ¿Qué pasó?"

    # Lo que Ana le contesta a él, el bot no lo contesta.
    enviados = len(meta.enviados)
    mandar(webhook_listo, aviso_de_meta(escribe("El señor de la caja fue grosero")))
    assert len(meta.enviados) == enviados
    # Lo de la fila, sí.
    mandar(webhook_listo, aviso_de_meta(escribe("ya llegué")))
    assert meta.textos[-1].startswith(f"¡Bienvenido! Ya te marcamos como llegado ({nuevo.numero})")


# --- Registrar el número, el día de la SIM ------------------------------------------------------


def test_registrar_el_numero_con_el_pin(conectado):
    peticiones: list[httpx.Request] = []

    def meta_registra(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        return httpx.Response(200, json={"success": True})

    http = httpx.Client(transport=httpx.MockTransport(meta_registra))
    whatsapp.registrar_numero(obtener_entorno(), "123456", http=http)

    [peticion] = peticiones
    assert str(peticion.url) == f"https://graph.facebook.com/v26.0/{ID_DEL_NUMERO}/register"
    assert peticion.headers["Authorization"] == "Bearer token-de-prueba"
    assert json.loads(peticion.content) == {"messaging_product": "whatsapp", "pin": "123456"}


def test_si_meta_no_registra_el_numero_dice_por_que(conectado):
    def pin_equivocado(_: httpx.Request) -> httpx.Response:
        return error_de_meta(400, 133005, "Two step verification PIN Mismatch")

    http = httpx.Client(transport=httpx.MockTransport(pin_equivocado))
    with pytest.raises(whatsapp.RegistroFallido, match="error 133005.*PIN Mismatch"):
        whatsapp.registrar_numero(obtener_entorno(), "000000", http=http)


def test_enlazar_la_app_con_la_cuenta_de_whatsapp(conectado):
    peticiones: list[httpx.Request] = []

    def meta_enlaza(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        if peticion.method == "POST":
            return httpx.Response(200, json={"success": True})
        app = {"id": "67084", "link": "https://www.facebook.com/games/?app_id=67084"}
        return httpx.Response(
            200, json={"data": [{"whatsapp_business_api_data": {**app, "name": "Auto Ibaguer"}}]}
        )

    http = httpx.Client(transport=httpx.MockTransport(meta_enlaza))
    enlazadas = whatsapp.enlazar_la_app(obtener_entorno(), http=http)

    assert enlazadas == ["Auto Ibaguer"]
    url = f"https://graph.facebook.com/v26.0/{ID_DE_LA_CUENTA}/subscribed_apps"
    assert [(p.method, str(p.url)) for p in peticiones] == [("POST", url), ("GET", url)]
    assert all(p.headers["Authorization"] == "Bearer token-de-prueba" for p in peticiones)


def test_si_meta_no_enlaza_la_app_dice_por_que(conectado):
    def sin_permiso(_: httpx.Request) -> httpx.Response:
        return error_de_meta(403, 200, "Permissions error")

    http = httpx.Client(transport=httpx.MockTransport(sin_permiso))
    with pytest.raises(whatsapp.RegistroFallido, match="error 200.*Permissions error"):
        whatsapp.enlazar_la_app(obtener_entorno(), http=http)


@pytest.fixture
def registro(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """El comando registrar-whatsapp, sin llamar a Meta: anota lo que haría."""
    hecho: list[str] = []

    def enlaza(_entorno) -> list[str]:
        hecho.append("enlazar")
        return ["Auto Ibaguer"]

    monkeypatch.setattr(whatsapp, "enlazar_la_app", enlaza)
    monkeypatch.setattr(whatsapp, "registrar_numero", lambda _entorno, pin: hecho.append(pin))
    return hecho


def test_el_comando_enlaza_la_app_y_registra_con_el_pin(conectado, registro, monkeypatch, capsys):
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "654321")

    assert cli.main(["registrar-whatsapp"]) == 0

    assert registro == ["enlazar", "654321"]  # primero el enlace: no necesita el PIN
    salida = capsys.readouterr().out
    assert "La cuenta de WhatsApp quedó enlazada con: Auto Ibaguer." in salida
    assert "Listo: el número quedó registrado en WhatsApp." in salida


def test_el_comando_no_manda_un_pin_mal_escrito(conectado, registro, monkeypatch, capsys):
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "12345")

    assert cli.main(["registrar-whatsapp"]) == 1

    assert registro == ["enlazar"]
    assert "El PIN son 6 números." in capsys.readouterr().err


def test_el_comando_dice_que_falta_en_el_env(desconectado, registro, capsys):
    assert cli.main(["registrar-whatsapp"]) == 1

    assert registro == []
    assert (
        "Faltan WHATSAPP_TOKEN, WHATSAPP_ID_NUMERO y WHATSAPP_ID_CUENTA en el archivo .env."
        in capsys.readouterr().err
    )


def test_si_no_enlaza_la_app_no_pide_el_pin(conectado, monkeypatch, capsys):
    def no_enlaza(_entorno):
        raise whatsapp.RegistroFallido("WhatsApp respondió 400 (error 100): Invalid WABA ID")

    def no_se_pide(_):
        raise AssertionError("no debía pedir el PIN")

    monkeypatch.setattr(whatsapp, "enlazar_la_app", no_enlaza)
    monkeypatch.setattr(cli.getpass, "getpass", no_se_pide)

    assert cli.main(["registrar-whatsapp"]) == 1
    error = capsys.readouterr().err
    assert "Meta no enlazó la app con la cuenta de WhatsApp: WhatsApp respondió 400" in error


def test_el_comando_cuenta_por_que_meta_no_lo_registro(conectado, registro, monkeypatch, capsys):
    def no_registra(_entorno, _pin):
        raise whatsapp.RegistroFallido("WhatsApp respondió 400 (error 133005): PIN Mismatch")

    monkeypatch.setattr(whatsapp, "registrar_numero", no_registra)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: "111111")

    assert cli.main(["registrar-whatsapp"]) == 1
    assert "Meta no registró el número: WhatsApp respondió 400" in capsys.readouterr().err


# --- Piezas -------------------------------------------------------------------------------------


def test_la_firma_de_meta():
    cuerpo = b'{"object": "whatsapp_business_account"}'

    assert whatsapp.firma_valida(cuerpo, firmar(cuerpo), SECRETO)
    assert not whatsapp.firma_valida(cuerpo + b" ", firmar(cuerpo), SECRETO)
    assert not whatsapp.firma_valida(cuerpo, None, SECRETO)
    assert not whatsapp.firma_valida(cuerpo, firmar(cuerpo), "")


def test_cada_mensaje_del_bot_sale_como_lo_pide_meta():
    texto = whatsapp.contenido_para_meta(Mensaje("Hola"), CELULAR)
    assert texto == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": CELULAR,
        "type": "text",
        "text": {"preview_url": False, "body": "Hola"},
    }
    lista = Lista("Ver servicios", (Opcion("s.1", "Básico", "$40.000"), Opcion("s.2", "Otro")))
    filas = whatsapp.contenido_para_meta(Mensaje("¿Cuál?", lista=lista), CELULAR)["interactive"]
    assert filas["action"]["sections"] == [
        {
            "title": "Opciones",
            "rows": [
                {"id": "s.1", "title": "Básico", "description": "$40.000"},
                {"id": "s.2", "title": "Otro"},
            ],
        }
    ]


def test_una_respuesta_del_bot_se_guarda_y_se_lee_igual():
    mensaje = Mensaje("¿Cuál?", lista=Lista("Ver", (Opcion("s.1", "Básico", "$40.000"),)))

    assert Mensaje.desde_datos(json.loads(json.dumps(mensaje.a_datos()))) == mensaje


# --- La pantalla de mensajes ---------------------------------------------------------------------


def test_la_pantalla_de_mensajes_muestra_lo_que_entro_y_salio(
    webhook_listo: TestClient, adentro: TestClient
):
    foto = {"from": CELULAR, "id": "wamid.foto", "type": "image", "image": {"id": "media-1"}}
    mandar(webhook_listo, aviso_de_meta(toca("menu.fila", "Ver la fila"), foto))

    pagina = adentro.get("/mensajes").text

    assert "WhatsApp está conectado." in pagina
    assert "300 *** 4567" in pagina  # el celular, sin mostrarlo entero
    assert "Tocó «Ver la fila»" in pagina and "Mandó una foto" in pagina
    assert "Así está la fila ahora:" in pagina and "Enviado" in pagina


def test_la_pantalla_dice_que_paso_con_los_que_fallaron(
    adentro: TestClient, sesion: Session, conectado
):
    def mensaje(texto: str, estado: EstadoMensaje, intentos: int, error: str | None, **datos):
        sesion.add(
            MensajeSaliente(
                telefono=CELULAR,
                tipo="respuesta",
                contenido={"texto": texto},
                estado=estado,
                intentos=intentos,
                ultimo_error=error,
                proximo_intento_en=datos.get("proximo", LUNES_8AM),
                enviado_en=datos.get("enviado"),
            )
        )

    mensaje(
        "Espera",
        EstadoMensaje.PENDIENTE,
        2,
        "WhatsApp no respondió a tiempo.",
        proximo=LUNES_8AM + minutos(2),
    )
    mensaje("Salió", EstadoMensaje.ENVIADO, 2, "WhatsApp respondió 503", enviado=LUNES_8AM)
    mensaje("No salió", EstadoMensaje.FALLIDO, 1, "WhatsApp respondió 400 (error 131026)")
    sesion.add(
        MensajeEntrante(
            id_meta="wamid.e",
            telefono=CELULAR,
            tipo="text",
            contenido={"texto": "hola"},
            recibido_en=LUNES_8AM,
            procesado_en=LUNES_8AM,
            error="RuntimeError('algo')",
        )
    )
    sesion.flush()

    pagina = adentro.get("/mensajes").text

    assert "Reintenta a las 8:02 a.m." in pagina
    assert "Antes falló: WhatsApp respondió 503" in pagina
    assert '<span class="tabla-mensajes__error">WhatsApp respondió 400 (error 131026)' in pagina
    assert "El bot falló y le pidió disculpas al cliente: RuntimeError(&#39;algo&#39;)" in pagina


def test_sin_whatsapp_la_pantalla_dice_que_falta(adentro: TestClient, desconectado):
    pagina = adentro.get("/mensajes").text

    assert "WhatsApp todavía no está conectado" in pagina
    assert "WHATSAPP_TOKEN, WHATSAPP_ID_NUMERO, WHATSAPP_SECRETO_APP" in pagina
