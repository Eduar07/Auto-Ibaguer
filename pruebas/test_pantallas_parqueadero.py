"""Las pantallas del parqueadero, como las usa el navegador: entrada, salida, caja, mensualidades,
tarifas, tickets y el archivo para Excel."""

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.parqueadero.estados import EstadoParqueo
from app.parqueadero.tablas import CajaParqueadero, Mensualidad, PagoMensualidad, Parqueo
from app.parqueadero.tarifas import leer_ajustes
from pruebas.fabricas import minutos
from pruebas.navegador import HTMX, aviso, error, eventos


def entrar(adentro: TestClient, placa: str = "abc123", tipo: str = "carro", **datos):
    return adentro.post(
        "/parqueadero/entradas", data={"placa": placa, "tipo": tipo, **datos}, headers=HTMX
    )


def parqueo(sesion: Session, placa: str = "ABC123") -> Parqueo:
    return sesion.scalars(select(Parqueo).where(Parqueo.placa == placa)).one()


def abrir_caja(adentro: TestClient, base: str = "50.000"):
    return adentro.post("/parqueadero/caja/abrir", data={"base": base}, headers=HTMX)


def test_la_pantalla_del_parqueadero(adentro: TestClient):
    respuesta = adentro.get("/parqueadero")

    assert respuesta.status_code == 200
    assert "Registrar entrada" in respuesta.text and "Cobrar salida" in respuesta.text
    assert "La caja está cerrada" in respuesta.text
    assert 'href="/parqueadero"' in respuesta.text  # la sección está en el menú


def test_sin_sesion_no_se_ve(web: TestClient):
    respuesta = web.get("/parqueadero", follow_redirects=False)

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/entrar"


def test_registrar_una_entrada_imprime_el_ticket(adentro: TestClient, sesion: Session):
    respuesta = entrar(adentro, "abc-123", observaciones="Deja casco")

    p = parqueo(sesion)
    assert respuesta.status_code == 200
    assert "ABC123" in respuesta.text and "Deja casco" in respuesta.text
    assert eventos(respuesta)["imprimir"] == f"/parqueadero/tickets/{p.id}"
    assert eventos(respuesta)["limpiar-entrada"] is True
    assert aviso(respuesta) == f"ABC123 entró con el ticket {p.id}."

    ticket = adentro.get(f"/parqueadero/tickets/{p.id}")
    assert "ABC123" in ticket.text and "$3.000 la hora" in ticket.text
    assert "15 minutos de gracia" in ticket.text and "Deja casco" in ticket.text
    # Solo el dashboard lo puede cargar en su marco para imprimirlo.
    assert ticket.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert ticket.headers["Content-Security-Policy"] == "frame-ancestors 'self'"


def test_una_entrada_repetida_avisa_y_no_cambia_la_pantalla(adentro: TestClient):
    entrar(adentro)

    respuesta = entrar(adentro, "ABC 123")

    assert respuesta.headers["HX-Reswap"] == "none"
    assert "ya está adentro" in error(respuesta)


def test_sin_caja_abierta_la_salida_no_se_puede_cobrar(adentro: TestClient, sesion: Session):
    entrar(adentro)

    dialogo = adentro.get("/parqueadero/salida", params={"q": "abc123"}, headers=HTMX)
    assert "La caja está cerrada" in dialogo.text
    respuesta = adentro.post(
        f"/parqueadero/{parqueo(sesion).id}/salida", data={"medio_pago": "efectivo"}, headers=HTMX
    )
    assert "Abre la caja para poder cobrar" in respuesta.text
    assert parqueo(sesion).estado is EstadoParqueo.ADENTRO


def test_buscar_una_placa_que_no_esta(adentro: TestClient):
    respuesta = adentro.get("/parqueadero/salida", params={"q": "zzz999"}, headers=HTMX)

    assert "No hay ningún vehículo adentro con ZZZ999" in error(respuesta)


