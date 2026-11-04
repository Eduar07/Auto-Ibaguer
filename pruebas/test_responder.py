"""Hito 7b, parte 3: responderle al cliente por WhatsApp desde Comentarios.

Primero las reglas (24 horas de WhatsApp, el mensaje en la bandeja de salida), después la
pausa del bot mientras le responde una persona, la conversación que se muestra, el
diálogo del dashboard, Probar el bot y al final el borrado de datos (Ley 1581).
"""

import itertools
import re
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app.bot import pausa
from app.bot.mensajes import Mensaje
from app.bot.tablas import PausaDelBot
from app.bot.textos import TEXTOS
from app.catalogo.parametros import leer_parametros
from app.clientes import servicio as clientes
from app.clientes.tablas import Cliente, Vehiculo
from app.comentarios import respuestas
from app.comentarios import servicio as comentarios
from app.comentarios.respuestas import NoSePuedeResponder, Quien
from app.comentarios.tablas import Comentario, TipoComentario
from app.config import obtener_entorno
from app.integraciones import bandeja_salida, whatsapp
from app.integraciones.bandeja_salida import TIPO_DEL_ADMINISTRADOR, EnvioFallido
from app.integraciones.tablas import EstadoMensaje, MensajeEntrante, MensajeSaliente
from app.reloj import proceso, tareas
from app.turnos import casos_de_uso as casos
from app.turnos.estados import EstadoTurno
from app.web.tablas import Administrador
from pruebas.fabricas import (
    LUNES_8AM,
    en_el_local,
    estado,
    minutos,
    ocupar_puestos,
    por_whatsapp,
)
from pruebas.navegador import HTMX, eventos, guardar
from pruebas.test_bot import CELULAR, MENU, Chat, paso
from pruebas.test_comentarios import ana, guardados

AHORA = LUNES_8AM
UNA_HORA = timedelta(hours=1)
HACE_UN_RATO = AHORA - timedelta(minutes=10)  # 7:50
PRUEBA = "579912345678"  # un celular de Probar el bot
_ids = itertools.count(1)


def llego(
    sesion: Session,
    texto: str | None = "me atendieron mal",
    *,
    cuando: datetime = HACE_UN_RATO,
    telefono: str = CELULAR,
    tipo: str = "text",
    contenido: dict | None = None,
) -> MensajeEntrante:
    """Un mensaje que llegó por WhatsApp y el bot ya atendió."""
    mensaje = MensajeEntrante(
        id_meta=f"wamid.prueba.{next(_ids)}",
        telefono=telefono,
        tipo=tipo,
        contenido=contenido if contenido is not None else {"texto": texto},
        recibido_en=cuando,
        procesado_en=cuando,
    )
    sesion.add(mensaje)
    sesion.flush()
    return mensaje


def queja_de_ana(sesion: Session) -> Comentario:
    """Ana, que es clienta, se quejó por WhatsApp hace 10 minutos (a las 7:50)."""
    cliente = sesion.get(Cliente, ana(sesion).cliente_id)
    llego(sesion)
    return comentarios.registrar(
        sesion,
        TipoComentario.QUEJA,
        "me atendieron mal",
        cliente=cliente,
        ahora=AHORA - minutos(10),
    )


def responder(
    sesion: Session,
    queja: Comentario,
    texto: str = "Hola Ana, te habla Eduar. ¿Qué pasó?",
    *,
    admin: Administrador,
    ahora: datetime = AHORA,
) -> MensajeSaliente:
    return respuestas.responder(
        sesion, queja.id, texto, administrador_id=admin.id, usuario=admin.usuario, ahora=ahora
    )


def del_administrador(sesion: Session) -> list[MensajeSaliente]:
    consulta = select(MensajeSaliente).where(MensajeSaliente.tipo == TIPO_DEL_ADMINISTRADOR)
    return list(sesion.scalars(consulta.execution_options(populate_existing=True)))


# --- Las reglas -------------------------------------------------------------------------------


