"""El diagnóstico de WhatsApp: saber con datos por qué a alguien no le contestó el bot.

Lo que tiene que cumplirse siempre:
- cada aviso que manda Meta queda anotado, también los rechazados (firma mala, no JSON);
- se ve de quién era (el celular a medias) y si se atendió o por qué se descartó;
- nunca se guarda lo que escribió el cliente;
- el diagnóstico no puede impedir que el bot conteste;
- lo que dice Meta del número del bot se muestra, con alertas si algo está mal.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integraciones import diagnostico
from app.integraciones.tablas import AvisoDeMeta
from app.main import app
from app.web import mensajes
from pruebas.test_whatsapp import (  # noqa: F401 (fixtures)
    CELULAR,
    ID_DEL_NUMERO,
    MetaDeMentira,
    abrir,
    aviso_de_meta,
    conectado,
    enviador,
    mandar,
    meta,
    webhook_listo,
)

USUARIO = "CO.13491208655302741918"


def avisos(sesion: Session) -> list[AvisoDeMeta]:
    consulta = select(AvisoDeMeta).order_by(AvisoDeMeta.id)
    return list(sesion.scalars(consulta.execution_options(populate_existing=True)))


def texto(cuerpo: str, de: str = CELULAR, id_meta: str = "wamid.d1") -> dict:
    return {"from": de, "id": id_meta, "type": "text", "text": {"body": cuerpo}}


# --- Lo que llega al webhook -----------------------------------------------------------------


def test_cada_mensaje_queda_anotado_sin_lo_que_dijo(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
):
    mandar(webhook_listo, aviso_de_meta(texto("mi placa es ABC123, quiero un turno")))

    [aviso] = avisos(sesion)
    assert aviso.firma_valida and (aviso.mensajes, aviso.estados) == (1, 0)
    assert aviso.para_el_numero == ID_DEL_NUMERO and aviso.problema is None
    [detalle] = aviso.detalle
    assert detalle["quien"] == "300 *** 4567" and detalle["atendible"]
    assert detalle["campos"] == ["from", "id", "text", "type"]
    guardado = json.dumps(aviso.detalle)
    assert "ABC123" not in guardado and CELULAR not in guardado  # nada de lo que escribió
    assert meta.enviados  # y el bot contestó igual


def test_quien_no_muestra_su_celular_se_ve_asi(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
):
    sin_celular = {
        "from_user_id": USUARIO,
        "id": "wamid.d2",
        "type": "text",
        "text": {"body": "hola"},
    }
    mandar(webhook_listo, aviso_de_meta(sin_celular))

    [detalle] = avisos(sesion)[0].detalle
    assert detalle["quien"] == "WhatsApp sin número visible" and detalle["con_usuario"]
    assert detalle["atendible"]
    assert USUARIO not in json.dumps(detalle)


def test_lo_que_se_descarta_dice_por_que(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
):
    mandar(webhook_listo, aviso_de_meta({"id": "wamid.d3", "type": "text", "text": {"body": "x"}}))

    [detalle] = avisos(sesion)[0].detalle
    assert not detalle["atendible"]
    assert detalle["quien"] == "Sin remitente"
    assert detalle["motivo"] == "el remitente no es un número de celular"


def test_un_aviso_para_otro_numero_queda_con_el_problema(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
):
    mandar(webhook_listo, aviso_de_meta(texto("hola"), numero="123456123"))

    [aviso] = avisos(sesion)
    assert "otro número de WhatsApp (id 123456123)" in aviso.problema
    assert meta.enviados == []


def test_un_estado_fallido_dice_el_error(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
):
    fallido = {
        "id": "wamid.salio",
        "status": "failed",
        "errors": [{"code": 131047, "title": "Re-engagement message"}],
    }
    mandar(webhook_listo, aviso_de_meta(estados=[fallido, {"id": "wamid.otro", "status": "read"}]))

    fallo, leido = avisos(sesion)[0].detalle
    assert fallo["motivo"] == "WhatsApp no pudo entregarlo (error 131047: Re-engagement message)."
    assert (leido["estado"], leido["motivo"]) == ("read", None)


def test_los_rechazados_tambien_quedan(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
):
    assert mandar(webhook_listo, aviso_de_meta(texto("hola")), firma="sha256=falsa") == 403

    [aviso] = avisos(sesion)
    assert not aviso.firma_valida and aviso.problema == diagnostico.FIRMA_INVALIDA


def test_quedan_solo_los_ultimos(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(diagnostico, "QUEDAN_AVISOS", 2)
    for numero in range(4):
        mandar(webhook_listo, aviso_de_meta(texto("hola", id_meta=f"wamid.q{numero}")))

    assert len(avisos(sesion)) == 2


def test_si_el_diagnostico_falla_el_bot_contesta_igual(
    webhook_listo: TestClient,  # noqa: F811
    sesion: Session,
    meta: MetaDeMentira,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(diagnostico, "QUEDAN_AVISOS", "no es un número")  # falla al borrar

    assert mandar(webhook_listo, aviso_de_meta(texto("hola"))) == 200
    assert meta.enviados


# --- La página -------------------------------------------------------------------------------


def meta_que_contesta(numero: dict, apps: list[dict]) -> httpx.Client:
    def contestar(pedido: httpx.Request) -> httpx.Response:
        if pedido.url.path.endswith("/subscribed_apps"):
            if pedido.method == "POST":
                return httpx.Response(200, json={"success": True})
            return httpx.Response(200, json={"data": apps})
        return httpx.Response(200, json=numero)

    return httpx.Client(transport=httpx.MockTransport(contestar))


NUMERO_BIEN = {
    "display_phone_number": "+57 323 000 0000",
    "verified_name": "Auto Ibaguer",
    "status": "CONNECTED",
    "quality_rating": "GREEN",
    "platform_type": "CLOUD_API",
    "account_mode": "LIVE",
    "webhook_configuration": {"application": "https://auto-ibaguer.onrender.com/webhook/whatsapp"},
}
APP = [{"whatsapp_business_api_data": {"name": "Auto - Ibaguer"}}]


@pytest.fixture
def con_meta():
    def poner(numero: dict = NUMERO_BIEN, apps: list[dict] = APP) -> None:
        app.dependency_overrides[mensajes.obtener_http_de_meta] = lambda: meta_que_contesta(
            numero, apps
        )

    return poner


def test_la_pagina_muestra_el_numero_y_los_avisos(
    adentro: TestClient,
    sesion: Session,
    conectado,  # noqa: F811
    con_meta,
):
    con_meta()
    diagnostico.anotar(sesion, diagnostico.resumir(aviso_de_meta(texto("hola")), ID_DEL_NUMERO))

    pagina = adentro.get("/mensajes/diagnostico").text

    assert "+57 323 000 0000" in pagina and "Conectado" in pagina and "Alta" in pagina
    assert "Auto - Ibaguer" in pagina
    assert "300 *** 4567" in pagina and "Lo atiende el bot" in pagina
    assert "Ninguna app está enlazada" not in pagina


def test_la_pagina_avisa_si_algo_esta_mal(
    adentro: TestClient,
    conectado,  # noqa: F811
    con_meta,
):
    otro = {
        **NUMERO_BIEN,
        "status": "FLAGGED",
        "webhook_configuration": {"phone_number": "https://otro.example.com/hook"},
    }
    con_meta(otro, apps=[])

    pagina = adentro.get("/mensajes/diagnostico").text

    assert "El número no está conectado en Meta: Marcado por Meta." in pagina
    assert "https://otro.example.com/hook, no al webhook del bot" in pagina
    assert "Ninguna app está enlazada" in pagina


def test_enlazar_la_app_otra_vez(adentro: TestClient, conectado, con_meta):  # noqa: F811
    con_meta()

    respuesta = adentro.post("/mensajes/diagnostico/enlazar", follow_redirects=True)

    assert "la app quedó enlazada otra vez" in respuesta.text
