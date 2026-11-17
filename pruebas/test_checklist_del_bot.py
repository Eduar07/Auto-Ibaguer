"""El checklist "¿Tu bot de WhatsApp vende o solo contesta?" (SPEKGEN), adaptado al lavadero:
las seis pruebas del documento, como las haría un cliente desde otro celular.

| Pieza | La prueba | Pasa si |
|---|---|---|
| 1. El modelo | "¿lavan motos?", tal cual | dice qué hay para motos, no "marca uno" |
| 2. La base | el precio de algo que no vende | dice que no lo tiene; nunca inventa un precio |
| 3. La visión | la foto de su carro sucio, sin decir nada | la entiende y le da los precios |
| 4. El seguimiento | pide precios, dice "lo pienso" | al otro día le escribe, dentro de las 24 h |
| 5. Pase a humano | 20 camionetas a precio especial | le avisa al dueño con un resumen y se calla |
| 6. El CRM | una semana después, buscar al cliente | sale con lo que pidió y en qué quedó |

Gemini aquí es de mentira (dice lo que entendería el de verdad): lo que se prueba es lo
que hace el bot con eso. Cómo entiende el Gemini de verdad se prueba con
`uv run auto-ibaguer probar-gemini` (app.bot.frases_de_prueba).

Y además, que suene a una persona: sin frases de robot, saludando por el nombre a quien ya
conoce y sin decir nunca que no puede ver fotos.
"""

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import conversacion, seguimiento
from app.bot.en_que_quedo import en_que_quedo
from app.bot.interprete import Intencion, Interpretacion
from app.bot.mensajes import Adjunto, Entrada, Mensaje
from app.clientes.tablas import Cliente
from app.comentarios import respuestas
from app.comentarios.tablas import Comentario, TipoComentario
from app.integraciones.tablas import MensajeEntrante, MensajeSaliente
from pruebas.fabricas import LUNES_8AM, cliente_con_vehiculo
from pruebas.test_bot import CELULAR, Chat
from pruebas.test_gemini import GeminiDeMentira, chat

Q = Intencion
FOTO_DEL_CARRO = Adjunto("image/jpeg", b"\xff\xd8camioneta-con-barro")

# Lo que hace sentir a un cliente que habla con un contestador.
DE_ROBOT = re.compile(
    r"marca (uno|dos|\d)|opci[oó]n inv[aá]lida|no entiendo|solo entiendo|"
    r"no puedo ver (fotos|im[aá]genes)|escribe el nombre|comando",
    re.IGNORECASE,
)


def suena_a_persona(texto: str) -> bool:
    return DE_ROBOT.search(texto) is None


def guardar_lo_que_llego(sesion: Session, cliente: Chat, cuando) -> None:
    """Lo que deja la bandeja de entrada por WhatsApp (aquí el chat llama al bot directo)."""
    sesion.add(
        MensajeEntrante(
            id_meta=f"wamid.{cuando.isoformat()}",
            telefono=cliente.telefono,
            tipo="text",
            contenido={"texto": "lo pienso"},
            recibido_en=cuando,
            procesado_en=cuando,
        )
    )
    sesion.flush()


# --- 1. El modelo: entiende sin menús ---------------------------------------------------------


def test_pieza_1_lavan_motos_contesta_con_lo_que_hay(sesion: Session):
    cliente = chat(sesion, Interpretacion(Q.PRECIOS, vehiculo="Moto")).escribe("¿lavan motos?")

    assert cliente.texto.startswith("Servicios para moto:")
    assert "$" in cliente.texto  # con los precios del sistema
    assert suena_a_persona(cliente.texto)


def test_pieza_1_aunque_gemini_no_responda(sesion: Session):
    """Si Gemini se cae (la cuota, la red), lo más común lo entiende solo."""
    cliente = chat(sesion, None).escribe("¿lavan motos?")

    assert cliente.texto.startswith("Servicios para moto:")
    assert (
        Chat(sesion, telefono="573001112233")
        .escribe("cuanto vale lavar la camioneta")
        .texto.startswith("Servicios para camioneta:")
    )