def test_responder_deja_el_mensaje_pone_la_pausa_y_marca_el_comentario(
    sesion: Session, admin: Administrador
):
    queja = queja_de_ana(sesion)

    mensaje = responder(
        sesion, queja, "  Hola Ana, ¿qué pasó?  \r\n\r\n\r\n\r\n  Cuéntanos  ", admin=admin
    )

    # Sale por la bandeja de salida, con sus reintentos, como las respuestas del bot.
    assert (mensaje.telefono, mensaje.tipo, mensaje.estado) == (
        CELULAR,
        TIPO_DEL_ADMINISTRADOR,
        EstadoMensaje.PENDIENTE,
    )
    assert mensaje.contenido == {"texto": "Hola Ana, ¿qué pasó?\n\nCuéntanos", "por": "eduar"}
    assert (mensaje.cliente_id, mensaje.creado_en) == (queja.cliente_id, AHORA)
    vigente = pausa.pausa_de(sesion, CELULAR, AHORA)
    assert (vigente.hasta, vigente.comentario_id, vigente.administrador_id) == (
        AHORA + UNA_HORA,
        queja.id,
        admin.id,
    )
    sesion.refresh(queja)
    assert (queja.atendido_en, queja.atendido_por) == (AHORA, admin.id)


def test_cada_mensaje_del_administrador_alarga_la_pausa(sesion: Session, admin: Administrador):
    queja = queja_de_ana(sesion)

    responder(sesion, queja, admin=admin)
    responder(sesion, queja, "¿Sigues ahí?", admin=admin, ahora=AHORA + minutos(40))

    assert pausa.pausa_de(sesion, CELULAR, AHORA).hasta == AHORA + minutos(100)
    assert len(sesion.scalars(select(PausaDelBot)).all()) == 1  # una por celular


@pytest.mark.parametrize(
    ("texto", "motivo"),
    [
        ("   \n  ", "Escribe lo que le quieres decir."),
        ("a" * 1001, "El mensaje puede tener hasta 1000 caracteres; este tiene 1001."),
    ],
)
def test_un_mensaje_vacio_o_muy_largo_no_sale(
    sesion: Session, admin: Administrador, texto: str, motivo: str
):
    queja = queja_de_ana(sesion)

    with pytest.raises(NoSePuedeResponder) as error:
        responder(sesion, queja, texto, admin=admin)

    assert str(error.value) == motivo
    assert del_administrador(sesion) == []
    assert pausa.pausa_de(sesion, CELULAR, AHORA) is None


def test_whatsapp_solo_deja_escribirle_hasta_24_horas_despues_de_su_ultimo_mensaje(
    sesion: Session, admin: Administrador
):
    queja = queja_de_ana(sesion)  # escribió a las 7:50
    mañana = AHORA - minutos(10) + timedelta(hours=24)

    assert respuestas.hasta_cuando_se_puede(sesion, CELULAR, AHORA) == mañana
    assert respuestas.hasta_cuando_se_puede(sesion, CELULAR, mañana) is None
    with pytest.raises(NoSePuedeResponder) as error:
        responder(sesion, queja, admin=admin, ahora=mañana)

    assert str(error.value).startswith("WhatsApp solo deja escribirle hasta 24 horas después")
    assert del_administrador(sesion) == []
    # Si vuelve a escribir, la ventana se abre otra vez.
    llego(sesion, "hola?", cuando=mañana)
    assert responder(sesion, queja, admin=admin, ahora=mañana + minutos(5))


def test_sin_celular_no_hay_a_quien_responderle(sesion: Session, admin: Administrador):
    anonima = comentarios.registrar(sesion, TipoComentario.FELICITACION, "excelente servicio")

    with pytest.raises(NoSePuedeResponder) as error:
        responder(sesion, anonima, "Gracias", admin=admin)

    assert str(error.value) == "Este comentario no tiene celular: no hay a quién responderle."


@pytest.mark.parametrize(
    ("cliente", "saludo"),
    [
        ("Ana Ruiz", "Hola Ana, te habla Eduar, de Auto Ibaguer. "),
        (None, "Hola, te habla Eduar, de Auto Ibaguer. "),  # no es cliente: no se sabe el nombre
    ],
)
def test_el_primer_mensaje_empieza_diciendo_quien_le_escribe(cliente, saludo):
    assert respuestas.saludo_sugerido(cliente, "eduar", "Auto Ibaguer") == saludo


