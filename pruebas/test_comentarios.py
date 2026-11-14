"""Hito 7b: quejas, felicitaciones, "quiero hablar con una persona" y "cuántas lavadas
llevo". Primero lo que dice el bot (con un Gemini de mentira o sin Gemini), después la
pantalla Comentarios del dashboard y al final el borrado de datos (Ley 1581).
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.interprete import CARRO, Intencion, Interpretacion
from app.clientes import servicio as clientes
from app.clientes.tablas import Cliente, Vehiculo
from app.comentarios import servicio as comentarios
from app.comentarios.tablas import Comentario, TipoComentario
from app.turnos.estados import EstadoTurno, OrigenTurno
from app.turnos.tablas import Turno
from app.web.tablas import Administrador
from pruebas.fabricas import cliente_con_vehiculo, fila, por_whatsapp
from pruebas.navegador import HTMX, aviso, eventos
from pruebas.test_bot import CELULAR, MENU, Chat, paso
from pruebas.test_gemini import chat

Q = Intencion
T = TipoComentario
PERMISO = ["Sí, que me responda", "No, gracias"]


def guardados(sesion: Session) -> list[Comentario]:
    consulta = select(Comentario).order_by(Comentario.id)
    return list(sesion.scalars(consulta.execution_options(populate_existing=True)))


def ana(sesion: Session) -> Vehiculo:
    """Una clienta que ya pidió turno antes por este celular."""
    return cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz", placa="ABC123")


def lavada(
    sesion: Session,
    vehiculo: Vehiculo,
    dia: date,
    *,
    consecutivo: int,
    estado: EstadoTurno = EstadoTurno.PAGADO,
) -> Turno:
    """Un turno de otro día, ya terminado (sin pasar por las reglas de la fila)."""
    turno = Turno(
        fila_id=fila(sesion).id,
        fecha=dia,
        consecutivo=consecutivo,
        cliente_id=vehiculo.cliente_id,
        vehiculo_id=vehiculo.id,
        estado=estado,
        origen=OrigenTurno.DASHBOARD,
    )
    sesion.add(turno)
    sesion.flush()
    return turno


# --- Las cinco frases que el bot no entendía ----------------------------------------------


@pytest.mark.parametrize(
    ("frase", "entendido", "empieza"),
    [
        ("Oiga me atendieron mal", Interpretacion(Q.QUEJA), "Qué pena, Ana, con lo que pasó."),
        (
            "el carro quedó mal lavado",
            Interpretacion(Q.QUEJA, vehiculo=CARRO),
            "Qué pena, Ana, con lo que pasó.",
        ),
        # Esta la entiende sin preguntarle a Gemini.
        ("me gustaría saber cuántas lavadas llevo con ustedes", None, "Ana, llevas 1 lavada"),
        ("oigan su servicio es pésimo", Interpretacion(Q.QUEJA), "Qué pena, Ana, con lo que"),
        ("oiga su servicio es bueno", Interpretacion(Q.FELICITACION), "¡Muchas gracias, Ana!"),
    ],
)
def test_las_cinco_frases_que_el_bot_no_entendia(sesion: Session, frase, entendido, empieza):
    lavada(sesion, ana(sesion), date(2026, 10, 24), consecutivo=40)

    cliente = chat(sesion, entendido).escribe(frase)

    assert cliente.texto.startswith(empieza)
    assert "No te entendí" not in cliente.texto


@pytest.mark.parametrize(
    "frase",
    ["Oiga me atendieron mal", "el carro quedó mal lavado", "oigan su servicio es pésimo"],
)
def test_sin_gemini_una_queja_igual_se_reconoce(sesion: Session, frase):
    ana(sesion)

    chat_sin_gemini = Chat(sesion).escribe(frase)

    assert chat_sin_gemini.texto.startswith("Qué pena, Ana, con lo que pasó.")
    [queja] = guardados(sesion)
    assert (queja.tipo, queja.texto) == (T.QUEJA, frase)


def test_si_gemini_dice_que_no_es_una_queja_manda_gemini(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.OTRO)).escribe("el clima está pésimo, abren hoy?")

    assert guardados(sesion) == []
    assert "Qué pena" not in cliente.texto


def test_sin_gemini_una_queja_negada_no_es_queja(sesion: Session):
    Chat(sesion).escribe("no fue pésimo, todo bien")

    assert guardados(sesion) == []


# --- Quejas de un cliente -----------------------------------------------------------------


def test_la_queja_de_un_cliente_le_llega_al_administrador_con_lo_que_cuenta(sesion: Session):
    vehiculo = ana(sesion)
    turno = por_whatsapp(sesion, vehiculo=vehiculo).turno
    cliente = chat(sesion, Interpretacion(Q.QUEJA))

    cliente.escribe("me atendieron mal")
    assert cliente.texto == (
        "Qué pena, Ana, con lo que pasó. Ya le avisamos al administrador para que lo revise y "
        "te responda lo antes posible. Si quieres, cuéntanos más: qué pasó y cuándo."
    )
    assert cliente.opciones == []
    assert paso(sesion) == "detalle"

    cliente.escribe("El señor de la caja fue grosero conmigo esta mañana")
    assert cliente.texto == "Gracias, ya quedó anotado para el administrador."
    assert paso(sesion) == "menu"

    [queja] = guardados(sesion)
    assert queja.tipo == T.QUEJA
    assert queja.texto == "me atendieron mal\nEl señor de la caja fue grosero conmigo esta mañana"
    assert (queja.cliente_id, queja.telefono) == (vehiculo.cliente_id, CELULAR)
    assert queja.turno_id == turno.id  # su último turno: casi siempre, de eso habla
    assert queja.atendido_en is None


def test_despues_de_la_queja_un_ok_cierra_sin_anotarse(sesion: Session):
    ana(sesion)
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe("me atendieron mal")

    cliente.escribe("ok gracias")

    assert cliente.texto == "Gracias, ya quedó anotado para el administrador."
    assert guardados(sesion)[0].texto == "me atendieron mal"


def test_despues_de_la_queja_un_hola_empieza_otra_conversacion(sesion: Session):
    ana(sesion)
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe("me atendieron mal")

    cliente.escribe("Hola")

    assert cliente.texto.startswith("¡Hola, Ana!")
    assert cliente.opciones == MENU
    assert guardados(sesion)[0].texto == "me atendieron mal"


def test_una_foto_en_la_queja_pide_que_lo_escriba(sesion: Session):
    ana(sesion)
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe("me rayaron la puerta")

    cliente.manda_un_audio()

    assert cliente.texto.startswith("Por ahora no podemos ver fotos, audios ni videos")
    assert paso(sesion) == "detalle"  # sigue esperando lo que pasó


def test_si_en_la_queja_tambien_cancela_se_le_pregunta(sesion: Session):
    por_whatsapp(sesion, vehiculo=ana(sesion))
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe(
        "llevo una hora esperando, qué servicio"
    )

    cliente.escribe("ya no voy, cancélenme el turno")

    [anotado, pregunta] = cliente.ultimas
    assert anotado.texto == "Gracias, ya quedó anotado para el administrador."
    assert pregunta.texto.startswith("¿Seguro que quieres cancelar tu turno *C-01*?")
    assert cliente.opciones == ["Sí, cancelar", "No, mantenerlo"]  # nada se cancela solo
    assert "cancélenme" in guardados(sesion)[0].texto


def test_si_en_la_queja_pregunta_por_su_turno_tambien_se_le_dice(sesion: Session):
    por_whatsapp(sesion, vehiculo=ana(sesion))
    cliente = chat(sesion, Interpretacion(Q.QUEJA), Interpretacion(Q.MI_TURNO))
    cliente.escribe("se están demorando muchísimo, qué mal servicio")

    cliente.escribe("cuánto me falta?")

    assert cliente.ultimas[0].texto == "Gracias, ya quedó anotado para el administrador."
    assert cliente.ultimas[1].texto.startswith("Tu turno *C-01* (carros):")


def test_un_relato_con_ya_llegue_no_marca_la_llegada(sesion: Session):
    turno = por_whatsapp(sesion, vehiculo=ana(sesion)).turno
    antes = turno.estado
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe("la vez pasada me rayaron el carro")

    cliente.escribe("ya llegué a la casa y vi el rayón en la puerta")

    assert cliente.texto == "Gracias, ya quedó anotado para el administrador."
    sesion.refresh(turno)
    assert turno.estado == antes != EstadoTurno.LLEGO


def test_en_medio_de_pedir_turno_una_queja_cambia_de_tema(sesion: Session):
    vehiculo = ana(sesion)
    cliente = chat(sesion, None, Interpretacion(Q.QUEJA))  # el "hola" también lo lee Gemini
    cliente.escribe("hola").toca("Pedir turno")  # conoce su carro: le pregunta el servicio
    assert paso(sesion) == "servicio"

    cliente.escribe("antes quiero decir que la otra vez lo dejaron sucio")

    assert cliente.texto.startswith("Qué pena, Ana, con lo que pasó.")
    assert guardados(sesion)[0].cliente_id == vehiculo.cliente_id
    assert paso(sesion) == "detalle"


# --- Quejas de alguien que no es cliente ---------------------------------------------------


def test_de_quien_no_es_cliente_la_queja_llega_sin_su_numero(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe("me atendieron mal")

    assert cliente.texto.startswith("Qué pena con lo que pasó. Ya le avisamos al administrador.")
    assert cliente.opciones == PERMISO
    [queja] = guardados(sesion)
    assert (queja.cliente_id, queja.telefono, queja.permiso_en) == (None, None, None)
    assert paso(sesion) == "permiso_contacto"


def test_si_da_permiso_se_guarda_su_numero_para_responderle(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe("me atendieron mal")

    cliente.escribe("fue ayer en la tarde")  # cuenta más en vez de tocar un botón
    assert cliente.texto.startswith("Lo anotamos.\n\n¿Quieres que el administrador te responda?")
    assert cliente.opciones == PERMISO

    cliente.toca("Sí, que me responda")
    assert cliente.texto.startswith("Listo, el administrador te responde lo antes posible.")
    cliente.escribe("y el carro quedó sucio por dentro")
    assert cliente.texto == "Gracias, ya quedó anotado para el administrador."

    [queja] = guardados(sesion)
    assert queja.texto == (
        "me atendieron mal\nfue ayer en la tarde\ny el carro quedó sucio por dentro"
    )
    assert queja.telefono == CELULAR and queja.permiso_en is not None
    assert queja.cliente_id is None  # no se vuelve cliente por quejarse


def test_si_no_da_permiso_la_queja_queda_sin_su_numero(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe("me atendieron mal")

    cliente.escribe("no")  # un no escrito vale como el botón

    assert cliente.texto == (
        "Listo, tu comentario ya le llegó al administrador. Gracias por ayudarnos a mejorar."
    )
    [queja] = guardados(sesion)
    assert (queja.telefono, queja.permiso_en) == (None, None)


# --- Hablar con una persona -----------------------------------------------------------------


@pytest.mark.parametrize(
    "frase",
    [
        "quiero hablar con una persona",
        "Necesito hablar con el encargado",
        "me pasa con un asesor por favor",
        "que me atienda alguien de verdad",
    ],
)
def test_pedir_una_persona_se_entiende_sin_gemini(sesion: Session, frase):
    ana(sesion)

    cliente = Chat(sesion).escribe(frase)

    assert cliente.texto.startswith("Listo, Ana. Ya le avisamos al administrador")
    assert "llama al 322 755 6850" in cliente.texto
    [pedido] = guardados(sesion)
    assert (pedido.tipo, pedido.telefono) == (T.QUIERE_HABLAR, CELULAR)
    assert paso(sesion) == "detalle"


def test_quien_no_es_cliente_y_pide_una_persona_da_permiso_primero(sesion: Session):
    cliente = Chat(sesion).escribe("quiero hablar con una persona")

    assert cliente.texto.startswith("Con gusto te comunicamos con el administrador.")
    assert cliente.opciones == PERMISO
    assert guardados(sesion) == []  # sin su permiso no hay nada que guardar

    cliente.escribe("es por una factura")
    assert cliente.texto.startswith("Lo anotamos.\n\nCon gusto te comunicamos")
    assert guardados(sesion) == []

    cliente.toca("Sí, que me responda")
    [pedido] = guardados(sesion)
    assert pedido.tipo == T.QUIERE_HABLAR
    assert pedido.texto == "quiero hablar con una persona\nes por una factura"
    assert (pedido.telefono, pedido.cliente_id) == (CELULAR, None)


def test_si_no_da_permiso_no_se_guarda_nada_y_se_le_da_el_telefono(sesion: Session):
    cliente = Chat(sesion).escribe("quiero hablar con alguien")

    cliente.toca("No, gracias")

    assert cliente.texto == (
        "Listo, sin problema. Si quieres hablar con el administrador, llama al 322 755 6850."
    )
    assert guardados(sesion) == []
    assert paso(sesion) == "menu"


def test_escribir_el_boton_del_no_vale_igual(sesion: Session):
    cliente = Chat(sesion).escribe("quiero hablar con alguien")

    cliente.escribe("No, gracias")  # "gracias" no lo vuelve una despedida

    assert cliente.texto.startswith("Listo, sin problema. Si quieres hablar")
    assert guardados(sesion) == []


def test_hablar_con_una_persona_por_gemini(sesion: Session):
    ana(sesion)

    cliente = chat(sesion, Interpretacion(Q.HABLAR_CON_PERSONA)).escribe("me pueden llamar?")

    assert cliente.texto.startswith("Listo, Ana. Ya le avisamos al administrador")
    assert guardados(sesion)[0].tipo == T.QUIERE_HABLAR


# --- Felicitaciones -----------------------------------------------------------------------


def test_una_felicitacion_se_agradece_y_le_llega_al_administrador(sesion: Session):
    ana(sesion)

    cliente = chat(sesion, Interpretacion(Q.FELICITACION)).escribe("su servicio es muy bueno")

    assert cliente.texto == (
        "¡Muchas gracias, Ana! Nos alegra mucho que te guste el servicio. Se lo contamos al equipo."
    )
    assert cliente.opciones == MENU
    [felicitacion] = guardados(sesion)
    assert (felicitacion.tipo, felicitacion.texto) == (T.FELICITACION, "su servicio es muy bueno")


def test_una_felicitacion_en_medio_de_pedir_turno_no_lo_interrumpe(sesion: Session):
    ana(sesion)
    cliente = chat(sesion, None, Interpretacion(Q.FELICITACION))
    cliente.escribe("hola").toca("Pedir turno")

    cliente.escribe("ustedes son los mejores")

    assert cliente.texto.startswith("¡Muchas gracias, Ana!")
    assert "¿Qué servicio quieres para tu automóvil?" in cliente.texto  # sigue la pregunta
    assert paso(sesion) == "servicio"
    assert guardados(sesion)[0].tipo == T.FELICITACION


# --- Cuántas lavadas lleva ------------------------------------------------------------------


def test_cuantas_lavadas_lleva_con_los_datos_del_sistema(sesion: Session):
    vehiculo = ana(sesion)
    lavada(sesion, vehiculo, date(2026, 10, 3), consecutivo=40)
    lavada(sesion, vehiculo, date(2026, 10, 24), consecutivo=41, estado=EstadoTurno.LISTO)
    lavada(sesion, vehiculo, date(2026, 10, 25), consecutivo=42, estado=EstadoTurno.CANCELADO)

    cliente = Chat(sesion).escribe("cuántas lavadas llevo?")

    assert cliente.texto == (
        "Ana, llevas 2 lavadas con nosotros; la más reciente fue el sábado. "
        "¡Gracias por preferirnos!"
    )
    assert cliente.opciones == ["Pedir turno"]


def test_las_lavadas_por_gemini(sesion: Session):
    lavada(sesion, ana(sesion), date(2026, 10, 26), consecutivo=40)

    cliente = chat(sesion, Interpretacion(Q.MIS_LAVADAS)).escribe("cuándo fue la última vez?")

    assert cliente.texto.startswith("Ana, llevas 1 lavada con nosotros; la más reciente fue hoy.")


def test_un_cliente_sin_lavadas_y_alguien_que_no_es_cliente(sesion: Session):
    assert (
        Chat(sesion)
        .escribe("mis lavadas")
        .texto.startswith("No encontramos lavadas con este número de WhatsApp.")
    )

    ana(sesion)
    cliente = Chat(sesion).escribe("mis lavadas")
    assert cliente.texto == ("Ana, todavía no tienes lavadas con nosotros. ¿Pedimos la primera?")
    assert cliente.opciones == ["Pedir turno"]


def test_las_lavadas_de_otro_cliente_no_cuentan(sesion: Session):
    ana(sesion)
    otro = cliente_con_vehiculo(sesion, nombre="Luis Gómez")
    lavada(sesion, otro, date(2026, 10, 20), consecutivo=40)

    assert clientes.lavadas(sesion, otro.cliente_id) == (1, date(2026, 10, 20))
    assert Chat(sesion).escribe("cuantas lavadas tengo").texto.startswith("Ana, todavía no")


# --- La pantalla Comentarios ------------------------------------------------------------------


def comentario(sesion: Session, tipo: TipoComentario = T.QUEJA, texto: str = "me atendieron mal"):
    vehiculo = sesion.scalars(select(Vehiculo).where(Vehiculo.placa == "ABC123")).first()
    cliente = sesion.get(Cliente, vehiculo.cliente_id) if vehiculo else None
    return comentarios.registrar(sesion, tipo, texto, cliente=cliente)


def test_la_pantalla_pide_entrar(web: TestClient):
    respuesta = web.get("/comentarios", follow_redirects=False)

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/entrar"


def test_la_pantalla_muestra_lo_que_falta_por_atender(adentro: TestClient, sesion: Session):
    vehiculo = ana(sesion)
    por_whatsapp(sesion, vehiculo=vehiculo)
    comentario(sesion, texto="me atendieron mal\nfue esta mañana")
    comentarios.registrar(sesion, T.FELICITACION, "excelente servicio")  # de alguien sin datos

    pagina = adentro.get("/comentarios").text

    assert pagina.count('class="panel comentario"') == 2
    assert "Queja" in pagina and "Felicitación" in pagina
    assert "me atendieron mal\nfue esta mañana" in pagina  # con sus saltos de línea
    assert f'href="/clientes?cliente={vehiculo.cliente_id}">Ana Ruiz</a>' in pagina
    assert 'href="tel:+573001234567">Llamar</a>' in pagina
    assert "300 *** 4567" in pagina  # el número se ve tapado
    assert "último turno C-01, hoy" in pagina
    assert "No es cliente y no dejó su número" in pagina
    # El número del menú, arriba y en la barra del celular.
    assert '<span id="contador-comentarios" class="contador">2<' in pagina
    assert '<span id="contador-comentarios-celular" class="contador">2<' in pagina


def test_el_numero_del_menu_sale_en_todas_las_pantallas_del_lavadero(
    adentro: TestClient, sesion: Session
):
    assert 'id="contador-comentarios" class="contador" hidden' in adentro.get("/clientes").text

    comentario(sesion)

    assert '<span id="contador-comentarios" class="contador">1<' in adentro.get("/fila").text
    # Los comentarios son del lavadero: el parqueadero no los muestra ni los vuelve a pedir.
    parqueadero = adentro.get("/parqueadero").text
    assert "contador-comentarios" not in parqueadero
    assert "/comentarios/contador" not in parqueadero


def test_marcar_como_atendido_y_volver_a_pendiente(
    adentro: TestClient, sesion: Session, admin: Administrador
):
    ana(sesion)
    queja = comentario(sesion)

    respuesta = adentro.post(f"/comentarios/{queja.id}/atendido", headers=HTMX)

    assert respuesta.status_code == 200
    assert "Atendido por eduar" in respuesta.text
    assert "Volver a pendiente" in respuesta.text
    assert aviso(respuesta) == "Listo, quedó como atendido."
    assert eventos(respuesta)["comentarios-cambiados"] is True  # el número del menú se actualiza
    sesion.refresh(queja)
    assert queja.atendido_por == admin.id and queja.atendido_en is not None
    assert "No hay comentarios por atender." in adentro.get("/comentarios").text
    assert "me atendieron mal" in adentro.get("/comentarios?ver=todos").text

    respuesta = adentro.post(f"/comentarios/{queja.id}/pendiente", headers=HTMX)

    assert "Marcar como atendido" in respuesta.text
    sesion.refresh(queja)
    assert (queja.atendido_en, queja.atendido_por) == (None, None)


def test_el_contador_se_pide_solo_y_no_renueva_la_sesion(adentro: TestClient, sesion: Session):
    comentario(sesion)

    respuesta = adentro.get("/comentarios/contador", headers=HTMX)

    assert respuesta.text.count('hx-swap-oob="true"') == 2
    assert (
        '<span id="contador-comentarios" class="contador" hx-swap-oob="true">1<' in respuesta.text
    )
    assert "set-cookie" not in respuesta.headers  # pedirlo solo no deja la sesión abierta
    assert "set-cookie" in adentro.get("/comentarios").headers  # usar el dashboard, sí


def test_el_contador_sin_sesion_manda_a_entrar(web: TestClient):
    respuesta = web.get("/comentarios/contador", headers=HTMX)

    assert respuesta.headers["HX-Redirect"] == "/entrar"


def test_borrar_un_comentario(adentro: TestClient, sesion: Session):
    queja = comentario(sesion, texto="mensaje de prueba")

    dialogo = adentro.get(f"/comentarios/{queja.id}/borrar", headers=HTMX).text
    assert "Borrar este comentario" in dialogo and "mensaje de prueba" in dialogo

    respuesta = adentro.post(f"/comentarios/{queja.id}/borrar", headers=HTMX)

    assert respuesta.headers["HX-Retarget"] == f"#comentario-{queja.id}"
    assert respuesta.headers["HX-Reswap"] == "delete"  # la tarjeta sale de la lista
    assert eventos(respuesta)["cerrar-dialogo"] is True
    assert guardados(sesion) == []


def test_un_comentario_que_ya_no_existe(adentro: TestClient):
    respuesta = adentro.post("/comentarios/999999/atendido", headers=HTMX)

    assert respuesta.status_code == 404
    assert respuesta.json()["detail"] == "Ese comentario ya no existe."


def test_otro_sitio_no_puede_marcar_comentarios(adentro: TestClient, sesion: Session):
    queja = comentario(sesion)

    respuesta = adentro.post(
        f"/comentarios/{queja.id}/atendido", headers={"Origin": "https://otro-sitio.com"}
    )

    assert respuesta.status_code == 403
    sesion.refresh(queja)
    assert queja.atendido_en is None


def test_si_borran_el_comentario_mientras_cuenta_sus_palabras_no_se_pierden(sesion: Session):
    ana(sesion)
    cliente = chat(sesion, Interpretacion(Q.QUEJA)).escribe("me atendieron mal")
    comentarios.borrar(sesion, guardados(sesion)[0].id)

    cliente.escribe("el carro quedó sucio por dentro")

    [nuevo] = guardados(sesion)
    assert (nuevo.tipo, nuevo.texto) == (T.QUEJA, "el carro quedó sucio por dentro")


# --- Ley 1581: borrar los datos de un cliente -------------------------------------------------


def test_al_borrar_los_datos_del_cliente_se_borran_sus_comentarios(sesion: Session):
    vehiculo = ana(sesion)
    cliente = sesion.get(Cliente, vehiculo.cliente_id)
    comentarios.registrar(sesion, T.QUEJA, "me atendieron mal", cliente=cliente)
    # Lo que escribió antes de ser cliente, cuando dio permiso para que le respondieran.
    antes = comentarios.registrar(sesion, T.QUIERE_HABLAR, "quiero hablar con alguien")
    comentarios.dar_permiso(sesion, antes.id, CELULAR)
    de_otro = comentarios.registrar(sesion, T.FELICITACION, "excelente servicio")

    clientes.borrar_datos(sesion, cliente.id)

    assert [c.id for c in guardados(sesion)] == [de_otro.id]
