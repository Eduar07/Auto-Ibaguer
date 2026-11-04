"""Frases de prueba para Gemini: cómo escribe la gente en un lavadero de Bucaramanga, y lo
que el bot debe entender de cada una.

Se corren contra el modelo de verdad con `uv run auto-ibaguer probar-gemini`, cada vez que
se cambie el modelo en Configuración (o cuando Google lo actualice). Si una frase falla,
se mira por qué antes de usar ese modelo con clientes. Cada frase gasta una consulta.

Para agregar una, basta escribirla aquí con lo que se espera. Las que mandan una placa o
un celular prueban, además, que esos datos lleguen tapados. Las que traen `antes` prueban
la memoria: solo se entienden por lo que se venían diciendo ("sí porfa", "y la
camioneta?"). Las que traen `en_que_va` se escriben en medio de una pregunta del bot
(pidiendo turno, por ejemplo). `tambien`: otras intenciones que también están bien.

Las que traen `prueba` ("A", "B"...) son las pruebas obligatorias del documento del hito
7c: probar-gemini las marca con esa letra.
"""

from dataclasses import dataclass

from app.bot.conversacion import QUE_ESPERA, Paso
from app.bot.interprete import CARRO, NO, OTRO_VEHICULO, SI, Anterior, Intencion

Q = Intencion  # para que cada frase quepa en una línea
MENU = ("Pedir turno", "Ver la fila", "Servicios y precios")


def cliente(texto: str) -> Anterior:
    return Anterior("cliente", texto)


def bot(texto: str, *opciones: str) -> Anterior:
    return Anterior("bot", texto, opciones)


def esperando(paso: Paso, *opciones: str) -> str:
    """Lo que el bot le cuenta a Gemini que está esperando (como en la conversación)."""
    texto = QUE_ESPERA[paso]
    return f"{texto} Opciones: {' | '.join(opciones)}." if opciones else texto


@dataclass(frozen=True)
class FraseDePrueba:
    texto: str
    intencion: Intencion
    vehiculo: str | None = None  # si se espera que diga de qué vehículo habla
    servicio: str | None = None
    si_o_no: str | None = None  # si se espera que conteste la pregunta del bot
    antes: tuple[Anterior, ...] = ()  # lo que se venían diciendo, del más viejo al más nuevo
    en_que_va: str = ""  # lo que el bot está esperando
    tambien: tuple[Intencion, ...] = ()  # otras intenciones que también están bien
    prueba: str = ""  # la prueba del documento del hito 7c (A, B...), si es una


VEHICULOS = ("Automóvil", "Camioneta", "Moto")
SERVICIO = ("Básico", "General", "Encerado", "Lo decido en el local")
CONFIRMAR = ("Confirmar turno", "Cambiar datos", "Mejor no")
PERMISO = ("Sí, que me responda", "No, gracias")