def test_lo_del_administrador_sale_por_whatsapp_como_texto(sesion: Session, admin: Administrador):
    mensaje = responder(sesion, queja_de_ana(sesion), "Hola Ana", admin=admin)

    contenido = whatsapp.contenido_para_meta(whatsapp.mensaje_del_bot(sesion, mensaje), CELULAR)

    assert (contenido["to"], contenido["type"], contenido["text"]["body"]) == (
        CELULAR,
        "text",
        "Hola Ana",
    )


def test_a_un_celular_de_prueba_nunca_se_le_escribe_por_whatsapp(sesion: Session):
    pedidos: list[httpx.Request] = []

    def meta(peticion: httpx.Request) -> httpx.Response:
        pedidos.append(peticion)
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    enviador = whatsapp.WhatsApp(
        obtener_entorno(), http=httpx.Client(transport=httpx.MockTransport(meta))
    )
    mensaje = bandeja_salida.encolar_del_administrador(
        sesion, telefono=PRUEBA, texto="Hola", por="eduar", ahora=AHORA
    )

    with pytest.raises(EnvioFallido) as error:
        enviador.enviar(sesion, mensaje)

    assert str(error.value) == "Es un celular de prueba (Probar el bot): no sale por WhatsApp."
    assert error.value.reintentar is False
    assert pedidos == []  # ni se intenta: Meta nunca ve un número inventado


# --- El bot en pausa mientras le responde una persona ----------------------------------------


def ana_se_queja(sesion: Session) -> tuple[Chat, Comentario]:
    """Ana se queja por WhatsApp (sin Gemini: la queja igual se reconoce)."""
    ana(sesion)
    llego(sesion, cuando=AHORA)
    cliente = Chat(sesion).escribe("me atendieron mal")
    [queja] = guardados(sesion)
    return cliente, queja


def test_en_pausa_el_bot_no_se_mete_y_lo_que_escribe_le_llega_al_administrador(
    sesion: Session, admin: Administrador
):
    cliente, queja = ana_se_queja(sesion)
    assert paso(sesion) == "detalle"  # el bot le pidió que contara más
    responder(sesion, queja, admin=admin)

    cliente.pasan(2).escribe("fue el señor de la caja, esta mañana")

    assert cliente.ultimas == []  # el bot no contesta: le está respondiendo una persona
    [queja] = guardados(sesion)
    assert queja.atendido_en is None  # vuelve a "por atender", para que se vea en el menú
    assert queja.texto == "me atendieron mal"  # lo nuevo está en la conversación
    assert paso(sesion) == "menu"  # después de la pausa, el bot empieza desde el menú
    # Una foto tampoco la contesta el bot.
    comentarios.marcar_atendido(sesion, queja.id, admin.id)
    assert cliente.manda_un_audio().ultimas == []
    assert guardados(sesion)[0].atendido_en is None
    # Con otro celular, el bot sigue normal.
    assert Chat(sesion, telefono="573109876543").escribe("hola").opciones == MENU


def test_en_pausa_lo_de_la_fila_si_lo_atiende_el_bot(sesion: Session, admin: Administrador):
    cliente, queja = ana_se_queja(sesion)
    vehiculo_de_ana = sesion.scalars(select(Vehiculo).where(Vehiculo.placa == "ABC123")).one()
    nuevo = por_whatsapp(sesion, vehiculo=vehiculo_de_ana)  # llamado hasta las 8:15
    responder(sesion, queja, admin=admin)

    # Un relato no es un pedido: es para la persona.
    cliente.pasan(1).escribe("ya llegué a la casa y vi que el carro tiene un rayón")
    assert cliente.ultimas == []
    assert estado(sesion, nuevo.turno.id) == EstadoTurno.LLAMADO
    comentarios.marcar_atendido(sesion, queja.id, admin.id)

    cliente.pasan(1).escribe("voy en camino")
    assert cliente.texto.startswith("Gracias por avisar, te esperamos.")
    cliente.pasan(1).escribe("¡Ya llegué!")
    assert cliente.texto.startswith(f"¡Bienvenido! Ya te marcamos como llegado ({nuevo.numero})")
    assert estado(sesion, nuevo.turno.id) == EstadoTurno.LLEGO
    cliente.pasan(1).escribe("mi turno")
    assert cliente.ultimas != []
    assert guardados(sesion)[0].atendido_en is not None  # nada de esto era para la persona