def test_cobrar_con_billete_grande_da_el_vuelto_y_el_recibo(
    adentro: TestClient, sesion: Session, reloj: dict
):
    assert "HX-Refresh" in abrir_caja(adentro).headers
    entrar(adentro)
    reloj["ahora"] += minutos(120)
    p = parqueo(sesion)

    dialogo = adentro.get(f"/parqueadero/{p.id}/salida", headers=HTMX)
    assert "$6.000" in dialogo.text and "2 horas × $3.000" in dialogo.text
    respuesta = adentro.post(
        f"/parqueadero/{p.id}/salida",
        data={"medio_pago": "efectivo", "recibido": "50.000"},
        headers=HTMX,
    )

    assert aviso(respuesta) == "ABC123 salió: $6.000 en efectivo. Vuelto: $44.000."
    assert eventos(respuesta)["imprimir"] == f"/parqueadero/tickets/{p.id}/salida"
    assert respuesta.headers["HX-Retarget"] == "#adentro"
    recibo = adentro.get(f"/parqueadero/tickets/{p.id}/salida")
    assert "$6.000" in recibo.text and "$50.000" in recibo.text and "$44.000" in recibo.text

    caja = adentro.get("/parqueadero/caja")
    assert "Salida ABC123" in caja.text
    assert "$56.000" in caja.text  # en el cajón: base + lo cobrado, no + el billete


def test_un_segundo_cobro_cierra_el_dialogo_y_avisa(adentro: TestClient, sesion: Session):
    abrir_caja(adentro)
    entrar(adentro)
    p = parqueo(sesion)
    adentro.post(f"/parqueadero/{p.id}/salida", data={"medio_pago": "nequi"}, headers=HTMX)

    otra_vez = adentro.post(
        f"/parqueadero/{p.id}/salida", data={"medio_pago": "nequi"}, headers=HTMX
    )

    assert "ya salió" in error(otra_vez)
    assert eventos(otra_vez)["cerrar-dialogo"] is True


def test_cobrar_otro_valor_sin_motivo_queda_en_el_dialogo(adentro: TestClient, sesion: Session):
    abrir_caja(adentro)
    entrar(adentro)
    p = parqueo(sesion)

    respuesta = adentro.post(
        f"/parqueadero/{p.id}/salida",
        data={"medio_pago": "efectivo", "valor_cobrado": "1.000"},
        headers=HTMX,
    )

    assert "escribe el motivo" in respuesta.text
    assert 'value="1.000"' in respuesta.text  # no se pierde lo escrito


def test_anular_una_entrada(adentro: TestClient, sesion: Session):
    entrar(adentro)
    p = parqueo(sesion)

    sin_motivo = adentro.post(f"/parqueadero/{p.id}/anular", data={"motivo": " "}, headers=HTMX)
    assert "Escribe por qué se anula" in sin_motivo.text
    respuesta = adentro.post(
        f"/parqueadero/{p.id}/anular", data={"motivo": "Placa mal escrita"}, headers=HTMX
    )
    assert "Se anuló la entrada de ABC123" in aviso(respuesta)
    assert parqueo(sesion).estado is EstadoParqueo.ANULADO


def test_cerrar_la_caja_lleva_al_resumen_que_se_imprime_solo(adentro: TestClient, sesion: Session):
    abrir_caja(adentro)
    entrar(adentro)
    adentro.post(
        f"/parqueadero/{parqueo(sesion).id}/salida", data={"medio_pago": "efectivo"}, headers=HTMX
    )
    caja = sesion.scalars(select(CajaParqueadero)).one()

    dialogo = adentro.get("/parqueadero/caja/cerrar", headers=HTMX)
    assert "$53.000" in dialogo.text  # 50.000 de base + 3.000
    respuesta = adentro.post(
        "/parqueadero/caja/cerrar",
        data={"caja_id": caja.id, "efectivo_contado": "52.000", "nota": "faltó una moneda"},
        headers=HTMX,
    )

    assert respuesta.headers["HX-Redirect"] == f"/parqueadero/caja/{caja.id}?imprimir=1"
    resumen = adentro.get(f"/parqueadero/caja/{caja.id}?imprimir=1")
    assert f'data-imprimir-al-abrir="/parqueadero/caja/{caja.id}/ticket"' in resumen.text
    assert "Faltaron $1.000" in resumen.text
    ticket = adentro.get(f"/parqueadero/caja/{caja.id}/ticket")
    assert "Cierre de caja" in ticket.text and "Faltó $1.000" in ticket.text
    assert "Cierres anteriores" in adentro.get("/parqueadero/caja").text