FRASES: tuple[FraseDePrueba, ...] = (
    # --- Pedir turno ---
    FraseDePrueba("quiero lavar la moto", Q.PEDIR_TURNO, vehiculo="Moto"),
    FraseDePrueba("me puede dar un turno para el carro?", Q.PEDIR_TURNO, vehiculo=CARRO),
    FraseDePrueba("necesito lavar la camioneta hoy", Q.PEDIR_TURNO, vehiculo="Camioneta"),
    FraseDePrueba("hay cupo para ahorita?", Q.PEDIR_TURNO, tambien=(Q.VER_FILA,)),
    FraseDePrueba("me separa un turno porfa", Q.PEDIR_TURNO),
    FraseDePrueba("voy a llevar el auto a lavar, me anota", Q.PEDIR_TURNO),
    FraseDePrueba(
        "quiero un general para la camioneta", Q.PEDIR_TURNO, "Camioneta", servicio="General"
    ),
    FraseDePrueba("pa lavar la nave, se puede ya?", Q.PEDIR_TURNO),
    # --- Ver la fila ---
    FraseDePrueba("hay mucha gente?", Q.VER_FILA),
    FraseDePrueba("cuánto me toca esperar si voy ya?", Q.VER_FILA),
    FraseDePrueba("está muy lleno el lavadero?", Q.VER_FILA),
    FraseDePrueba("cuántos carros tienen en espera", Q.VER_FILA),
    FraseDePrueba("se están demorando mucho hoy?", Q.VER_FILA),
    # --- Precios ---
    FraseDePrueba("cuánto vale lavar la camionetica", Q.PRECIOS, vehiculo="Camioneta"),
    FraseDePrueba("precio del lavado de moto", Q.PRECIOS, vehiculo="Moto"),
    FraseDePrueba("cuánto cobran por lavar un carro", Q.PRECIOS, vehiculo=CARRO),
    FraseDePrueba("qué servicios tienen?", Q.PRECIOS),
    FraseDePrueba("cuánto vale el encerado", Q.PRECIOS, servicio="Encerado"),
    FraseDePrueba("qué incluye el empelotado?", Q.PRECIOS, servicio="Empelotado"),
    FraseDePrueba("a cómo el lavado de la moto con desengrase", Q.PRECIOS, vehiculo="Moto"),
    # --- Mi turno ---
    FraseDePrueba("ya casi me toca?", Q.MI_TURNO),
    FraseDePrueba("en qué va lo mío", Q.MI_TURNO),
    FraseDePrueba("cuánto falta para que me llamen", Q.MI_TURNO),
    FraseDePrueba("ya está lista la moto?", Q.MI_TURNO),
    FraseDePrueba("cuántos tengo adelante", Q.MI_TURNO),
    # --- Cancelar ---
    FraseDePrueba("ya no voy a poder ir", Q.CANCELAR),
    FraseDePrueba("me salió un imprevisto, no alcanzo a llegar", Q.CANCELAR),
    FraseDePrueba("quíteme de la fila, ya no voy", Q.CANCELAR),
    FraseDePrueba("mejor otro día, hoy no puedo", Q.CANCELAR),
    FraseDePrueba("ya no necesito el turno", Q.CANCELAR),
    FraseDePrueba("Quiero cancelar mi turno.", Q.CANCELAR, prueba="I"),
    # En Colombia, cancelar también es pagar.
    FraseDePrueba("puedo cancelar con nequi?", Q.PREGUNTA),
    # --- Llegó o va en camino ---
    FraseDePrueba("ya estoy afuera esperando", Q.LLEGUE),
    FraseDePrueba("voy saliendo para allá", Q.EN_CAMINO),
    # --- Pico y placa ---
    FraseDePrueba("qué placas tienen pico y placa hoy", Q.PICO_Y_PLACA),
    FraseDePrueba("mañana tengo pico y placa con la moto?", Q.PICO_Y_PLACA, vehiculo="Moto"),
    # --- Mis lavadas ---
    FraseDePrueba("me gustaría saber cuántas lavadas llevo con ustedes", Q.MIS_LAVADAS),
    FraseDePrueba("cuándo fue la última vez que lavé la moto?", Q.MIS_LAVADAS),
    FraseDePrueba("cuántas veces he ido a lavar el carro donde ustedes", Q.MIS_LAVADAS),
    # --- Quejas ---
    FraseDePrueba("oiga me atendieron mal", Q.QUEJA),
    FraseDePrueba("el carro quedó mal lavado", Q.QUEJA),
    FraseDePrueba("oigan su servicio es pésimo", Q.QUEJA),
    FraseDePrueba("el señor que me atendió fue muy grosero", Q.QUEJA),
    FraseDePrueba("me rayaron la puerta de la camioneta", Q.QUEJA, vehiculo="Camioneta"),
    FraseDePrueba("me cobraron el encerado y no me lo hicieron", Q.QUEJA),
    # --- Felicitaciones ---
    FraseDePrueba("oiga su servicio es bueno", Q.FELICITACION),
    FraseDePrueba("la moto quedó como nueva, muchas gracias", Q.FELICITACION, vehiculo="Moto"),
    FraseDePrueba("los felicito, excelente atención", Q.FELICITACION),
    # --- Hablar con una persona ---
    FraseDePrueba("necesito hablar con el encargado", Q.HABLAR_CON_PERSONA),
    FraseDePrueba("me puede llamar alguien por favor?", Q.HABLAR_CON_PERSONA),
    FraseDePrueba("quiero hablar con una persona, no con un robot", Q.HABLAR_CON_PERSONA),
    # --- Preguntas sobre el negocio (las contesta la ficha) ---
    FraseDePrueba("a qué hora abren el domingo?", Q.PREGUNTA),
    FraseDePrueba("reciben nequi?", Q.PREGUNTA),
    FraseDePrueba("dónde quedan?", Q.PREGUNTA),
    FraseDePrueba("tienen sala de espera?", Q.PREGUNTA),
    FraseDePrueba("puedo pagar con tarjeta?", Q.PREGUNTA),
    FraseDePrueba("gracias, y abren mañana?", Q.PREGUNTA),
    FraseDePrueba("puedo apartar turno para mañana a las 3?", Q.PREGUNTA, tambien=(Q.PEDIR_TURNO,)),
    FraseDePrueba(
        "¿Puedes lavar camiones o tractomulas?",
        Q.PREGUNTA,
        vehiculo=OTRO_VEHICULO,
        tambien=(Q.PEDIR_TURNO,),
        prueba="A",
    ),
    FraseDePrueba("¿Tampoco guardan el número?", Q.PREGUNTA, prueba="B"),
    FraseDePrueba(
        "Ola lavan tractomula?",
        Q.PREGUNTA,
        vehiculo=OTRO_VEHICULO,
        tambien=(Q.PEDIR_TURNO,),
        prueba="G",
    ),
    FraseDePrueba(
        "¿Cuánto cuesta lavar una tractomula?",
        Q.PRECIOS,
        vehiculo=OTRO_VEHICULO,
        tambien=(Q.PREGUNTA,),
        prueba="H",
    ),
    # --- Saludos y despedidas ---
    FraseDePrueba("buenas", Q.SALUDO),
    FraseDePrueba("Hola, ¿cómo estás?", Q.SALUDO, tambien=(Q.CHARLA,), prueba="E"),
    FraseDePrueba("buenas noches, cómo estás?", Q.SALUDO, tambien=(Q.CHARLA,)),
    FraseDePrueba("listo, muchas gracias", Q.DESPEDIDA),
    FraseDePrueba("chao, que estés bien", Q.DESPEDIDA),
    # --- Charla ---
    FraseDePrueba("jajaja ok", Q.CHARLA, tambien=(Q.DESPEDIDA,)),
    FraseDePrueba("te amo", Q.CHARLA),
    FraseDePrueba("estoy cansado", Q.CHARLA),
    FraseDePrueba("me quiero ir a dormir", Q.CHARLA),
    FraseDePrueba("eres un robot?", Q.CHARLA, tambien=(Q.PREGUNTA,)),
    # Otros temas: conversa, pero no se vuelve un asistente para todo (política de Meta).
    FraseDePrueba("viste el partido de anoche?", Q.CHARLA),
    FraseDePrueba("me ayudas con una tarea de matemáticas?", Q.CHARLA),
    # --- Mensajes graves: el bot no improvisa ---
    FraseDePrueba("ya no quiero vivir", Q.CRISIS),
    FraseDePrueba("a veces pienso en quitarme la vida", Q.CRISIS),
    FraseDePrueba("jajaja me muero de la risa", Q.CHARLA),  # una forma de hablar
    # --- No se entiende ---
    FraseDePrueba("asdfgh", Q.OTRO),
    FraseDePrueba("kjsdhfk", Q.OTRO),
    # --- Con datos personales: deben llegar tapados ---
    FraseDePrueba("quiero turno para la moto ABC12D", Q.PEDIR_TURNO, vehiculo="Moto"),
    FraseDePrueba("llámenme al 300 123 4567 que ya no voy", Q.CANCELAR),
    # --- Con memoria: se entienden por lo que se venían diciendo ---
    FraseDePrueba(
        "sí porfa",
        Q.PEDIR_TURNO,
        antes=(
            cliente("están atendiendo hoy?"),
            bot("¡Sí, hoy atendemos hasta las 6:00 p.m.! ¿Quieres que te aparte un turno?"),
        ),
    ),
    FraseDePrueba(
        "y para la camioneta?",
        Q.PRECIOS,
        vehiculo="Camioneta",
        antes=(
            cliente("cuánto vale lavar la moto?"),
            bot("Servicios para moto: *Básico* · $18.000, *Intermedio* · $25.000."),
        ),
    ),
    FraseDePrueba(
        "¿Cuánto cuesta?",
        Q.PRECIOS,
        servicio="Encerado",
        antes=(
            cliente("qué es eso del encerado?"),
            bot("El Encerado deja la pintura protegida y con brillo, como recién salida."),
        ),
        prueba="D",
    ),
    FraseDePrueba(
        "el del medio",
        Q.VER_FILA,
        antes=(bot("¡Hola, Ana! Qué bueno saludarte de nuevo.", *MENU),),
    ),
    FraseDePrueba(
        "jaja sí, todo el día",
        Q.CHARLA,
        antes=(
            cliente("uff estoy muy cansado"),
            bot("Uy, se nota que fue un día largo. ¿Te tocó trabajar mucho?"),
        ),
    ),
    FraseDePrueba(
        "no gracias, era solo para saber",
        Q.DESPEDIDA,
        tambien=(Q.CHARLA,),
        antes=(
            cliente("a qué hora cierran hoy?"),
            bot("Hoy atendemos hasta las 6:00 p.m. ¿Te aparto un turno?"),
        ),
    ),
    FraseDePrueba(
        "Aquí viendo el partido",
        Q.CHARLA,
        antes=(
            cliente("Hola, ¿cómo estás?"),
            bot("¡Hola! Muy bien, gracias por preguntar. ¿Y tú qué tal?", *MENU),
        ),
        prueba="E",
    ),
    # --- En medio de una pregunta del bot: contesta, pregunta o cambia de tema ---
    FraseDePrueba(
        "Una moto.",
        Q.PEDIR_TURNO,
        vehiculo="Moto",
        tambien=(Q.OTRO,),
        antes=(cliente("Necesito lavar mi vehículo"), bot("¿Qué vehículo traes?", *VEHICULOS)),
        en_que_va=esperando(Paso.VEHICULO, *VEHICULOS),
        prueba="C",
    ),
    FraseDePrueba(
        "Por cierto, ¿cómo estuvo el clima hoy?",
        Q.CHARLA,
        antes=(
            cliente("Quiero lavar mi moto"),
            bot("¿Qué servicio quieres para tu moto?"),
        ),
        en_que_va=esperando(Paso.SERVICIO, *SERVICIO),
        prueba="F",
    ),
    FraseDePrueba(
        "el general",
        Q.PEDIR_TURNO,
        servicio="General",
        tambien=(Q.OTRO,),
        en_que_va=esperando(Paso.SERVICIO, *SERVICIO),
    ),
    FraseDePrueba(
        "para qué necesitan la placa?",
        Q.PREGUNTA,
        en_que_va=esperando(Paso.PLACA, "Ahora no"),
    ),
    FraseDePrueba(
        "sí señor, hágale pues",
        Q.PEDIR_TURNO,
        si_o_no=SI,
        tambien=(Q.CHARLA, Q.OTRO),
        en_que_va=esperando(Paso.CONFIRMAR, *CONFIRMAR),
    ),
    FraseDePrueba(
        "uy no, así no, mejor después",
        Q.PEDIR_TURNO,
        si_o_no=NO,
        tambien=(Q.CHARLA, Q.OTRO, Q.CANCELAR, Q.DESPEDIDA),
        en_que_va=esperando(Paso.CONFIRMAR, *CONFIRMAR),
    ),
    FraseDePrueba(
        "para qué quieren mi número?",
        Q.PREGUNTA,
        en_que_va=esperando(Paso.PERMISO_CONTACTO, *PERMISO),
    ),
)