def test_en_pausa_el_codigo_qr_del_local_si_lo_atiende_el_bot(
    sesion: Session, admin: Administrador
):
    cliente, queja = ana_se_queja(sesion)
    vehiculo_de_ana = sesion.scalars(select(Vehiculo).where(Vehiculo.placa == "ABC123")).one()
    nuevo = en_el_local(sesion, vehiculo=vehiculo_de_ana)
    responder(sesion, queja, admin=admin)

    cliente.pasan(5).escribe("Hola, ya estoy en la fila")

    assert cliente.texto.startswith(f"Listo: quedaste conectado al turno *{nuevo.numero}*.")


def test_en_pausa_los_botones_y_lo_que_va_haciendo_con_el_bot_siguen(
    sesion: Session, admin: Administrador
):
    cliente, queja = ana_se_queja(sesion)
    responder(sesion, queja, admin=admin)

    cliente.toca_el_id("menu.precios")  # un botón de un mensaje anterior: es una orden
    assert cliente.texto == "¿Para qué vehículo quieres ver los precios?"
    cliente.escribe("moto")  # le contesta al bot: va en algo con él
    assert cliente.texto.startswith("Servicios para moto:")

    assert guardados(sesion)[0].atendido_en is not None


def test_en_pausa_un_mensaje_grave_recibe_las_lineas_de_ayuda(
    sesion: Session, admin: Administrador
):
    cliente, queja = ana_se_queja(sesion)
    responder(sesion, queja, admin=admin)

    cliente.pasan(1).escribe("me quiero morir")

    assert cliente.texto == TEXTOS["ayuda_urgente"]
    assert guardados(sesion)[0].atendido_en is None  # y la persona que le responde lo ve


def test_la_pausa_dura_una_hora_desde_el_ultimo_mensaje_del_administrador(
    sesion: Session, admin: Administrador
):
    cliente, queja = ana_se_queja(sesion)
    responder(sesion, queja, admin=admin)  # 8:00
    responder(sesion, queja, "¿Nos cuentas más?", admin=admin, ahora=AHORA + minutos(40))

    assert cliente.pasan(95).escribe("hola").ultimas == []  # 9:35: todavía
    assert cliente.pasan(10).escribe("hola").opciones == MENU  # 9:45: ya pasó


def test_devolver_al_bot(sesion: Session, admin: Administrador):
    cliente, queja = ana_se_queja(sesion)
    responder(sesion, queja, admin=admin)

    pausa.devolver_al_bot(sesion, CELULAR)

    assert cliente.pasan(1).escribe("hola").opciones == MENU


def test_si_borran_el_comentario_el_bot_vuelve_a_contestarle(sesion: Session, admin: Administrador):
    cliente, queja = ana_se_queja(sesion)
    responder(sesion, queja, admin=admin)

    comentarios.borrar(sesion, queja.id)

    assert pausa.pausa_de(sesion, CELULAR, AHORA) is None
    assert cliente.pasan(1).escribe("hola").opciones == MENU


def test_el_reloj_borra_las_pausas_que_ya_pasaron(
    sesion: Session, conexion: Connection, admin: Administrador, caplog
):
    responder(sesion, queja_de_ana(sesion), admin=admin)
    reloj = proceso.Reloj(
        [tareas.SESIONES_DEL_BOT],
        lambda: Session(bind=conexion, join_transaction_mode="create_savepoint"),
    )

    reloj.una_vuelta(AHORA + minutos(59))
    assert len(sesion.scalars(select(PausaDelBot)).all()) == 1
    reloj.una_vuelta(AHORA + minutos(60))

    assert sesion.scalars(select(PausaDelBot).execution_options(populate_existing=True)).all() == []
    assert not any("pausa" in mensaje for mensaje in caplog.messages)


# --- La conversación ---------------------------------------------------------------------------


