"""Las pantallas de Clientes y Configuración, usadas como lo haría el navegador."""

from datetime import time

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import Puesto, Servicio
from app.clientes.tablas import Cliente
from app.turnos import datos
from app.turnos.estados import EstadoTurno
from app.turnos.tablas import Turno
from pruebas.fabricas import (
    cliente_con_vehiculo,
    en_el_local,
    fila,
    ocupar_puestos,
    puesto,
    servicio,
    tipo_vehiculo,
)
from pruebas.navegador import HTMX, aviso, error, eventos, guardar

DESDE_CLIENTES = {**HTMX, "HX-Current-URL": "http://testserver/clientes"}


def formulario_de_configuracion(sesion: Session, **cambios: str) -> dict:
    """El formulario de Configuración tal como lo envía el navegador, con los valores actuales."""
    parametros = leer_parametros(sesion)
    datos_formulario: dict = {
        "nombre_negocio": parametros.nombre_negocio,
        "telefono_contacto": "322 755 6850",
        "hora_apertura": "05:30",
        "hora_cierre": "19:30",
        "dias_atencion": ["1", "2", "3", "4", "5", "6", "7"],
        "bot_activo": "si",
        "gracia_minutos": "15",
        "avisar_cuando_falten": "2",
        "sesion_bot_minutos": "30",
        "aviso_datos": parametros.aviso_datos,
    }
    puestos = list(sesion.scalars(select(Puesto)))
    datos_formulario["puestos_en_pantalla"] = [str(p.id) for p in puestos]
    for p in puestos:
        if p.activo:
            datos_formulario[f"puesto-{p.id}"] = "si"
    datos_formulario.update(cambios)
    return datos_formulario


# --- Navegación ---------------------------------------------------------------------------


def test_las_secciones_piden_entrar(web: TestClient):
    for ruta in ("/clientes", "/configuracion"):
        assert web.get(ruta, follow_redirects=False).headers["location"] == "/entrar"


def test_cada_pantalla_marca_su_seccion(adentro: TestClient):
    for ruta in ("/fila", "/clientes", "/configuracion"):
        pagina = adentro.get(ruta).text
        assert f'<a href="{ruta}" aria-current="page">' in pagina


# --- Clientes ----------------------------------------------------------------------------------


def test_la_pantalla_de_clientes_lista_y_busca(adentro: TestClient, sesion: Session):
    cliente_con_vehiculo(sesion, nombre="Andrés Pérez", placa="KLM482")
    cliente_con_vehiculo(sesion, nombre="Laura Gómez")

    pagina = adentro.get("/clientes").text
    busqueda = adentro.get("/clientes/lista?q=andres", headers=HTMX).text

    assert "2 clientes registrados" in pagina and "Laura Gómez" in pagina
    assert "Andrés Pérez" in busqueda and "KLM482" in busqueda
    assert "Laura Gómez" not in busqueda
    assert "Ningún cliente coincide" in adentro.get("/clientes/lista?q=zzz", headers=HTMX).text


def test_la_ficha_del_cliente(adentro: TestClient, sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, nombre="Felipe Osorio", placa="JKL221")
    en_el_local(sesion, vehiculo=vehiculo)

    ficha = adentro.get(f"/clientes/{vehiculo.cliente_id}/ficha", headers=HTMX).text

    assert "Felipe Osorio" in ficha and "JKL221" in ficha
    assert "Llegó" in ficha  # su turno de hoy, en la fila
    assert "Dar turno" in ficha and "Borrar datos del cliente" in ficha
    assert adentro.get("/clientes/999999/ficha", headers=HTMX).status_code == 404


def test_editar_los_datos_actualiza_la_ficha_y_la_lista(adentro: TestClient, sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, nombre="felipe", telefono="3001112233")

    respuesta = adentro.post(
        f"/clientes/{vehiculo.cliente_id}/editar",
        headers=HTMX,
        data={"nombre": "Felipe Osorio", "celular": "321 456 4410"},
    )

    assert respuesta.headers["HX-Retarget"] == "#ficha-cliente"
    assert aviso(respuesta) == "Datos de Felipe Osorio guardados."
    assert eventos(respuesta, "HX-Trigger-After-Swap") == {"lista-cambiada": True}
    assert "321 *** 4410" in respuesta.text


