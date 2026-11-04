"""Frases de prueba para Gemini: cómo escribe la gente en un lavadero de Bucaramanga, y lo
que el bot debe entender de cada una.

Se corren contra el modelo de verdad con `uv run auto-ibaguer probar-gemini`, cada vez que
se cambie el modelo en Configuración (o cuando Google lo actualice). Si una frase falla,
se mira por qué antes de usar ese modelo con clientes.

Para agregar una, basta escribirla aquí con lo que se espera. Las que mandan una placa o
un celular prueban, además, que esos datos lleguen tapados.
"""

from dataclasses import dataclass

from app.bot.interprete import CARRO, Intencion

Q = Intencion  # para que cada frase quepa en una línea


@dataclass(frozen=True)
class FraseDePrueba:
    texto: str
    intencion: Intencion
    vehiculo: str | None = None  # si se espera que diga de qué vehículo habla
    servicio: str | None = None


FRASES: tuple[FraseDePrueba, ...] = (
    # --- Pedir turno ---
    FraseDePrueba("quiero lavar la moto", Q.PEDIR_TURNO, vehiculo="Moto"),
    FraseDePrueba("me puede dar un turno para el carro?", Q.PEDIR_TURNO, vehiculo=CARRO),
    FraseDePrueba("necesito lavar la camioneta hoy", Q.PEDIR_TURNO, vehiculo="Camioneta"),
    FraseDePrueba("hay cupo para ahorita?", Q.PEDIR_TURNO),
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
    # --- Charla ---
    FraseDePrueba("jajaja ok", Q.CHARLA),
    FraseDePrueba("te amo", Q.CHARLA),
    FraseDePrueba("estoy cansado", Q.CHARLA),
    FraseDePrueba("me quiero ir a dormir", Q.CHARLA),
    FraseDePrueba("eres un robot?", Q.CHARLA),
    FraseDePrueba("buenas noches, cómo estás?", Q.CHARLA),
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
)