def test_la_conversacion_junta_lo_que_llego_y_lo_que_salio_en_orden(
    sesion: Session, admin: Administrador
):
    ana(sesion)
    llego(sesion, "hola", cuando=AHORA)
    for texto in ("¡Hola, Ana!", "¿Qué necesitas?"):  # dos respuestas a la misma hora
        bandeja_salida.encolar_respuesta(
            sesion, telefono=CELULAR, mensaje=Mensaje(texto), ahora=AHORA
        )
    contenido = {"opcion": "menu.fila", "titulo": "Ver la fila"}
    llego(sesion, None, cuando=AHORA + minutos(1), tipo="interactive", contenido=contenido)
    llego(sesion, None, cuando=AHORA + minutos(2), tipo="image", contenido={})
    llego(sesion, None, cuando=AHORA + minutos(2), tipo="reaction", contenido={})
    bandeja_salida.encolar_del_administrador(
        sesion, telefono=CELULAR, texto="Hola Ana", por="eduar", ahora=AHORA + minutos(3)
    )
    llego(sesion, "de otro celular", telefono="573109876543")

    lineas = respuestas.charla_con(sesion, CELULAR, leer_parametros(sesion))

    assert [(linea.quien, linea.dice) for linea in lineas] == [
        (Quien.CLIENTE, "hola"),
        (Quien.BOT, "¡Hola, Ana!"),
        (Quien.BOT, "¿Qué necesitas?"),
        (Quien.CLIENTE, "Tocó «Ver la fila»"),
        (
            Quien.CLIENTE,
            "Mandó algo que no es texto (una foto, un audio...): aquí no se puede ver.",
        ),
        (Quien.CLIENTE, "Reaccionó a un mensaje."),
        (Quien.ADMINISTRADOR, "Hola Ana"),
    ]
    assert (lineas[-1].por, lineas[-1].estado) == ("eduar", EstadoMensaje.PENDIENTE)
    assert len(respuestas.charla_con(sesion, CELULAR, leer_parametros(sesion), cuantos=3)) == 3


def test_la_conversacion_muestra_los_avisos_de_la_fila(sesion: Session):
    lavando = ocupar_puestos(sesion, "C1", "C2")[0]
    nuevo = por_whatsapp(sesion, vehiculo=ana(sesion))  # no hay puesto libre: espera
    casos.marcar_listo(sesion, lavando, ahora=AHORA + minutos(30))  # C1 libre: lo llaman

    aviso = respuestas.charla_con(sesion, CELULAR, leer_parametros(sesion))[-1]

    assert aviso.quien == Quien.BOT
    assert aviso.dice.startswith(f"¡Es tu turno! Ya hay un puesto libre para *{nuevo.numero}*.")


# --- El diálogo de responder en el dashboard ---------------------------------------------------


def test_la_tarjeta_ofrece_responder_por_whatsapp_si_dejo_su_celular(
    adentro: TestClient, sesion: Session
):
    queja = queja_de_ana(sesion)
    anonima = comentarios.registrar(sesion, TipoComentario.FELICITACION, "excelente servicio")

    pagina = adentro.get("/comentarios").text

    assert f'hx-get="/comentarios/{queja.id}/responder"' in pagina
    assert f'hx-get="/comentarios/{anonima.id}/responder"' not in pagina
    assert pagina.count("Responder por WhatsApp") == 1