def test_un_celular_repetido_se_explica_en_el_dialogo(adentro: TestClient, sesion: Session):
    cliente_con_vehiculo(sesion, nombre="Laura", telefono="3001112233")
    otro = cliente_con_vehiculo(sesion, nombre="Paola", telefono="3004445566")
    guardar(sesion)

    respuesta = adentro.post(
        f"/clientes/{otro.cliente_id}/editar",
        headers=HTMX,
        data={"nombre": "Paola", "celular": "3001112233"},
    )

    assert "HX-Retarget" not in respuesta.headers
    assert "Ese celular ya es de otro cliente: Laura." in respuesta.text


def test_borrar_los_datos_de_un_cliente(adentro: TestClient, sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, nombre="Felipe", placa="JKL221")
    dialogo = adentro.get(f"/clientes/{vehiculo.cliente_id}/borrar", headers=HTMX).text
    assert "JKL221" in dialogo and "No se puede deshacer" in dialogo

    respuesta = adentro.post(f"/clientes/{vehiculo.cliente_id}/borrar", headers=HTMX)

    assert aviso(respuesta) == "Se borraron los datos de Felipe."
    assert "Se borraron los datos de Felipe." in respuesta.text
    assert sesion.get(Cliente, vehiculo.cliente_id).nombre == "Cliente borrado"


def test_no_se_borran_los_datos_de_quien_esta_en_la_fila(adentro: TestClient, sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, nombre="Felipe")
    nuevo = en_el_local(sesion, vehiculo=vehiculo)
    guardar(sesion)

    respuesta = adentro.post(f"/clientes/{vehiculo.cliente_id}/borrar", headers=HTMX)

    assert "HX-Retarget" not in respuesta.headers
    assert f"Tiene el turno {nuevo.numero} en la fila" in respuesta.text
    assert sesion.get(Cliente, vehiculo.cliente_id).nombre == "Felipe"


def test_dar_turno_desde_la_ficha_usa_el_mismo_cliente(adentro: TestClient, sesion: Session):
    vehiculo = cliente_con_vehiculo(sesion, nombre="Felipe", placa="JKL221", sin_telefono=True)

    dialogo = adentro.get(f"/turnos/nuevo?cliente_id={vehiculo.cliente_id}", headers=HTMX).text
    respuesta = adentro.post(
        "/turnos",
        headers=DESDE_CLIENTES,
        data={
            "cliente_id": vehiculo.cliente_id,
            "acepta_datos": "si",
            "tipo_vehiculo_id": tipo_vehiculo(sesion, "Automóvil").id,
            "placa": "JKL221",
            "servicio_id": servicio(sesion, "Básico").id,
        },
    )

    assert 'name="cliente_id"' in dialogo and 'value="JKL221"' in dialogo
    assert "autoriza el tratamiento" not in dialogo  # ya lo autorizó
    assert respuesta.headers["HX-Reswap"] == "none"  # en Clientes no hay fila que pintar
    assert eventos(respuesta)["datos-cambiados"] is True
    assert aviso(respuesta) == "Turno C-01 para Felipe."
    turno = sesion.scalars(select(Turno)).one()
    assert (turno.cliente_id, turno.vehiculo_id) == (vehiculo.cliente_id, vehiculo.id)
    assert sesion.scalars(select(Cliente)).all() == [sesion.get(Cliente, vehiculo.cliente_id)]


# --- Configuración -----------------------------------------------------------------------------


def test_la_pantalla_de_configuracion_muestra_lo_guardado(adentro: TestClient, sesion: Session):
    pagina = adentro.get("/configuracion").text

    assert 'value="Auto Ibaguer"' in pagina and 'value="322 755 6850"' in pagina
    assert 'value="05:30"' in pagina and 'value="19:30"' in pagina
    assert "Puesto C1" in pagina and "Puesto M4" in pagina
    basico = servicio(sesion, "Básico").id
    automovil = tipo_vehiculo(sesion, "Automóvil").id
    assert f'name="precio-{basico}-{automovil}" inputmode="numeric" value="40.000"' in pagina


def test_guardar_la_configuracion(adentro: TestClient, sesion: Session):
    basico = servicio(sesion, "Básico").id
    automovil = tipo_vehiculo(sesion, "Automóvil").id
    formulario = formulario_de_configuracion(
        sesion,
        hora_apertura="06:00",
        gracia_minutos="20",
        **{f"precio-{basico}-{automovil}": "45.000", f"duracion-{basico}": "50"},
    )
    formulario.pop("bot_activo")  # apagar el bot

    respuesta = adentro.post("/configuracion", headers=HTMX, data=formulario)

    assert aviso(respuesta).startswith("Configuración guardada.")
    parametros = leer_parametros(sesion)
    assert parametros.hora_apertura == time(6) and parametros.bot_activo is False
    assert datos.precio_vigente(sesion, basico, automovil).precio == 45_000
    assert datos.precio_vigente(sesion, basico, automovil).duracion_min == 50
    assert 'value="45.000"' in respuesta.text