def test_cerrar_la_caja_pide_lo_contado(adentro: TestClient, sesion: Session):
    abrir_caja(adentro)
    caja = sesion.scalars(select(CajaParqueadero)).one()

    respuesta = adentro.post(
        "/parqueadero/caja/cerrar", data={"caja_id": caja.id, "efectivo_contado": ""}, headers=HTMX
    )

    assert "Escribe cuánto efectivo contaste" in respuesta.text


def test_abrir_dos_cajas_avisa(adentro: TestClient):
    abrir_caja(adentro)

    assert "Ya hay una caja abierta" in error(abrir_caja(adentro))


def test_mensualidad_de_punta_a_punta(adentro: TestClient, sesion: Session):
    sin_autorizacion = adentro.post(
        "/parqueadero/mensualidades",
        data={
            "placa": "men123",
            "tipo": "carro",
            "titular": "ana ruiz",
            "valor_mensual": "120.000",
        },
        headers=HTMX,
    )
    assert "Ley 1581" in sin_autorizacion.text

    creada = adentro.post(
        "/parqueadero/mensualidades",
        data={
            "placa": "men123",
            "tipo": "carro",
            "titular": "ana ruiz",
            "telefono": "322 755 6850",
            "valor_mensual": "120.000",
            "autorizo": "si",
        },
        headers=HTMX,
    )
    assert creada.headers["HX-Retarget"] == "#lista-mensualidades"
    assert "MEN123" in creada.text and "el primer mes" in creada.text
    mensualidad = sesion.scalars(select(Mensualidad)).one()

    abrir_caja(adentro)
    dialogo = adentro.get(f"/parqueadero/mensualidades/{mensualidad.id}/pagar", headers=HTMX)
    assert 'value="2026-10-26"' in dialogo.text and 'value="2026-11-24"' in dialogo.text
    assert 'value="120.000"' in dialogo.text
    pagada = adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar",
        data={
            "desde": "2026-10-26",
            "hasta": "2026-11-24",
            "valor": "120.000",
            "medio_pago": "nequi",
        },
        headers=HTMX,
    )
    assert "Al día hasta el 24/11" in pagada.text
    recibo = eventos(pagada)["imprimir"]
    assert "26/10/2026" in adentro.get(recibo).text

    entrar(adentro, "MEN123")
    p = parqueo(sesion, "MEN123")
    salida = adentro.post(f"/parqueadero/{p.id}/salida", headers=HTMX)
    assert aviso(salida) == "MEN123 salió sin pagar: mensualista al día."
    assert "imprimir" not in eventos(salida)


def mensualidad_vencida(adentro: TestClient, sesion: Session) -> Mensualidad:
    """MEN123 pagó hasta el 23 de octubre: el lunes 26 ya debe 3 días."""
    adentro.post(
        "/parqueadero/mensualidades",
        data={
            "placa": "men123",
            "tipo": "carro",
            "titular": "ana ruiz",
            "valor_mensual": "120.000",
            "autorizo": "si",
        },
        headers=HTMX,
    )
    mensualidad = sesion.scalars(select(Mensualidad)).one()
    abrir_caja(adentro)
    adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar",
        data={
            "desde": "2026-09-24",
            "hasta": "2026-10-23",
            "valor": "120.000",
            "medio_pago": "efectivo",
        },
        headers=HTMX,
    )
    return mensualidad


def test_la_mensualidad_vencida_se_ve_en_rojo_donde_se_atiende(
    adentro: TestClient, sesion: Session
):
    mensualidad_vencida(adentro, sesion)

    pagina = adentro.get("/parqueadero").text
    assert "1 mensualidad pendiente de pago" in pagina and "(desde el 24/10)" in pagina
    lista = adentro.get("/parqueadero/mensualidades").text
    assert "Pendiente de pago" in lista and "desde el 24/10 · 3 días" in lista
    assert "parqueo--debe" in lista

    entrada = entrar(adentro, "MEN123")

    p = parqueo(sesion, "MEN123")
    assert aviso(entrada) == (
        f"MEN123 entró con el ticket {p.id}. Su mensualidad está pendiente de pago: cóbrale el mes."
    )
    assert "Mensualidad pendiente" in entrada.text