def test_el_dialogo_muestra_la_conversacion_y_propone_como_empezar(
    adentro: TestClient, sesion: Session
):
    queja = queja_de_ana(sesion)
    respuesta_del_bot = bandeja_salida.encolar_respuesta(
        sesion,
        telefono=CELULAR,
        mensaje=Mensaje("Qué pena, Ana, con lo que pasó."),
        ahora=AHORA - minutos(10),
    )
    respuesta_del_bot.estado, respuesta_del_bot.enviado_en = EstadoMensaje.ENVIADO, AHORA

    dialogo = adentro.get(f"/comentarios/{queja.id}/responder", headers=HTMX).text

    assert '<h2 id="dialogo-titulo">Responder a Ana Ruiz</h2>' in dialogo
    assert "Por WhatsApp, al 300 *** 4567." in dialogo
    assert '<blockquote class="comentario__cita">me atendieron mal</blockquote>' in dialogo
    assert '<p class="charla-cliente__dia"><span>Hoy</span></p>' in dialogo
    assert '<div class="linea linea--cliente">' in dialogo
    assert '<span class="linea__quien">Bot</span>' in dialogo
    assert "<p>Qué pena, Ana, con lo que pasó.</p>" in dialogo
    assert "Enviado" not in dialogo  # del bot solo se dice si hubo un problema
    assert ">Hola Ana, te habla Eduar, de Auto Ibaguer. </textarea>" in dialogo
    assert "WhatsApp deja escribirle hasta mañana a las 7:50 a.m." in dialogo
    assert "Al enviarlo, el bot hace una pausa de una hora con este cliente" in dialogo
    assert 'id="pausa-del-bot" class="pausa-del-bot" hidden' in dialogo  # todavía no hay pausa
    # La conversación se vuelve a pedir sola.
    assert f'hx-get="/comentarios/{queja.id}/charla" hx-trigger="every 10s"' in dialogo


def test_responder_desde_el_dialogo(adentro: TestClient, sesion: Session, admin: Administrador):
    queja = queja_de_ana(sesion)

    respuesta = adentro.post(
        f"/comentarios/{queja.id}/responder",
        data={"texto": "Hola Ana, te habla Eduar. ¿Qué pasó?"},
        headers=HTMX,
    )

    assert respuesta.status_code == 200
    dialogo = respuesta.text
    # El diálogo sigue abierto, con el mensaje en la conversación y el campo vacío.
    assert '<div class="linea linea--administrador">' in dialogo
    assert '<span class="linea__quien">eduar</span>' in dialogo
    assert "<p>Hola Ana, te habla Eduar. ¿Qué pasó?</p>" in dialogo
    assert 'linea__estado--pendiente">Por enviar</span>' in dialogo
    assert "required></textarea>" in dialogo
    assert "<strong>Bot en pausa hasta las 9:00 a.m.</strong>" in dialogo
    assert f'hx-post="/comentarios/{queja.id}/devolver-al-bot"' in dialogo
    # La tarjeta que está detrás del diálogo también cambia.
    assert f'aria-labelledby="comentario-{queja.id}-tipo" hx-swap-oob="true"' in dialogo
    pausa_en_la_tarjeta = "Le están respondiendo por WhatsApp: el bot no le contesta hasta"
    assert f"{pausa_en_la_tarjeta} las 9:00 a.m.</span>" in dialogo  # sin un punto de más
    assert "Atendido por eduar" in dialogo
    assert eventos(respuesta)["comentarios-cambiados"] is True
    [mensaje] = del_administrador(sesion)
    assert mensaje.contenido == {"texto": "Hola Ana, te habla Eduar. ¿Qué pasó?", "por": "eduar"}
    assert pausa.pausa_de(sesion, CELULAR, AHORA).administrador_id == admin.id
    # En la pantalla Mensajes se ve quién lo escribió.
    assert "eduar: Hola Ana, te habla Eduar. ¿Qué pasó?" in adentro.get("/mensajes").text


def test_si_no_escribe_nada_el_dialogo_lo_dice(adentro: TestClient, sesion: Session):
    queja = queja_de_ana(sesion)
    guardar(sesion)

    respuesta = adentro.post(
        f"/comentarios/{queja.id}/responder", data={"texto": "   "}, headers=HTMX
    )

    assert respuesta.status_code == 200
    assert '<p class="error" role="alert">Escribe lo que le quieres decir.</p>' in respuesta.text
    assert del_administrador(sesion) == []


def test_si_ya_pasaron_24_horas_se_ofrece_llamar_y_lo_escrito_no_se_pierde(
    adentro: TestClient, sesion: Session, reloj: dict
):
    queja = queja_de_ana(sesion)
    guardar(sesion)
    reloj["ahora"] = AHORA + timedelta(days=1)

    dialogo = adentro.get(f"/comentarios/{queja.id}/responder", headers=HTMX).text
    assert "WhatsApp solo deja escribirle hasta 24 horas después de su último mensaje" in dialogo
    assert 'href="tel:+573001234567">Llamar</a>' in dialogo
    assert 'name="texto"' not in dialogo  # no hay cómo enviar
    assert "Hola Ana, te habla" not in dialogo

    # Si el diálogo estaba abierto desde antes, lo que escribió queda para copiarlo.
    respuesta = adentro.post(
        f"/comentarios/{queja.id}/responder", data={"texto": "Hola Ana"}, headers=HTMX
    )
    assert '<textarea id="respuesta" rows="3" readonly>Hola Ana</textarea>' in respuesta.text
    assert del_administrador(sesion) == []