# --- 2. La base de conocimiento: no inventa --------------------------------------------------


def test_pieza_2_lo_que_no_vende_no_tiene_precio(sesion: Session):
    se_lo_invento = Interpretacion(
        Q.PRECIOS, servicio_que_no_hay=True, respuesta="El polichado te sale en $90.000 🙌"
    )

    cliente = chat(sesion, se_lo_invento).escribe("cuánto vale el polichado?")

    assert "90.000" not in cliente.texto  # el filtro no lo deja salir
    assert cliente.texto.startswith("Ese servicio no lo tenemos en el menú. Lo que sí hacemos:")
    assert cliente.opciones == ["Servicios y precios", "Pedir turno"]


# --- 3. La visión: lee la foto -----------------------------------------------------------------


def test_pieza_3_la_foto_de_su_carro_sucio_le_trae_los_precios(sesion: Session):
    cliente = chat(
        sesion,
        Interpretacion(
            Q.PRECIOS, vehiculo="Camioneta", lo_que_dice="Una camioneta blanca con barro"
        ),
    )

    cliente._enviar(Entrada(CELULAR, otro=True, adjunto=FOTO_DEL_CARRO))

    assert cliente.texto.startswith("Servicios para camioneta:")
    assert suena_a_persona(cliente.texto)
    assert cliente.gemini.contextos[0].adjunto == FOTO_DEL_CARRO  # la vio


# --- 4. El seguimiento: vuelve al día siguiente ------------------------------------------------


def test_pieza_4_lo_pienso_y_al_otro_dia_le_escribe_dentro_de_las_24_horas(sesion: Session):
    cliente = Chat(sesion).escribe("hola").toca("Servicios y precios").toca("Automóvil")
    cliente.escribe("lo pienso").toca("Sí, escríbeme")
    guardar_lo_que_llego(sesion, cliente, LUNES_8AM)

    al_otro_dia = LUNES_8AM + timedelta(hours=23)
    assert seguimiento.enviar_los_que_tocan(sesion, ahora=al_otro_dia) == 1

    [mensaje] = sesion.scalars(select(MensajeSaliente)).all()
    texto = Mensaje.desde_datos(mensaje.contenido).texto
    assert texto.startswith("¡Hola! Te escribo como quedamos.")
    assert mensaje.creado_en - LUNES_8AM < timedelta(hours=24)  # nunca un 131047


# --- 5. Pase a humano: suelta lo que no le toca ------------------------------------------------


def test_pieza_5_un_pedido_grande_le_llega_al_dueno_y_el_bot_se_calla(sesion: Session, admin):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    cliente = chat(
        sesion,
        Interpretacion(
            Q.PEDIDO_ESPECIAL, resumen="Pide precio para lavar 200 carros de su empresa al mes"
        ),
    )

    cliente.escribe("tengo una empresa, son 200 carros al mes, en cuánto me los dejan?")

    assert "$" not in cliente.texto  # no negocia
    assert "Eso lo cuadra directamente el administrador" in cliente.texto
    pedido = sesion.scalars(select(Comentario)).one()
    assert pedido.tipo == TipoComentario.PEDIDO_ESPECIAL
    assert pedido.resumen == "Pide precio para lavar 200 carros de su empresa al mes"

    # El dueño le contesta desde Comentarios: el bot ya no se mete.
    guardar_lo_que_llego(sesion, cliente, LUNES_8AM)
    respuestas.responder(
        sesion,
        pedido.id,
        "Hola Ana, te habla Eduar.",
        administrador_id=admin.id,
        usuario="eduar",
        ahora=LUNES_8AM,
    )
    cliente.gemini.entendidos.append(Interpretacion(Q.PRECIOS))
    cliente.pasan(2).escribe("y cuánto sería más o menos?")
    assert cliente.ultimas == []  # le responde la persona, no el bot


# --- 6. El CRM: lo guarda todo ----------------------------------------------------------------