def mensualidad_pagada_hasta_el_24(adentro: TestClient, sesion: Session) -> Mensualidad:
    """MEN123 pagó el lunes 26 de octubre su primer mes: del 26/10 al 24/11."""
    adentro.post(
        "/parqueadero/mensualidades",
        data={
            "placa": "men123",
            "tipo": "moto",
            "titular": "ana ruiz",
            "valor_mensual": "200.000",
            "autorizo": "si",
        },
        headers=HTMX,
    )
    mensualidad = sesion.scalars(select(Mensualidad)).one()
    abrir_caja(adentro)
    adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar",
        data={
            "desde": "2026-10-26",
            "hasta": "2026-11-24",
            "valor": "200.000",
            "medio_pago": "efectivo",
        },
        headers=HTMX,
    )
    return mensualidad


def pagos(sesion: Session) -> int:
    return len(sesion.scalars(select(PagoMensualidad)).all())


def test_al_dia_no_deja_cobrar_otro_mes_sin_marcar_que_es_adelantado(
    adentro: TestClient, sesion: Session
):
    mensualidad = mensualidad_pagada_hasta_el_24(adentro, sesion)

    # En la lista: al día, cuándo toca el próximo cobro y ningún botón "Cobrar mes".
    lista = adentro.get("/parqueadero/mensualidades").text
    assert "Al día hasta el 24/11" in lista and "Próximo cobro desde el 21/11" in lista
    assert "Cobrar mes</button>" not in lista and "Adelantar mes" in lista

    # El cobro, si lo abren, avisa que es un adelanto y pide marcarlo.
    dialogo = adentro.get(f"/parqueadero/mensualidades/{mensualidad.id}/pagar", headers=HTMX)
    assert "Adelantar mensualidad" in dialogo.text and 'name="adelantado"' in dialogo.text
    assert 'value="2026-11-25"' in dialogo.text and 'value="2026-12-24"' in dialogo.text
    datos = {
        "desde": "2026-11-25",
        "hasta": "2026-12-24",
        "valor": "200.000",
        "medio_pago": "efectivo",
    }

    sin_marcar = adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar", data=datos, headers=HTMX
    )

    assert "Ya pagó hasta el 24 de noviembre de 2026" in sin_marcar.text
    assert "se cobra desde el 21 de noviembre de 2026" in sin_marcar.text
    assert pagos(sesion) == 1  # no cobró nada

    adelantado = adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar",
        data={**datos, "adelantado": "si"},
        headers=HTMX,
    )

    assert "pagada hasta el 24 de diciembre de 2026" in aviso(adelantado)
    assert pagos(sesion) == 2


def test_los_estados_de_la_mensualidad_a_lo_largo_del_mes(
    adentro: TestClient, sesion: Session, reloj: dict
):
    mensualidad = mensualidad_pagada_hasta_el_24(adentro, sesion)
    lunes = reloj["ahora"]

    # 21 de noviembre: se acaba en 3 días; ya se puede cobrar, en naranja.
    reloj["ahora"] = lunes + minutos(26 * 24 * 60)
    lista = adentro.get("/parqueadero/mensualidades").text
    assert "Vence el 24/11" in lista and "en 3 días" in lista
    assert "Cobrar mes</button>" in lista and "Adelantar mes" not in lista
    dialogo = adentro.get(f"/parqueadero/mensualidades/{mensualidad.id}/pagar", headers=HTMX)
    assert "Cobrar mensualidad" in dialogo.text and 'name="adelantado"' not in dialogo.text

    # 24 de noviembre: es el último día pagado.
    reloj["ahora"] = lunes + minutos(29 * 24 * 60)
    assert "hoy es el último día" in adentro.get("/parqueadero/mensualidades").text

    # 27 de noviembre: pendiente de pago desde el 25, en rojo, también donde se atiende.
    reloj["ahora"] = lunes + minutos(32 * 24 * 60)
    lista = adentro.get("/parqueadero/mensualidades").text
    assert "Pendiente de pago" in lista and "desde el 25/11 · 3 días" in lista
    assert "parqueo--debe" in lista
    assert "(desde el 25/11)" in adentro.get("/parqueadero").text

    # Paga: el mes va del 25/11 al 24/12 (los días de atraso no se regalan) y vuelve a verde.
    pagada = adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar",
        data={
            "desde": "2026-11-25",
            "hasta": "2026-12-24",
            "valor": "200.000",
            "medio_pago": "nequi",
        },
        headers=HTMX,
    )
    assert "Al día hasta el 24/12" in pagada.text
    assert "pendiente de pago" not in adentro.get("/parqueadero/por-cobrar", headers=HTMX).text