def test_con_un_error_no_se_guarda_nada(adentro: TestClient, sesion: Session):
    basico = servicio(sesion, "Básico").id
    automovil = tipo_vehiculo(sesion, "Automóvil").id
    formulario = formulario_de_configuracion(
        sesion, hora_cierre="04:00", **{f"precio-{basico}-{automovil}": "45.000"}
    )

    respuesta = adentro.post("/configuracion", headers=HTMX, data=formulario)

    assert error(respuesta).startswith("No se guardó nada")
    assert "Debe ser después de la hora en que abre." in respuesta.text
    assert 'value="45.000"' in respuesta.text  # lo escrito no se pierde
    assert datos.precio_vigente(sesion, basico, automovil).precio == 40_000


def test_apagar_un_puesto_ocupado_se_explica_en_su_interruptor(
    adentro: TestClient, sesion: Session
):
    ocupar_puestos(sesion, "C1")
    guardar(sesion)
    c1 = puesto(sesion, "C1").id
    formulario = formulario_de_configuracion(sesion)
    formulario.pop(f"puesto-{c1}")

    respuesta = adentro.post("/configuracion", headers=HTMX, data=formulario)

    assert "se está lavando" in respuesta.text
    assert sesion.get(Puesto, c1).activo is True


def test_un_puesto_que_no_estaba_en_la_pantalla_no_se_apaga(adentro: TestClient, sesion: Session):
    formulario = formulario_de_configuracion(sesion)
    nuevo = adentro.post(
        "/configuracion/puestos", headers=HTMX, data={"fila_id": fila(sesion, "Carros").id}
    )
    c3 = sesion.scalars(select(Puesto).where(Puesto.nombre == "C3")).one()

    adentro.post("/configuracion", headers=HTMX, data=formulario)

    assert aviso(nuevo).startswith("Puesto C3 agregado")
    assert nuevo.headers["HX-Retarget"] == f"#puestos-{c3.fila_id}"
    assert sesion.get(Puesto, c3.id).activo is True


def test_agregar_un_servicio_desde_el_dialogo(adentro: TestClient, sesion: Session):
    motos = fila(sesion, "Motos").id
    moto = tipo_vehiculo(sesion, "Moto").id
    dialogo = adentro.get(f"/configuracion/servicios/nuevo?fila_id={motos}", headers=HTMX).text
    assert "Agregar servicio de motos" in dialogo

    malo = adentro.post(
        "/configuracion/servicios",
        headers=HTMX,
        data={"fila_id": motos, "nombre": "Básico", f"precio-{moto}": "10.000", "duracion": "30"},
    )
    bueno = adentro.post(
        "/configuracion/servicios",
        headers=HTMX,
        data={
            "fila_id": motos,
            "nombre": "Lavado de cadena",
            f"precio-{moto}": "12.000",
            "duracion": "20",
        },
    )

    assert "Ya hay un servicio que se llama Básico." in malo.text
    assert bueno.headers["HX-Retarget"] == f"#servicios-{motos}"
    assert aviso(bueno) == "Lavado de cadena ya está en el menú de motos."
    assert "<tr>" in bueno.text and 'value="12.000"' in bueno.text
    assert sesion.scalars(select(Servicio).where(Servicio.nombre == "Lavado de cadena")).one()


def test_un_turno_nuevo_desde_configuracion_no_pinta_la_fila(adentro: TestClient, sesion: Session):
    respuesta = adentro.post(
        "/turnos",
        headers={**HTMX, "HX-Current-URL": "http://testserver/configuracion"},
        data={
            "nombre": "Ana",
            "tipo_vehiculo_id": tipo_vehiculo(sesion, "Moto").id,
            "servicio_id": "",
            "acepta_datos": "si",
        },
    )

    assert respuesta.headers["HX-Reswap"] == "none"
    assert aviso(respuesta) == "Turno M-01 para Ana."
    assert sesion.scalars(select(Turno.estado)).one() == EstadoTurno.LLEGO