def test_devolver_al_bot_desde_el_dialogo(
    adentro: TestClient, sesion: Session, admin: Administrador
):
    queja = queja_de_ana(sesion)
    responder(sesion, queja, admin=admin)

    respuesta = adentro.post(f"/comentarios/{queja.id}/devolver-al-bot", headers=HTMX)

    assert respuesta.status_code == 200
    assert respuesta.text.lstrip().startswith('<div id="pausa-del-bot"')
    assert "Listo: el bot vuelve a contestarle." in respuesta.text
    assert "Devolver al bot" not in respuesta.text
    assert 'hx-swap-oob="true"' in respuesta.text  # la tarjeta, sin la pausa
    assert "Le están respondiendo" not in respuesta.text
    assert pausa.pausa_de(sesion, CELULAR, AHORA) is None


def test_la_conversacion_se_pide_sola_y_solo_llega_si_cambio(adentro: TestClient, sesion: Session):
    queja = queja_de_ana(sesion)
    dialogo = adentro.get(f"/comentarios/{queja.id}/responder", headers=HTMX).text
    huella = re.search(r'"huella": "([0-9a-f]+)"', dialogo).group(1)

    igual = adentro.get(f"/comentarios/{queja.id}/charla?huella={huella}", headers=HTMX)
    assert (igual.status_code, igual.text) == (204, "")
    assert "set-cookie" not in igual.headers  # pedirla sola no deja la sesión abierta

    llego(sesion, "¿alguien me va a responder?", cuando=AHORA)
    cambio = adentro.get(f"/comentarios/{queja.id}/charla?huella={huella}", headers=HTMX)

    assert cambio.status_code == 200
    assert cambio.text.lstrip().startswith('<section id="charla-cliente"')
    assert "<p>¿alguien me va a responder?</p>" in cambio.text
    # La pausa y la tarjeta, al día: puede que el comentario haya vuelto a "por atender".
    assert '<div id="pausa-del-bot" class="pausa-del-bot" hidden hx-swap-oob="true">' in cambio.text
    assert f'aria-labelledby="comentario-{queja.id}-tipo" hx-swap-oob="true"' in cambio.text
    assert eventos(cambio)["comentarios-cambiados"] is True


def test_sin_celular_no_hay_dialogo_de_responder(adentro: TestClient, sesion: Session):
    anonima = comentarios.registrar(sesion, TipoComentario.FELICITACION, "excelente servicio")

    respuesta = adentro.get(f"/comentarios/{anonima.id}/responder", headers=HTMX)

    assert respuesta.status_code == 404
    assert respuesta.json()["detail"] == (
        "Este comentario no tiene celular: no hay a quién responderle."
    )


def test_otro_sitio_no_puede_responder(adentro: TestClient, sesion: Session):
    queja = queja_de_ana(sesion)

    respuesta = adentro.post(
        f"/comentarios/{queja.id}/responder",
        data={"texto": "hola"},
        headers={"Origin": "https://otro-sitio.com"},
    )

    assert respuesta.status_code == 403
    assert del_administrador(sesion) == []


def test_el_dialogo_avisa_si_whatsapp_no_esta_conectado(
    adentro: TestClient, sesion: Session, monkeypatch
):
    queja = queja_de_ana(sesion)
    entorno = obtener_entorno()
    datos = {"whatsapp_token": "t", "whatsapp_id_numero": "1", "whatsapp_secreto_app": "s"}

    def dialogo() -> str:
        return adentro.get(f"/comentarios/{queja.id}/responder", headers=HTMX).text

    for campo in datos:
        monkeypatch.setattr(entorno, campo, "")
    assert "WhatsApp todavía no está conectado" in dialogo()
    for campo, valor in datos.items():
        monkeypatch.setattr(entorno, campo, valor)
    assert "WhatsApp todavía no está conectado" not in dialogo()