def test_cobrar_el_mes_desde_la_salida_y_salir_sin_pagar_horas(
    adentro: TestClient, sesion: Session, reloj: dict
):
    mensualidad = mensualidad_vencida(adentro, sesion)
    entrar(adentro, "MEN123")
    p = parqueo(sesion, "MEN123")
    reloj["ahora"] += minutos(120)

    salida = adentro.get(f"/parqueadero/{p.id}/salida", headers=HTMX)
    assert f"/parqueadero/mensualidades/{mensualidad.id}/pagar?salida={p.id}" in salida.text

    dialogo = adentro.get(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar",
        params={"salida": p.id},
        headers=HTMX,
    )
    # El mes sigue desde que se venció, no desde hoy.
    assert 'value="2026-10-24"' in dialogo.text and 'value="2026-11-22"' in dialogo.text
    assert f'name="salida" value="{p.id}"' in dialogo.text
    assert "Volver a la salida" in dialogo.text

    pagada = adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/pagar",
        data={
            "desde": "2026-10-24",
            "hasta": "2026-11-22",
            "valor": "120.000",
            "medio_pago": "efectivo",
            "salida": str(p.id),
        },
        headers=HTMX,
    )

    # Vuelve a la salida, que ya lo ve al día, e imprime el recibo del mes.
    assert "sale sin pagar" in pagada.text
    assert "pagada hasta el 22 de noviembre" in aviso(pagada)
    assert eventos(pagada)["imprimir"].startswith("/parqueadero/mensualidades/pagos/")
    # El aviso rojo de arriba se actualiza solo: ya no la debe.
    assert eventos(pagada)["mensualidad-pagada"] is True
    assert "MEN123" not in adentro.get("/parqueadero/por-cobrar", headers=HTMX).text
    afuera = adentro.post(f"/parqueadero/{p.id}/salida", headers=HTMX)
    assert aviso(afuera) == "MEN123 salió sin pagar: mensualista al día."


def test_la_caja_de_ayer_pide_que_la_cierren(adentro: TestClient, reloj: dict):
    abrir_caja(adentro)
    assert "La caja está abierta desde" not in adentro.get("/parqueadero").text

    reloj["ahora"] += minutos(24 * 60)

    assert "La caja está abierta desde ayer a las 8:00 a.m." in adentro.get("/parqueadero").text
    assert "viene de un día anterior" in adentro.get("/parqueadero/caja").text


def test_mensualista_que_ya_traia_dias_pagados(adentro: TestClient, sesion: Session):
    datos = {
        "placa": "men123",
        "tipo": "moto",
        "titular": "ana ruiz",
        "valor_mensual": "60.000",
        "autorizo": "si",
    }
    mala = adentro.post(
        "/parqueadero/mensualidades", data={**datos, "pagada_hasta": "2026-10-20"}, headers=HTMX
    )
    assert "ya pasó" in mala.text
    assert 'value="2026-10-20"' in mala.text  # no se pierde lo escrito

    creada = adentro.post(
        "/parqueadero/mensualidades", data={**datos, "pagada_hasta": "2026-11-03"}, headers=HTMX
    )

    assert aviso(creada) == (
        "Mensualidad de MEN123 creada: al día hasta el 3 de noviembre de 2026, "
        "sin entrar plata a la caja."
    )
    assert "Al día hasta el 3/11" in creada.text
    mensualidad = sesion.scalars(select(Mensualidad)).one()
    dialogo = adentro.get(f"/parqueadero/mensualidades/{mensualidad.id}/pagar", headers=HTMX)
    assert 'value="2026-11-04"' in dialogo.text  # el primer cobro, el día siguiente
    assert "ya lo traía pagado" in dialogo.text
    pago = sesion.scalars(select(PagoMensualidad)).one()
    assert adentro.get(f"/parqueadero/mensualidades/pagos/{pago.id}/ticket").status_code == 404
    archivo = adentro.get(
        "/parqueadero/exportar", params={"desde": "2026-10-26", "hasta": "2026-10-26"}
    )
    assert "MEN123" not in archivo.content.decode("utf-8")  # no entró plata


