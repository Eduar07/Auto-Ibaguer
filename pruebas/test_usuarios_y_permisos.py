"""Usuarios y permisos: el administrador hace todo en los dos negocios; el cajero trabaja en el
suyo, pero no cambia precios, no corrige lo cobrado y no ve las cuentas.

Lo que tiene que cumplirse siempre:
- lo que el cajero no ve en el menú tampoco lo abre escribiendo la dirección;
- quien entra a un solo negocio no ve el otro;
- nadie se quita a sí mismo el acceso, y siempre queda un administrador activo.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.caja.estados import Negocio
from app.errores import ReglaDelNegocio
from app.web import administradores, permisos
from app.web.tablas import Administrador, Rol
from pruebas.navegador import HTMX, aviso, error

CLAVE_CAJERO = "clave-del-cajero-1"


def usuario(rol: Rol, negocio: Negocio | None = None) -> Administrador:
    return Administrador(usuario="x", clave_cifrada="x", rol=rol, negocio=negocio)


@pytest.mark.parametrize(
    ("metodo", "ruta", "puede"),
    [
        ("GET", "/fila", True),
        ("POST", "/turnos/3/cobrar", True),
        ("GET", "/caja", True),
        ("POST", "/caja/salidas", True),
        ("POST", "/caja/cerrar", True),
        ("GET", "/clientes", True),
        ("GET", "/balance", False),
        ("GET", "/configuracion", False),
        ("GET", "/caja/5/corregir", False),
        ("POST", "/caja/salidas/7/anular", False),
        ("GET", "/caja/exportar", False),
        ("POST", "/turnos/3/corregir-cobro", False),
        ("GET", "/resultados", False),
        ("GET", "/usuarios", False),
        ("GET", "/parqueadero", False),  # otro negocio
        ("GET", "/inicio", True),
    ],
)
def test_lo_que_abre_un_cajero_del_lavadero(metodo: str, ruta: str, puede: bool):
    assert (permisos.revisar(usuario(Rol.CAJERO, Negocio.LAVADERO), metodo, ruta) is None) is puede


@pytest.mark.parametrize(
    ("metodo", "ruta", "puede"),
    [
        ("GET", "/parqueadero", True),
        ("POST", "/parqueadero/entradas", True),
        ("POST", "/parqueadero/12/salida", True),
        ("POST", "/parqueadero/mensualidades/4/pagar", True),
        ("GET", "/parqueadero/mensualidades", True),
        ("POST", "/parqueadero/mensualidades", False),
        ("GET", "/parqueadero/mensualidades/4/editar", False),
        ("GET", "/parqueadero/tarifas", False),
        ("GET", "/parqueadero/balance", False),
        ("POST", "/parqueadero/12/corregir", False),
        ("GET", "/fila", True),  # entra a los dos
    ],
)
def test_lo_que_abre_un_cajero_de_los_dos(metodo: str, ruta: str, puede: bool):
    assert (permisos.revisar(usuario(Rol.CAJERO), metodo, ruta) is None) is puede


def test_el_administrador_abre_todo():
    for ruta in (
        "/balance",
        "/parqueadero/tarifas",
        "/resultados",
        "/usuarios",
        "/caja/5/corregir",
    ):
        assert permisos.revisar(usuario(Rol.ADMINISTRADOR), "GET", ruta) is None


# --- Crear y cambiar usuarios ----------------------------------------------------------------


def test_un_administrador_entra_a_los_dos(sesion: Session):
    nuevo = administradores.crear_usuario(
        sesion, "Laura", "clave-larga-de-laura", "administrador", "lavadero"
    )

    assert (nuevo.usuario, nuevo.rol, nuevo.negocio) == ("laura", Rol.ADMINISTRADOR, None)


@pytest.mark.parametrize(
    ("datos", "dice"),
    [
        ({"usuario": "eduar"}, "Ya hay un usuario eduar"),
        ({"usuario": "A B"}, "El usuario debe tener"),
        ({"clave": "corta"}, "al menos 10"),
        ({"rol": "jefe"}, "administrador o cajero"),
        ({"negocio": "panaderia"}, "a qué negocio"),
    ],
)
def test_lo_que_se_revisa_al_crear(sesion: Session, admin, datos: dict, dice: str):
    valores = {
        "usuario": "camilo",
        "clave": CLAVE_CAJERO,
        "rol": "cajero",
        "negocio": "lavadero",
    } | datos
    with pytest.raises(ReglaDelNegocio, match=dice):
        administradores.crear_usuario(sesion, **valores)


def test_nadie_se_quita_el_acceso_y_queda_un_administrador(sesion: Session, admin):
    with pytest.raises(ReglaDelNegocio, match="No puedes quitarte"):
        administradores.cambiar_usuario(
            sesion, admin, admin.id, rol="cajero", negocio=None, activo=True
        )
    otro = administradores.crear_usuario(
        sesion, "laura", "clave-larga-de-laura", "administrador", None
    )
    administradores.cambiar_usuario(
        sesion, otro, admin.id, rol="cajero", negocio="lavadero", activo=True
    )
    with pytest.raises(ReglaDelNegocio, match="al menos un administrador activo"):
        administradores.cambiar_usuario(
            sesion, admin, otro.id, rol="administrador", negocio=None, activo=False
        )


# --- En la pantalla -------------------------------------------------------------------------


def entrar_como_cajero(adentro: TestClient, sesion: Session, negocio: str) -> TestClient:
    respuesta = adentro.post(
        "/usuarios",
        data={"usuario": "camilo", "clave": CLAVE_CAJERO, "rol": "cajero", "negocio": negocio},
        headers=HTMX,
    )
    assert aviso(respuesta) == "Usuario camilo creado. Ya puede entrar."
    adentro.cookies.clear()
    entro = adentro.post(
        "/entrar", data={"usuario": "camilo", "clave": CLAVE_CAJERO}, follow_redirects=False
    )
    assert entro.status_code == 303
    return adentro


def test_el_cajero_del_lavadero_va_derecho_a_su_negocio(adentro: TestClient, sesion: Session):
    cajero = entrar_como_cajero(adentro, sesion, "lavadero")

    assert cajero.get("/inicio", follow_redirects=False).headers["location"] == "/fila"
    fila = cajero.get("/fila").text
    assert (
        'href="/caja"' in fila
        and 'href="/balance"' not in fila
        and 'href="/configuracion"' not in fila
    )
    assert "Corregir la base" not in cajero.get("/caja").text
    prohibido = cajero.get("/parqueadero")
    assert prohibido.status_code == 403 and "no entra al parqueadero" in prohibido.text
    assert (
        error(cajero.get("/balance", headers=HTMX))
        == "Eso lo hace un administrador: pídeselo a él."
    )
    assert cajero.get("/usuarios").status_code == 403


def test_el_administrador_ve_los_usuarios_y_los_cambia(adentro: TestClient, sesion: Session):
    adentro.post(
        "/usuarios",
        data={
            "usuario": "camilo",
            "clave": CLAVE_CAJERO,
            "rol": "cajero",
            "negocio": "parqueadero",
        },
        headers=HTMX,
    )
    camilo = sesion.query(Administrador).filter_by(usuario="camilo").one()
    pagina = adentro.get("/usuarios").text
    assert "Cajero · Parqueadero" in pagina and "eduar" in pagina
    assert 'href="/usuarios"' in adentro.get("/inicio").text

    respuesta = adentro.post(
        f"/usuarios/{camilo.id}", data={"rol": "cajero", "negocio": "", "activo": ""}, headers=HTMX
    )

    assert aviso(respuesta) == "Usuario camilo actualizado."
    assert "Desactivado" in respuesta.text
    sesion.refresh(camilo)
    assert not camilo.activo and camilo.negocio is None
    adentro.cookies.clear()
    no_entra = adentro.post(
        "/entrar", data={"usuario": "camilo", "clave": CLAVE_CAJERO}, follow_redirects=False
    )
    assert no_entra.status_code != 303


def test_quitarse_el_acceso_se_explica_en_el_dialogo(adentro: TestClient, sesion: Session, admin):
    respuesta = adentro.post(
        f"/usuarios/{admin.id}", data={"rol": "cajero", "activo": "si"}, headers=HTMX
    )

    assert "No puedes quitarte a ti mismo" in respuesta.text
