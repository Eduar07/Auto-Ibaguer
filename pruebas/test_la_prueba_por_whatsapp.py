"""Lo que falló en la prueba por WhatsApp del 20 de noviembre de 2026, y cómo quedó.

En esa prueba, el cliente tenía su moto registrada y:
- escribió "Ya" y "Quiero venir ya en 30 hora" en la pregunta de cuándo viene, y el bot le
  contestó dos veces "Toca una de las opciones, por favor";
- dijo que quería un servicio de carro y el bot le siguió mostrando los de moto;
- pidió el video del encerado y el bot (Gemini) le contestó que no tenía videos;
- la confirmación del turno repetía cuánto tiempo tenía para llegar.

Ahora el bot entiende "ya", "de una", "en 20 minutos" o "en una hora" (solo, o con lo que
entendió Gemini), cambia de vehículo si nombra otro, manda el video cuando se lo piden y
confirma en corto. Cuando de verdad no entiende, dice cómo contestar, con un ejemplo.
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.bot.conversacion import videos_de_la_ficha
from app.bot.interprete import CARRO, SI, Intencion, Interpretacion
from app.bot.textos import TEXTOS
from pruebas.fabricas import cliente_con_vehiculo, servicio
from pruebas.test_bot import CELULAR, MENU, Chat, paso
from pruebas.test_gemini import chat
from pruebas.test_lo_que_ya_dijo import pregunta_la_placa
from pruebas.test_reservas_por_whatsapp import hasta_cuando
from pruebas.test_videos_de_los_servicios import poner_video, ya_le_salio_el_video

Q = Intencion
PIDE_TURNO = Q.PEDIR_TURNO
LO_ANTES_POSIBLE = "*Cuándo:* lo antes posible (fila de carros: hay puesto libre, puedes venir ya)"
PREGUNTA_SERVICIO_MOTO = "¿Qué servicio quieres para tu moto?"


def con_su_moto(sesion: Session) -> None:
    """Como en la prueba: el cliente ya tiene su moto registrada."""
    cliente_con_vehiculo(sesion, tipo="Moto", telefono=CELULAR, nombre="Eduar Díaz", placa="RPP74F")


# --- 1. Cuándo viene, con sus palabras ---------------------------------------------------------


def test_ya_en_la_pregunta_de_cuando_es_lo_antes_posible(sesion: Session):
    chat = hasta_cuando(sesion).escribe("Ya")

    assert chat.texto.startswith("Revisa tu turno:")
    assert LO_ANTES_POSIBLE in chat.texto
    assert paso(sesion) == "confirmar"


def test_quiero_venir_ya_en_30_hora_es_lo_antes_posible(sesion: Session):
    chat = hasta_cuando(sesion).escribe("Quiero venir ya en 30 hora")

    assert LO_ANTES_POSIBLE in chat.texto


def test_ya_voy_en_la_pregunta_de_cuando_no_se_pierde_el_turno(sesion: Session):
    """Antes lo tomaba como "voy en camino" a un turno que no existía y lo mandaba al menú."""
    chat = hasta_cuando(sesion).escribe("ya voy")

    assert LO_ANTES_POSIBLE in chat.texto
    assert paso(sesion) == "confirmar"


def test_en_hora_y_media_reserva_esa_hora(sesion: Session):
    chat = hasta_cuando(sesion).escribe("en hora y media")  # son las 8:00 a.m.

    assert "*Cuándo:* hoy a las 9:30 a.m." in chat.texto


def test_en_una_hora_sin_horario_exacto_muestra_las_mas_cercanas(sesion: Session):
    chat = hasta_cuando(sesion).escribe("en una hora")  # las 9:00; hay a las 8:30 y 9:30

    assert chat.texto.startswith(TEXTOS["hora_no_exacta"])
    assert paso(sesion) == "hora"


def test_si_el_bot_no_lo_entiende_usa_lo_que_entendio_gemini(sesion: Session):
    gemini = Interpretacion(PIDE_TURNO, cuando="hoy a las 4 pm")
    cliente = chat(sesion, gemini)
    cliente.toca_el_id("menu.pedir").toca("Acepto").escribe("Laura Gómez")
    cliente.toca("Automóvil").toca("Ahora no").toca("Básico")

    cliente.escribe("apenas salga del trabajo, porfa")

    assert "*Cuándo:* hoy a las 4:30 p.m." not in cliente.texto  # a las 4 no hay: las cercanas
    assert cliente.texto.startswith(TEXTOS["hora_no_exacta"])
    assert "Gemini entendió cuándo viene: «hoy a las 4 pm»." in cliente.traza.notas


def test_si_nadie_entiende_cuando_dice_como_escribirlo(sesion: Session):
    cliente = chat(sesion, Interpretacion(PIDE_TURNO))
    cliente.toca_el_id("menu.pedir").toca("Acepto").escribe("Laura Gómez")
    cliente.toca("Automóvil").toca("Ahora no").toca("Básico")

    cliente.escribe("cuando pueda salir del trabajo")

    assert cliente.texto.startswith(TEXTOS["ayuda_cuando"] + "\n\n¿Cuándo quieres venir?")
    assert "Toca una de las opciones" not in cliente.texto
    assert paso(sesion) == "cuando"


def test_sin_gemini_tampoco_dice_toca_una_opcion(sesion: Session):
    chat = hasta_cuando(sesion).escribe("cuando pueda salir del trabajo")

    assert chat.texto.startswith(TEXTOS["ayuda_cuando"])


# --- 2. Otro vehículo en medio del turno -------------------------------------------------------


def test_pedir_servicio_de_carro_con_la_moto_registrada_pregunta_cual_carro(sesion: Session):
    con_su_moto(sesion)
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo=CARRO))
    cliente.toca_el_id("menu.pedir")
    assert cliente.texto.startswith(PREGUNTA_SERVICIO_MOTO)  # el bot escogió su moto

    cliente.escribe("Quiero un servicio de carro")

    assert cliente.texto == TEXTOS["pedir_cual_carro"]
    assert cliente.opciones == ["Automóvil", "Camioneta"]
    cliente.toca("Automóvil")
    assert cliente.texto.startswith(pregunta_la_placa("automóvil"))
    cliente.toca("Ahora no")
    assert cliente.texto.startswith("¿Qué servicio quieres para tu automóvil?")


def test_sin_gemini_tambien_cambia_de_vehiculo(sesion: Session):
    con_su_moto(sesion)
    cliente = Chat(sesion).toca_el_id("menu.pedir")

    cliente.escribe("es para el carro")

    assert cliente.texto == TEXTOS["pedir_cual_carro"]


def test_si_tiene_ese_vehiculo_registrado_lo_usa(sesion: Session):
    carro = cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Eduar Díaz", placa="ABC123")
    cliente_con_vehiculo(sesion, tipo="Moto", telefono=CELULAR, placa="RPP74F")
    del carro
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="Moto"))
    cliente.toca_el_id("menu.pedir").toca("Automóvil ABC123")

    cliente.escribe("mejor la moto")

    assert cliente.texto.startswith(PREGUNTA_SERVICIO_MOTO)


def test_al_cambiar_de_vehiculo_se_conserva_el_servicio(sesion: Session):
    con_su_moto(sesion)
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo=CARRO))
    cliente.toca_el_id("menu.pedir").toca("Básico")
    assert cliente.texto.startswith("¿Cuándo quieres venir?")

    cliente.escribe("ah no, era para el carro").toca("Automóvil").toca("Ahora no")

    # El Básico de la moto pasa a ser el Básico del carro: de una, cuándo viene.
    assert cliente.texto.startswith("¿Cuándo quieres venir?")
    cliente.toca("Lo antes posible")
    assert "*Vehículo:* Automóvil, placa pendiente" in cliente.texto
    assert "*Servicio:* Básico · $40.000" in cliente.texto


def test_nombrar_el_mismo_vehiculo_no_cambia_nada(sesion: Session):
    con_su_moto(sesion)
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, vehiculo="Moto"))
    cliente.toca_el_id("menu.pedir")

    cliente.escribe("para la moto porfa")

    assert TEXTOS["ayuda_servicio"].format(ejemplo="Básico") in cliente.texto
    assert paso(sesion) == "servicio"


# --- 3. El video, cuando lo pide ---------------------------------------------------------------


def hasta_el_resumen_con_la_moto(sesion: Session, *entendidos: Interpretacion) -> Chat:
    con_su_moto(sesion)
    cliente = chat(sesion, *entendidos)
    return cliente.toca_el_id("menu.pedir").toca("Intermedio").toca("Lo antes posible")


def test_pedir_el_video_en_el_resumen_lo_manda_y_sigue_el_resumen(sesion: Session):
    servicio_id = poner_video(sesion, "Intermedio", "Motos")
    ya_le_salio_el_video(sesion, servicio_id, hace=timedelta(minutes=5))  # se lo pide igual
    gemini = Interpretacion(Q.PREGUNTA, servicio="Intermedio", pide_video=True)
    cliente = hasta_el_resumen_con_la_moto(sesion, gemini)

    cliente.escribe("Me puedes pasar el video del intermedio?")

    video, resumen = cliente.ultimas
    assert video.video == servicio_id
    assert video.texto == "🎬 Así es el lavado *Intermedio* para tu moto, paso a paso."
    assert resumen.texto.startswith("Revisa tu turno:")
    assert paso(sesion) == "confirmar"


def test_la_palabra_video_basta_aunque_gemini_no_lo_marque(sesion: Session):
    """En la prueba, Gemini contestó que no tenía videos: eso ya no sale."""
    servicio_id = poner_video(sesion, "Intermedio", "Motos")
    gemini = Interpretacion(
        Q.PREGUNTA,
        servicio="Intermedio",
        respuesta="Por aquí no tengo videos disponibles para mostrarte.",
        sabe=False,
    )
    cliente = hasta_el_resumen_con_la_moto(sesion, gemini)

    cliente.escribe("Me puedes pasar el video del intermedio?")

    assert cliente.ultimas[0].video == servicio_id
    assert "no tengo videos" not in cliente.texto


def test_sin_decir_cual_manda_el_del_servicio_que_eligio(sesion: Session):
    servicio_id = poner_video(sesion, "Intermedio", "Motos")
    cliente = hasta_el_resumen_con_la_moto(sesion, Interpretacion(Q.PREGUNTA, pide_video=True))

    cliente.escribe("y tienes video?")

    assert cliente.ultimas[0].video == servicio_id


def test_quiero_el_servicio_y_muestrame_el_video_elige_y_lo_manda(sesion: Session):
    servicio_id = poner_video(sesion, "Intermedio", "Motos")
    ya_le_salio_el_video(sesion, servicio_id, hace=timedelta(days=3))
    con_su_moto(sesion)
    gemini = Interpretacion(PIDE_TURNO, vehiculo="Moto", servicio="Intermedio", pide_video=True)
    cliente = chat(sesion, gemini).toca_el_id("menu.pedir")

    cliente.escribe("Quiero un servicio intermedio y muéstrame un video del servicio")

    video, cuando = cliente.ultimas
    assert video.video == servicio_id  # aunque lo vio hace 3 días: lo pidió
    assert cuando.texto.startswith("¿Cuándo quieres venir?")


def test_en_el_menu_sin_saber_cual_pregunta_de_cual_servicio(sesion: Session):
    basico = poner_video(sesion)
    moto = poner_video(sesion, "Básico", "Motos")
    cliente = Chat(sesion)

    cliente.escribe("tienen videos?")

    assert cliente.texto == TEXTOS["pedir_cual_video"]
    [lista] = [m.lista for m in cliente.ultimas]
    assert [(o.titulo, o.descripcion) for o in lista.opciones] == [
        ("Básico", "Para carro"),
        ("Básico", "Para moto"),
    ]
    cliente.toca_el_id(f"video.{moto}")
    video, menu = cliente.ultimas
    assert (video.video, video.texto) == (
        moto,
        TEXTOS["video_del_servicio"].format(servicio="Básico", vehiculo="moto"),
    )
    assert menu.texto == TEXTOS["menu"] and cliente.opciones == MENU
    assert basico != moto


def test_en_el_menu_le_manda_los_videos_de_su_vehiculo(sesion: Session):
    con_su_moto(sesion)
    basico = poner_video(sesion, "Básico", "Motos")
    premium = poner_video(sesion, "Premium", "Motos")
    poner_video(sesion, "Básico", "Carros")  # no es de su vehículo

    cliente = Chat(sesion).escribe("me mandas un video?")

    assert [m.video for m in cliente.ultimas] == [basico, premium, None]
    assert cliente.ultimas[-1].texto == TEXTOS["menu"]


def test_un_servicio_sin_video_dice_lo_que_incluye(sesion: Session):
    cliente = hasta_el_resumen_con_la_moto(
        sesion, Interpretacion(Q.PREGUNTA, servicio="Intermedio", pide_video=True)
    )

    cliente.escribe("hay video del intermedio?")

    descripcion = servicio(sesion, "Intermedio", "Motos").descripcion
    assert cliente.texto.startswith(
        f"Del *Intermedio* todavía no tenemos video. Incluye: {descripcion}."
    )
    assert [m.video for m in cliente.ultimas] == [None]


def test_si_todavia_no_sabe_el_vehiculo_el_video_sale_al_elegir_el_servicio(sesion: Session):
    servicio_id = poner_video(sesion)
    cliente = Chat(sesion).toca_el_id("menu.pedir").toca("Acepto")

    cliente.escribe("muéstrame el video primero")

    assert cliente.texto.startswith(TEXTOS["video_cuando_elija"])
    ya_le_salio_el_video(sesion, servicio_id, hace=timedelta(days=1))
    cliente.escribe("Laura Gómez").toca("Automóvil").toca("Ahora no").toca("Básico")
    assert cliente.ultimas[0].video == servicio_id  # aunque ya lo vio: lo pidió


def test_no_me_mandes_videos_no_es_pedirlo():
    from app.bot.conversacion import habla_de_un_video

    assert habla_de_un_video("me pasas el video del encerado")
    assert habla_de_un_video("tienen videitos")
    assert not habla_de_un_video("no me mandes videos")
    assert not habla_de_un_video("hacen videollamada")


def test_gemini_sabe_de_cuales_servicios_hay_video(sesion: Session):
    poner_video(sesion, "Intermedio", "Motos")
    poner_video(sesion, "Básico", "Motos")
    poner_video(sesion, "Encerado", "Carros")

    assert videos_de_la_ficha(sesion) == ("Carros: Encerado", "Motos: Básico y Intermedio")
    cliente = chat(sesion, Interpretacion(Q.SALUDO))
    cliente.escribe("hola")
    [contexto] = cliente.gemini.contextos
    assert "Videos: el bot manda por aquí el video" in contexto.ficha
    assert "Motos: Básico y Intermedio." in contexto.ficha


# --- 4. La confirmación, corta -----------------------------------------------------------------


def test_la_confirmacion_dice_el_turno_y_cuando_llegar_sin_repetir(sesion: Session):
    chat = hasta_cuando(sesion).toca("Lo antes posible").toca("Confirmar turno")

    assert chat.texto == (
        "¡Listo, Laura! Tu turno es *C-01*.\n\n"
        "Hay un puesto libre: ven ya. Tienes hasta las 8:15 a.m. para llegar."
    )
    assert chat.opciones == ["En camino", "Ya estoy aquí"]


def test_en_la_fila_cambiar_la_hora_o_cancelar_van_en_botones(sesion: Session):
    for telefono in ("573001110001", "573001110002", "573001110003"):
        Chat(sesion, telefono=telefono).toca_el_id("menu.pedir").toca("Acepto").escribe(
            "Ana Ruiz"
        ).toca("Automóvil").toca("Ahora no").toca("Básico").toca("Lo antes posible").toca(
            "Confirmar turno"
        )

    chat = hasta_cuando(sesion).toca("Lo antes posible").toca("Confirmar turno")

    assert "Escribe *mi turno*" not in chat.texto
    assert chat.opciones == ["Cambiar hora", "Cancelar"]


# --- 5. Preguntar el precio en medio del turno -------------------------------------------------


def test_preguntar_cuanto_vale_en_medio_del_turno_da_el_precio(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PRECIOS))
    cliente.toca_el_id("menu.pedir").toca("Acepto").escribe("Laura Gómez")
    cliente.toca("Automóvil").toca("Ahora no").toca("Básico")

    cliente.escribe("y eso cuánto me sale?")

    assert cliente.texto.startswith(
        "El *Básico* para automóvil vale $40.000. El pago es en el local.\n\n¿Cuándo quieres venir?"
    )
    assert paso(sesion) == "cuando"


def test_pedir_otro_servicio_despues_de_elegir_lo_cambia(sesion: Session):
    cliente = chat(sesion, Interpretacion(PIDE_TURNO, servicio="Encerado"))
    cliente.toca_el_id("menu.pedir").toca("Acepto").escribe("Laura Gómez")
    cliente.toca("Automóvil").toca("Ahora no").toca("Básico")

    cliente.escribe("mejor el encerado")

    assert cliente.texto.startswith("¿Cuándo quieres venir?")
    cliente.toca("Lo antes posible")
    assert "*Servicio:* Encerado · $60.000" in cliente.texto


def test_un_si_con_el_video_confirma_y_manda_el_video(sesion: Session):
    servicio_id = poner_video(sesion, "Intermedio", "Motos")
    gemini = Interpretacion(PIDE_TURNO, si_o_no=SI, pide_video=True)
    cliente = hasta_el_resumen_con_la_moto(sesion, gemini)

    cliente.escribe("sí, confírmalo y pásame el video")

    assert cliente.ultimas[0].video == servicio_id
    assert cliente.ultimas[1].texto.startswith("¡Listo, Eduar! Tu turno es *M-01*.")


def test_ya_y_pasame_el_video_hace_las_dos_cosas(sesion: Session):
    servicio_id = poner_video(sesion)
    gemini = Interpretacion(PIDE_TURNO, cuando="ya", pide_video=True)
    cliente = chat(sesion, gemini)
    cliente.toca_el_id("menu.pedir").toca("Acepto").escribe("Laura Gómez")
    cliente.toca("Automóvil").toca("Ahora no").toca("Básico")

    cliente.escribe("ya y pásame el video otra vez")

    video, resumen = cliente.ultimas
    assert video.video == servicio_id
    assert LO_ANTES_POSIBLE in resumen.texto


def test_sin_gemini_ya_y_el_video_tambien(sesion: Session):
    servicio_id = poner_video(sesion)
    chat_sin_gemini = hasta_cuando(sesion)

    chat_sin_gemini.escribe("ya, mándame el video")

    assert chat_sin_gemini.ultimas[0].video == servicio_id
    assert LO_ANTES_POSIBLE in chat_sin_gemini.texto


def test_en_la_lista_de_videos_puede_escribir_de_cual(sesion: Session):
    poner_video(sesion)
    moto = poner_video(sesion, "Básico", "Motos")
    cliente = chat(sesion, None, Interpretacion(Q.OTRO, vehiculo="Moto"))
    cliente.escribe("tienen videos?")  # Gemini no respondió: por la palabra "video"
    assert paso(sesion) == "video"

    cliente.escribe("el de la moto")

    assert [m.video for m in cliente.ultimas] == [moto, None]
    assert paso(sesion) == "menu"
