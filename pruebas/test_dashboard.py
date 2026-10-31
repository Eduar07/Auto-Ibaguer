"""El dashboard por dentro: entrar, ver la fila en vivo y mover cada turno con sus botones.

Estas pruebas hablan con el servidor como lo haría el navegador (TestClient), pero sobre
la base de pruebas que se deshace sola. La hora queda fija en el lunes a las 8:00 a.m.
"""

import json
import time
from collections.abc import AsyncIterator, Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import tiempo
from app.db.sesion import obtener_sesion
from app.main import app
from app.turnos.estados import EstadoTurno
from app.turnos.tablas import Pago, Turno
from app.web import dashboard, en_vivo, sesiones
from app.web.administradores import guardar_administrador
from app.web.tablas import Administrador
from pruebas.fabricas import (
    LUNES_8AM,
    en_el_local,
    estado,
    historial,
    minutos,
    ocupar_puestos,
    por_whatsapp,
    puesto,
    servicio,
    tipo_vehiculo,
    turno_listo,
)

CLAVE = "clave-larga-del-lavadero"
HTMX = {"HX-Request": "true"}


@pytest.fixture
def reloj(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    """La hora del sistema queda fija. Para adelantarla: reloj["ahora"] += minutos(5)."""
    hora = {"ahora": LUNES_8AM}
    monkeypatch.setattr(tiempo, "ahora", lambda: hora["ahora"])
    return hora


@pytest.fixture
def web(sesion: Session, reloj: dict, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Un navegador sin sesión. Cada prueba empieza sin intentos fallidos guardados."""
    app.dependency_overrides[obtener_sesion] = lambda: sesion
    monkeypatch.setattr(dashboard, "LIMITE_DE_INTENTOS", sesiones.LimiteDeIntentos())
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def admin(sesion: Session) -> Administrador:
    return guardar_administrador(sesion, "eduar", CLAVE)[0]


@pytest.fixture
def adentro(web: TestClient, admin: Administrador) -> TestClient:
    """Un navegador que ya entró con el usuario del administrador."""
    respuesta = web.post(
        "/entrar", data={"usuario": "eduar", "clave": CLAVE}, follow_redirects=False
    )
    assert respuesta.status_code == 303
    return web


def poner_cookie(web: TestClient, valor: str) -> None:
    """Pone a mano la cookie de sesión, como si el navegador la tuviera guardada."""
    web.cookies.clear()
    web.cookies.set(sesiones.NOMBRE_COOKIE, valor, domain="testserver.local")


def guardar(sesion: Session) -> None:
    """Confirma lo preparado, como si ya estuviera guardado de antes.

    Cuando una regla no se cumple, el dashboard deshace solo esa acción (rollback). En la
    vida real lo anterior ya estaba guardado; en la prueba hay que confirmarlo primero.
    Igual se borra al final: la prueba entera corre dentro de una transacción.
    """
    sesion.commit()


def eventos(respuesta: Response) -> dict:
    """Lo que el servidor le pide a la página después de una acción (HX-Trigger)."""
    return json.loads(respuesta.headers.get("HX-Trigger", "{}"))


def aviso(respuesta: Response) -> str:
    return eventos(respuesta).get("aviso", "")


def error(respuesta: Response) -> str:
    return eventos(respuesta).get("error", "")


# --- Entrar y salir --------------------------------------------------------------------


def test_sin_sesion_la_fila_manda_a_entrar(web: TestClient):
    respuesta = web.get("/fila", follow_redirects=False)

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/entrar"


def test_sin_sesion_htmx_recibe_la_orden_de_ir_a_entrar(web: TestClient):
    """HTMX no sigue redirecciones por su cuenta: se le dice a dónde ir."""
    respuesta = web.get("/fila/tablero", headers=HTMX)

    assert respuesta.status_code == 200
    assert respuesta.headers["HX-Redirect"] == "/entrar"


def test_sin_sesion_los_eventos_en_vivo_se_niegan(web: TestClient):
    assert web.get("/eventos").status_code == 401


def test_entrar_con_la_clave_correcta(web: TestClient, admin: Administrador):
    respuesta = web.post(
        "/entrar", data={"usuario": "Eduar ", "clave": CLAVE}, follow_redirects=False
    )

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/fila"
    cookie = respuesta.headers["set-cookie"].lower()
    assert sesiones.NOMBRE_COOKIE in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert web.get("/fila").status_code == 200


def test_clave_incorrecta(web: TestClient, admin: Administrador):
    respuesta = web.post("/entrar", data={"usuario": "eduar", "clave": "no-es-la-clave"})

    assert respuesta.status_code == 401
    assert "Usuario o contraseña incorrectos." in respuesta.text
    assert "set-cookie" not in respuesta.headers


def test_un_usuario_que_no_existe_recibe_el_mismo_mensaje(web: TestClient, admin: Administrador):
    """Así nadie puede averiguar qué usuarios existen."""
    respuesta = web.post("/entrar", data={"usuario": "nadie", "clave": CLAVE})

    assert respuesta.status_code == 401
    assert "Usuario o contraseña incorrectos." in respuesta.text


def test_cinco_intentos_fallidos_bloquean_al_usuario(web: TestClient, admin: Administrador):
    for _ in range(5):
        web.post("/entrar", data={"usuario": "eduar", "clave": "adivinando-123"})

    respuesta = web.post("/entrar", data={"usuario": "eduar", "clave": CLAVE})

    assert respuesta.status_code == 429
    assert "Demasiados intentos fallidos" in respuesta.text


def test_la_cookie_hecha_por_el_servidor_sirve(web: TestClient, admin: Administrador):
    poner_cookie(web, dashboard._valor_de_cookie(admin))

    assert web.get("/fila", follow_redirects=False).status_code == 200


def test_una_cookie_alterada_no_sirve(web: TestClient, admin: Administrador):
    contenido, firma = dashboard._valor_de_cookie(admin).rsplit(".", 1)
    otra_letra = "A" if firma[0] != "A" else "B"
    poner_cookie(web, f"{contenido}.{otra_letra}{firma[1:]}")

    assert web.get("/fila", follow_redirects=False).headers["location"] == "/entrar"


def test_una_sesion_vencida_pide_entrar_otra_vez(web: TestClient, admin: Administrador):
    vencida = sesiones.DatosDeSesion(
        administrador_id=admin.id,
        huella=sesiones.huella_de_la_clave(admin.clave_cifrada),
        vence=int(time.time()) - 1,
    )
    poner_cookie(web, sesiones.crear_valor(vencida, dashboard.obtener_entorno().clave_secreta))

    assert web.get("/fila", follow_redirects=False).status_code == 303


def test_cambiar_la_clave_cierra_las_sesiones_abiertas(adentro: TestClient, sesion: Session):
    assert adentro.get("/fila", follow_redirects=False).status_code == 200

    guardar_administrador(sesion, "eduar", "otra-clave-larga-456")

    assert adentro.get("/fila", follow_redirects=False).status_code == 303


def test_un_administrador_desactivado_ya_no_entra(
    adentro: TestClient, admin: Administrador, sesion: Session
):
    admin.activo = False
    sesion.flush()

    assert adentro.get("/fila", follow_redirects=False).status_code == 303


def test_usar_el_dashboard_renueva_la_sesion(adentro: TestClient):
    respuesta = adentro.get("/fila")

    assert sesiones.NOMBRE_COOKIE in respuesta.headers["set-cookie"]


def test_con_sesion_abierta_entrar_lleva_a_la_fila(adentro: TestClient):
    respuesta = adentro.get("/entrar", follow_redirects=False)

    assert respuesta.headers["location"] == "/fila"


def test_salir_borra_la_sesion(adentro: TestClient):
    respuesta = adentro.post("/salir", follow_redirects=False)

    assert respuesta.headers["location"] == "/entrar"
    assert 'auto_ibaguer_sesion=""' in respuesta.headers["set-cookie"]
    assert adentro.get("/fila", follow_redirects=False).status_code == 303


def test_un_formulario_de_otro_sitio_no_puede_actuar(adentro: TestClient, sesion: Session):
    """Defensa contra CSRF: otra página no puede enviar formularios al dashboard."""
    turno_id = ocupar_puestos(sesion, "C1")[0]

    respuesta = adentro.post(
        f"/turnos/{turno_id}/listo", headers={"Origin": "https://otro-sitio.com"}
    )

    assert respuesta.status_code == 403
    assert estado(sesion, turno_id) == EstadoTurno.EN_LAVADO


# --- La fila en vivo -------------------------------------------------------------------


def test_la_fila_muestra_puestos_espera_y_lo_que_falta_cobrar(adentro: TestClient, sesion: Session):
    listo = turno_listo(sesion)  # se lavó en C1 y quedó libre
    lavando = ocupar_puestos(sesion, "C1")[0]
    llamado = por_whatsapp(sesion)  # C2 está libre: lo llaman de una vez
    en_fila = por_whatsapp(sesion)
    llego = en_el_local(sesion, servicio_id=servicio(sesion, "Encerado").id)

    pagina = adentro.get("/fila").text

    assert "Fila en vivo" in pagina
    assert "Lunes 26 de octubre · 8:00 a.m." in pagina
    assert "Puesto C1" in pagina and "Marcar listo" in pagina
    assert f"Llamamos a {llamado.numero}. Tiene hasta las 8:15 a.m. para llegar." in pagina
    assert "Llamado · 15:00" in pagina  # la cuenta regresiva arranca con los 15 minutos
    assert en_fila.numero in pagina and "Es el siguiente" not in pagina
    assert "Llegó · en el local" in pagina
    # El puesto no espera: quien ya llegó puede pasar antes que el llamado.
    assert f"{llego.numero} ya está en el local y puede pasar primero" in pagina
    assert "Pasar a puesto C2" in pagina
    assert "Encerado · $60.000" in pagina
    # Por cobrar
    numero_listo = dashboard._numero(sesion, listo)
    assert numero_listo in pagina and "Cobrar" in pagina
    assert f'hx-get="/turnos/{listo}/cobrar"' in pagina
    assert lavando and "Caja de hoy" in pagina


def test_el_pedazo_de_la_fila_viene_sin_la_pagina_completa(adentro: TestClient):
    respuesta = adentro.get("/fila/tablero", headers=HTMX)

    assert respuesta.status_code == 200
    assert "En espera" in respuesta.text
    assert "<html" not in respuesta.text


def test_la_pagina_pide_los_archivos_con_su_huella(adentro: TestClient):
    pagina = adentro.get("/fila").text

    assert "/estaticos/css/dashboard.css?v=" in pagina
    assert adentro.get("/estaticos/js/dashboard.js").status_code == 200


def test_un_llamado_sin_llegar_muestra_el_tiempo_vencido(
    adentro: TestClient, sesion: Session, reloj: dict
):
    llamado = por_whatsapp(sesion)
    reloj["ahora"] = LUNES_8AM + minutos(16)

    pagina = adentro.get("/fila/tablero", headers=HTMX).text

    assert llamado.llamado
    assert "Llamado · tiempo vencido" in pagina


# --- Nuevo turno en el local -----------------------------------------------------------


def test_el_dialogo_de_nuevo_turno_propone_automovil_y_basico(adentro: TestClient, sesion: Session):
    respuesta = adentro.get("/turnos/nuevo", headers=HTMX)

    assert "Nuevo turno en el local" in respuesta.text
    automovil = tipo_vehiculo(sesion, "Automóvil").id
    assert f'name="tipo_vehiculo_id" value="{automovil}" required' in respuesta.text
    assert "C-01" in respuesta.text and "Entra a la fila de carros" in respuesta.text


def test_la_vista_previa_cambia_con_el_tipo_de_vehiculo(adentro: TestClient, sesion: Session):
    moto = tipo_vehiculo(sesion, "Moto").id

    respuesta = adentro.get(f"/turnos/nuevo/vista-previa?tipo_vehiculo_id={moto}", headers=HTMX)

    assert "M-01" in respuesta.text
    assert "Entra a la fila de motos" in respuesta.text


def test_crear_un_turno_en_el_local(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/turnos",
        headers=HTMX,
        data={
            "nombre": "paola ruiz",
            "celular": "321 456 4410",
            "tipo_vehiculo_id": tipo_vehiculo(sesion, "Automóvil").id,
            "placa": "",
            "servicio_id": servicio(sesion, "Básico").id,
            "acepta_datos": "si",
        },
    )

    assert respuesta.status_code == 200
    assert respuesta.headers["HX-Retarget"] == "#tablero"
    assert eventos(respuesta)["cerrar-dialogo"] is True
    assert aviso(respuesta).startswith("Turno C-01 para Paola Ruiz.")
    assert "código QR" in aviso(respuesta)
    turno = sesion.scalars(select(Turno)).one()
    assert turno.estado == EstadoTurno.LLEGO
    assert "Paola Ruiz" in respuesta.text and "Placa pendiente" in respuesta.text


def test_sin_autorizacion_de_datos_el_dialogo_lo_explica_y_conserva_lo_escrito(
    adentro: TestClient, sesion: Session
):
    respuesta = adentro.post(
        "/turnos",
        headers=HTMX,
        data={
            "nombre": "Paola",
            "tipo_vehiculo_id": tipo_vehiculo(sesion, "Camioneta").id,
            "servicio_id": "",
        },
    )

    assert "HX-Retarget" not in respuesta.headers  # sigue en el diálogo
    assert "tratamiento de datos" in respuesta.text
    assert 'value="Paola"' in respuesta.text
    assert sesion.scalars(select(Turno)).first() is None


# --- Mover la fila: puesto, listo y cobro ------------------------------------------------


def test_una_fila_completa_desde_el_dashboard(adentro: TestClient, sesion: Session, reloj: dict):
    """La prueba final del hito: crear, pasar a puesto, marcar listo y cobrar."""
    nuevo = adentro.post(
        "/turnos",
        headers=HTMX,
        data={
            "nombre": "Felipe",
            "tipo_vehiculo_id": tipo_vehiculo(sesion, "Automóvil").id,
            "servicio_id": "",  # lo decide al llegar
            "placa": "",
            "acepta_datos": "si",
        },
    )
    turno_id = sesion.scalars(select(Turno.id)).one()
    assert aviso(nuevo) == "Turno C-01 para Felipe."  # sin celular, sin código QR

    pasar = adentro.post(
        f"/turnos/{turno_id}/puesto", headers=HTMX, data={"puesto_id": puesto(sesion, "C1").id}
    )
    assert aviso(pasar) == "C-01 pasó al puesto C1."

    reloj["ahora"] += minutos(45)
    listo = adentro.post(f"/turnos/{turno_id}/listo", headers=HTMX)
    assert aviso(listo) == "C-01 quedó listo. Ya se puede cobrar."
    assert "1 por cobrar" in listo.text

    dialogo = adentro.get(f"/turnos/{turno_id}/cobrar", headers=HTMX)
    assert "Cobrar turno" in dialogo.text
    assert "Placa (obligatoria para cobrar)" in dialogo.text
    assert "Escribe la placa para cobrar" in dialogo.text

    cobro = adentro.post(
        f"/turnos/{turno_id}/cobrar",
        headers=HTMX,
        data={
            "servicio_id": servicio(sesion, "Encerado").id,
            "precio_cobrado": "60.000",
            "motivo": "",
            "medio_pago": "nequi",
            "placa": "jkl221",
        },
    )

    assert aviso(cobro) == "C-01 cobrado: $60.000 en nequi."
    assert estado(sesion, turno_id) == EstadoTurno.PAGADO
    pago = sesion.scalars(select(Pago)).one()
    assert (pago.precio_menu, pago.precio_cobrado) == (60_000, 60_000)
    assert "$60.000" in cobro.text  # la caja de hoy ya lo suma


def test_cobrar_sin_placa_lo_explica_dentro_del_dialogo(adentro: TestClient, sesion: Session):
    turno_id = turno_listo(sesion, placa=None)
    guardar(sesion)

    respuesta = adentro.post(
        f"/turnos/{turno_id}/cobrar",
        headers=HTMX,
        data={"servicio_id": servicio(sesion, "Básico").id, "medio_pago": "efectivo"},
    )

    assert "HX-Retarget" not in respuesta.headers
    assert "Escribe la placa antes de cobrar" in respuesta.text
    assert estado(sesion, turno_id) == EstadoTurno.LISTO


def test_cobrar_distinto_al_menu_pide_el_motivo(adentro: TestClient, sesion: Session):
    turno_id = turno_listo(sesion)
    guardar(sesion)
    datos = {
        "servicio_id": servicio(sesion, "Básico").id,
        "precio_cobrado": "$35.000",
        "medio_pago": "efectivo",
    }

    sin_motivo = adentro.post(f"/turnos/{turno_id}/cobrar", headers=HTMX, data=datos)
    con_motivo = adentro.post(
        f"/turnos/{turno_id}/cobrar", headers=HTMX, data={**datos, "motivo": "Cliente frecuente"}
    )

    assert "escribe el motivo" in sin_motivo.text
    assert aviso(con_motivo).endswith("cobrado: $35.000 en efectivo.")
    assert sesion.scalars(select(Pago.motivo_ajuste)).one() == "Cliente frecuente"


def test_con_varios_puestos_libres_se_elige_en_un_dialogo(adentro: TestClient, sesion: Session):
    llego = en_el_local(sesion)

    fila = adentro.get("/fila/tablero", headers=HTMX).text
    dialogo = adentro.get(f"/turnos/{llego.turno.id}/puesto", headers=HTMX).text

    assert f'hx-get="/turnos/{llego.turno.id}/puesto"' in fila
    assert 'name="puesto_id"' in dialogo and ">C1<" in dialogo and ">C2<" in dialogo


def test_un_puesto_que_se_ocupo_mientras_tanto_se_explica_en_el_dialogo(
    adentro: TestClient, sesion: Session
):
    ocupar_puestos(sesion, "C1")
    llego = en_el_local(sesion)
    guardar(sesion)

    respuesta = adentro.post(
        f"/turnos/{llego.turno.id}/puesto",
        headers=HTMX,
        data={"puesto_id": puesto(sesion, "C1").id, "dialogo": "1"},
    )

    assert "HX-Retarget" not in respuesta.headers
    assert 'class="error"' in respuesta.text
    assert ">C2<" in respuesta.text  # le ofrece el que sí está libre
    assert estado(sesion, llego.turno.id) == EstadoTurno.LLEGO


def test_marcar_la_llegada_de_un_llamado(adentro: TestClient, sesion: Session):
    llamado = por_whatsapp(sesion)

    respuesta = adentro.post(f"/turnos/{llamado.turno.id}/llegada", headers=HTMX)

    assert aviso(respuesta) == f"{llamado.numero} ya está en el local."
    assert estado(sesion, llamado.turno.id) == EstadoTurno.LLEGO


def test_corregir_servicio_y_placa(adentro: TestClient, sesion: Session):
    llego = en_el_local(sesion)
    assert "Corregir turno" in adentro.get(f"/turnos/{llego.turno.id}/corregir", headers=HTMX).text

    respuesta = adentro.post(
        f"/turnos/{llego.turno.id}/corregir",
        headers=HTMX,
        data={
            "tipo_vehiculo_id": tipo_vehiculo(sesion, "Camioneta").id,
            "placa": "klm482",
            "servicio_id": servicio(sesion, "General").id,
        },
    )

    assert aviso(respuesta) == f"{llego.numero} corregido."
    assert "General · $140.000" in respuesta.text
    assert "KLM482" in respuesta.text


def test_cancelar_un_turno_guarda_el_motivo(adentro: TestClient, sesion: Session):
    en_fila = por_whatsapp(sesion)

    respuesta = adentro.post(
        f"/turnos/{en_fila.turno.id}/cancelar", headers=HTMX, data={"motivo": "Avisó que no viene"}
    )

    assert aviso(respuesta) == f"{en_fila.numero} cancelado."
    assert estado(sesion, en_fila.turno.id) == EstadoTurno.CANCELADO
    assert historial(sesion, en_fila.turno.id)[-1].detalle == "Avisó que no viene"


def test_una_accion_que_ya_no_aplica_se_explica_sin_romper_la_fila(
    adentro: TestClient, sesion: Session
):
    """Por ejemplo: otro celular ya lo marcó listo hace un segundo."""
    turno_id = turno_listo(sesion)
    guardar(sesion)

    respuesta = adentro.post(f"/turnos/{turno_id}/listo", headers=HTMX)

    assert respuesta.status_code == 200
    assert respuesta.headers["HX-Retarget"] == "#tablero"
    assert error(respuesta)
    assert estado(sesion, turno_id) == EstadoTurno.LISTO


def test_un_turno_que_no_existe(adentro: TestClient):
    respuesta = adentro.post("/turnos/999999/listo", headers=HTMX)

    assert respuesta.status_code == 404
    assert respuesta.json()["detail"] == "Ese turno no existe."


# --- Avisos en vivo ------------------------------------------------------------------------


def test_los_eventos_le_pasan_cada_cambio_al_navegador(
    web: TestClient, monkeypatch: pytest.MonkeyPatch
):
    async def cambios_de_prueba(_url: str) -> AsyncIterator[int]:
        for fila_id in (1, 2):
            yield fila_id

    monkeypatch.setattr(en_vivo, "cambios", cambios_de_prueba)
    app.dependency_overrides[dashboard._administrador_para_eventos] = lambda: 1

    with web.stream("GET", "/eventos") as respuesta:
        cuerpo = "".join(respuesta.iter_text())

    assert respuesta.headers["content-type"].startswith("text/event-stream")
    assert "event: cambio\ndata: 1\n\n" in cuerpo
    assert "event: cambio\ndata: 2\n\n" in cuerpo