# --- Probar el bot: todo el camino en tu computador --------------------------------------------


def test_probar_el_bot_anota_lo_que_se_dicen_sin_mandarlo(adentro: TestClient, sesion: Session):
    for datos in ({"texto": "hola"}, {"opcion": "menu.precios", "titulo": "Servicios y precios"}):
        adentro.post("/probar-bot/mensaje", data={"telefono": PRUEBA, **datos}, headers=HTMX)

    entrantes = sesion.scalars(select(MensajeEntrante).where(MensajeEntrante.telefono == PRUEBA))
    entrantes = list(entrantes)
    assert [m.contenido for m in entrantes] == [
        {"texto": "hola"},
        {"opcion": "menu.precios", "titulo": "Servicios y precios"},
    ]
    assert all(m.id_meta.startswith("prueba-") and m.procesado_en == AHORA for m in entrantes)
    salientes = sesion.scalars(select(MensajeSaliente).where(MensajeSaliente.telefono == PRUEBA))
    salientes = list(salientes)
    assert [m.tipo for m in salientes] == ["respuesta", "respuesta"]
    assert all(m.estado == EstadoMensaje.ENVIADO for m in salientes)  # no sale por WhatsApp
    # Cuenta para las 24 horas de WhatsApp, como si hubiera escrito de verdad.
    assert respuestas.hasta_cuando_se_puede(sesion, PRUEBA, AHORA) == AHORA + timedelta(hours=24)


def test_de_probar_el_bot_a_comentarios_y_de_vuelta(adentro: TestClient, sesion: Session):
    ana(sesion)
    pagina = adentro.get(f"/probar-bot?celular={CELULAR}").text
    desde = int(re.search(r'"desde": (\d+)', pagina).group(1))

    def escribir(texto: str) -> str:
        datos = {"telefono": CELULAR, "texto": texto}
        return adentro.post("/probar-bot/mensaje", data=datos, headers=HTMX).text

    # 1. Ana se queja en Probar el bot.
    assert "Qué pena, Ana, con lo que pasó." in escribir("me atendieron mal")
    [queja] = guardados(sesion)

    # 2. En Comentarios se ve la conversación y se le responde.
    dialogo = adentro.get(f"/comentarios/{queja.id}/responder", headers=HTMX).text
    assert "<p>me atendieron mal</p>" in dialogo and "Qué pena, Ana, con lo que pasó." in dialogo
    adentro.post(
        f"/comentarios/{queja.id}/responder",
        data={"texto": "Hola Ana, te habla Eduar. ¿Qué pasó?"},
        headers=HTMX,
    )

    # 3. La respuesta le llega a la charla de Probar el bot.
    avisos = adentro.get(f"/probar-bot/avisos?telefono={CELULAR}&desde={desde}").text
    assert "burbuja__etiqueta--persona" in avisos and "Lo escribió eduar en Comentarios" in avisos
    assert "Hola Ana, te habla Eduar. ¿Qué pasó?" in avisos

    # 4. Lo que Ana contesta ya no lo contesta el bot, y vuelve a Comentarios.
    respuesta = escribir("El señor de la caja fue grosero")
    assert (
        '<div class="burbuja burbuja--yo"><p>El señor de la caja fue grosero</p></div>' in respuesta
    )
    assert "burbuja--bot" not in respuesta
    assert guardados(sesion)[0].atendido_en is None
    conversacion = adentro.get(f"/comentarios/{queja.id}/charla", headers=HTMX).text
    assert "<p>El señor de la caja fue grosero</p>" in conversacion


# --- Ley 1581 ----------------------------------------------------------------------------------


def test_al_borrar_los_datos_del_cliente_se_borra_su_pausa(sesion: Session, admin: Administrador):
    queja = queja_de_ana(sesion)
    responder(sesion, queja, admin=admin)

    clientes.borrar_datos(sesion, queja.cliente_id)

    assert sesion.scalars(select(PausaDelBot)).all() == []
    assert sesion.scalars(select(Comentario)).all() == []