def test_dar_de_baja_una_mensualidad(adentro: TestClient, sesion: Session):
    adentro.post(
        "/parqueadero/mensualidades",
        data={
            "placa": "men123",
            "tipo": "moto",
            "titular": "Ana",
            "valor_mensual": "60000",
            "autorizo": "si",
        },
        headers=HTMX,
    )
    mensualidad = sesion.scalars(select(Mensualidad)).one()

    dialogo = adentro.get(f"/parqueadero/mensualidades/{mensualidad.id}/baja", headers=HTMX)
    assert 'name="borrar_datos"' in dialogo.text

    respuesta = adentro.post(
        f"/parqueadero/mensualidades/{mensualidad.id}/baja",
        data={"borrar_datos": "si"},
        headers=HTMX,
    )

    assert "ya no es mensualista" in aviso(respuesta)
    assert "Se borraron el nombre" in aviso(respuesta)
    assert "MEN123" not in respuesta.text
    sesion.refresh(mensualidad)
    assert (mensualidad.titular, mensualidad.telefono) == ("Datos borrados", None)


def test_guardar_tarifas_y_datos_del_ticket(adentro: TestClient, sesion: Session):
    assert "Hora de carro" in adentro.get("/parqueadero/tarifas").text

    datos = {
        "hora_carro": "3.500",
        "hora_moto": "1.500",
        "fraccion_carro": "1.000",
        "fraccion_moto": "",
        "gracia_minutos": "10",
        "nombre": "Parqueadero Ibaguer",
        "nit": "",
        "direccion": "Cra. 2 #26-80",
        "telefono": "",
        "pie_ticket": "Gracias por su visita",
        "ancho_ticket": "58",
    }
    respuesta = adentro.post("/parqueadero/tarifas", data=datos, headers=HTMX)

    assert "Tarifas guardadas" in aviso(respuesta)
    ajustes = leer_ajustes(sesion)
    assert (ajustes.hora_carro, ajustes.fraccion_carro, ajustes.fraccion_moto) == (3500, 1000, None)
    assert (ajustes.gracia_minutos, ajustes.ancho_ticket) == (10, 58)

    malo = adentro.post("/parqueadero/tarifas", data={**datos, "hora_moto": "0"}, headers=HTMX)
    assert "No se guardó nada" in malo.text


def test_el_ticket_dice_nit_y_el_numero(adentro: TestClient, sesion: Session):
    datos = {
        "hora_carro": "3.000",
        "hora_moto": "1.500",
        "gracia_minutos": "15",
        "nombre": "Parqueadero Ibaguer",
        "direccion": "Cra. 2 #26-80",
        "telefono": "300 000 0000",
        "pie_ticket": "",
        "ancho_ticket": "80",
    }
    # Aunque escriban la palabra, en el ticket sale una sola vez: "NIT 900.123.456-7".
    adentro.post("/parqueadero/tarifas", data={**datos, "nit": "NIT: 900.123.456-7"}, headers=HTMX)
    assert leer_ajustes(sesion).nit == "900.123.456-7"
    entrar(adentro)

    ticket = adentro.get(f"/parqueadero/tickets/{parqueo(sesion).id}").text

    assert "NIT 900.123.456-7" in ticket and "NIT NIT" not in ticket
    assert "Tel. 300 000 0000" in ticket
    malo = adentro.post(
        "/parqueadero/tarifas", data={**datos, "nit": "Parqueadero SAS"}, headers=HTMX
    )
    assert "El NIT es solo el número" in malo.text


def test_el_archivo_para_excel(adentro: TestClient, sesion: Session, reloj: dict):
    abrir_caja(adentro)
    entrar(adentro)
    reloj["ahora"] += minutos(120)
    adentro.post(
        f"/parqueadero/{parqueo(sesion).id}/salida", data={"medio_pago": "efectivo"}, headers=HTMX
    )

    archivo = adentro.get(
        "/parqueadero/exportar", params={"desde": "2026-10-26", "hasta": "2026-10-26"}
    )

    assert archivo.headers["content-type"].startswith("text/csv")
    assert "parqueadero-2026-10-26-a-2026-10-26.csv" in archivo.headers["content-disposition"]
    texto = archivo.content.decode("utf-8")
    assert texto.startswith("﻿Fecha;Hora;Concepto")
    assert ";ABC123;Carro;2 h;Efectivo;6000;" in texto