def test_pieza_6_una_semana_despues_sale_con_lo_que_pidio_y_en_que_quedo(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")
    cliente = Chat(sesion).escribe("hola").toca("Servicios y precios").toca("Moto")
    cliente.escribe("lo pienso").toca("Sí, escríbeme")
    guardar_lo_que_llego(sesion, cliente, LUNES_8AM)
    ana = sesion.scalars(select(Cliente).where(Cliente.telefono == CELULAR)).one()

    quedo = en_que_quedo(sesion, ana.id, ahora=LUNES_8AM + timedelta(minutes=5))

    assert quedo.ultimo_mensaje == "lo pienso"
    assert any(
        cosa.startswith("Dijo «lo pienso»: el bot le escribe mañana") for cosa in quedo.cosas
    )
    assert any("Preguntó los precios de moto" in cosa for cosa in quedo.cosas)

    una_semana_despues = LUNES_8AM + timedelta(days=7)
    seguimiento.cancelar(sesion, CELULAR)
    quedo = en_que_quedo(sesion, ana.id, ahora=una_semana_despues)
    assert quedo.cosas == [
        "Preguntó los precios de moto el 26 de octubre a las 8:00 a.m. y no ha pedido turno."
    ]


# --- Que suene a una persona -------------------------------------------------------------------


def test_a_quien_ya_conoce_lo_saluda_por_su_nombre(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=CELULAR, nombre="Ana Ruiz")

    assert Chat(sesion).escribe("buenas").texto.startswith("¡Hola, Ana!")


def test_con_gemini_el_saludo_no_es_siempre_el_mismo(sesion: Session):
    cliente = chat(
        sesion,
        Interpretacion(Q.SALUDO, respuesta="¡Buenas, buenas! ¿Cómo va todo?"),
        Interpretacion(Q.SALUDO, respuesta="¡Hola de nuevo! Dime en qué te ayudo."),
    )

    primero = cliente.escribe("hola").texto
    segundo = cliente.pasan(40).escribe("holaa").texto

    assert primero != segundo


def test_ninguna_respuesta_fija_suena_a_robot():
    from app.bot.textos import TEXTOS

    de_robot = {clave: texto for clave, texto in TEXTOS.items() if not suena_a_persona(texto)}
    assert de_robot == {}


def test_nunca_dice_que_es_una_persona(sesion: Session):
    miente = Interpretacion(Q.CHARLA, respuesta="Tranquilo, soy una persona de verdad.")

    cliente = chat(sesion, None, miente)
    cliente.escribe("hola").pasan(1).escribe("eres un robot?")

    assert "soy una persona" not in cliente.texto


def test_el_recorrido_completo_no_tiene_frases_de_robot(sesion: Session):
    """Un cliente nuevo, de punta a punta, sin Gemini: ningún mensaje suena a contestador."""
    cliente = Chat(sesion)
    textos = [
        cliente.escribe("hola").texto,
        cliente.escribe("¿lavan motos?").texto,
        cliente.toca("Pedir turno").texto,
        cliente.toca("Acepto").texto,
        cliente.escribe("Ana Ruiz").texto,
        cliente.manda_un_audio().texto,
        cliente.escribe("lo pienso").texto,
    ]

    assert [texto for texto in textos if not suena_a_persona(texto)] == []


def test_la_conversacion_va_en_la_memoria_del_bot(sesion: Session):
    """Lo que dice un audio queda escrito: así el bot lo recuerda en el mensaje siguiente."""
    de_mentira = GeminiDeMentira(
        Interpretacion(Q.PRECIOS, vehiculo="Moto", lo_que_dice="cuánto vale la moto"),
    )
    respuestas_del_bot, traza = conversacion.atender_con_traza(
        sesion,
        Entrada(CELULAR, otro=True, adjunto=Adjunto("audio/ogg", b"OggS")),
        ahora=LUNES_8AM,
        interprete=de_mentira,
    )

    assert traza.lo_que_dice == "cuánto vale la moto"
    assert respuestas_del_bot[0].texto.startswith("Servicios para moto:")
