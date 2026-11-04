"""Lo que dice el bot. Los textos son propios: siempre iguales, nunca los inventa una IA.

La única excepción es la respuesta a una pregunta sobre el negocio o a la charla, que la
escribe Gemini con la ficha del negocio y el bot revisa (app.bot.redactor). Si no sirve,
sale el texto fijo de aquí ("charla", "pregunta_al_administrador").

Cada texto tiene un nombre y huecos entre llaves ({numero}, {hora}...) que el bot llena
con los datos del sistema. *así* sale en negrilla en WhatsApp.

Estos son los originales. Los más importantes se pueden cambiar desde el dashboard
(Configuración > Textos del bot, app.bot.textos_editables): el cambio se guarda en la
tabla `configuracion` y `Textos` lo usa en lugar del original.
"""

import logging
import string
from collections.abc import Mapping

from app.bot.mensajes import Opcion

logger = logging.getLogger(__name__)

TEXTOS: dict[str, str] = {
    # --- Saludo y menú ---
    "saludo": "¡Hola! Bienvenido a {negocio}. Por WhatsApp damos turnos {horario}. "
    "¿En qué te ayudamos?",
    "saludo_cliente": "¡Hola, {nombre}! Qué bueno saludarte de nuevo. ¿En qué te ayudamos?",
    "menu": "¿En qué más te ayudamos?",
    "no_entendi": "No te entendí bien. Puedes tocar una opción, escribir *mi turno* o "
    "*cancelar*, o pedir *hablar con una persona*.",
    "solo_texto": "Por ahora solo entiendo mensajes escritos y los botones. ¿En qué te ayudamos?",
    "boton_vencido": "Ese botón ya no está vigente. ¿En qué te ayudamos?",
    "toca_una_opcion": "Toca una de las opciones, por favor.",
    "despedida": "¡Con gusto! Que tengas un buen día.",
    "pico_y_placa": "Por ahora no damos información de pico y placa por aquí. ¿En qué más te "
    "ayudamos?",
    "error_interno": "Tuvimos un problema para atenderte. Intenta de nuevo en un momento o "
    "llama al {telefono}.",
    # --- Cuando no se dan turnos ---
    "fuera_de_horario": "Por WhatsApp damos turnos {horario}, así que ahora no podemos "
    "darte uno. Abrimos {apertura}. Mientras tanto puedes ver la fila o los precios.",
    "bot_apagado": "Hoy no estamos dando turnos por WhatsApp. Puedes venir directamente al "
    "local o llamar al {telefono}.",
    # --- Aviso de datos ---
    "datos_no": "Entendido. Sin tu autorización no guardamos tus datos ni podemos darte turno "
    "por WhatsApp. Puedes venir directamente al local y ahí te damos turno.",
    # --- Pedir turno ---
    "pedir_nombre": "¿A nombre de quién hacemos el turno?",
    "nombre_invalido": "No alcancé a leer el nombre. ¿Me lo escribes otra vez?",
    "pedir_vehiculo": "¿Qué vehículo traes?",
    "pedir_vehiculo_conocido": "¿Para cuál vehículo es el turno?",
    "pedir_placa": "¿Cuál es la placa de tu {vehiculo}? La pedimos por seguridad: así te "
    "entregamos el vehículo solo a ti. Si no la tienes a mano, toca *Ahora no* y la anotamos "
    "cuando llegues.",
    "placa_invalida": "{error} Escríbela otra vez (por ejemplo, {ejemplo}) o toca *Ahora no*.",
    "placa_de_otro": "La placa {placa} ya está registrada con otro cliente. Revísala y "
    "escríbela otra vez, o toca *Ahora no* y lo resolvemos en el local.",
    "pedir_servicio": "¿Qué servicio quieres para tu {vehiculo}? El pago es en el local.",
    "servicio_no_disponible": "Ese servicio ya no está disponible. Elige otro, por favor.",
    "resumen": "Revisa tu turno:\n\n*Nombre:* {nombre}\n*Vehículo:* {vehiculo}\n"
    "*Servicio:* {servicio}\n*Fila de {fila}:* {como_va}\n\n¿Lo reservamos?",
    "mejor_no": "Listo, no reservamos nada. Cuando quieras, aquí estamos.",
    "turno_confirmado": "¡Listo, {nombre}! Tu turno es *{numero}*.\n\n{situacion}\n\n"
    "Cuando te llamemos tienes {gracia} minutos para llegar. Si no llegas, pasas al final de "
    "la fila, y la segunda vez pierdes el turno. Escribe *mi turno* para ver cómo vas o "
    "*cancelar* si no puedes venir.",
    "confirmado_llamado": "Hay un puesto libre: ven ya. Tienes hasta {hora} para llegar.",
    "confirmado_cerca": "Ya casi te toca ({adelante}): sal hacia el lavadero.",
    "confirmado_en_fila": "{adelante_mayuscula}{espera}. Te avisamos por aquí cuando falten "
    "{avisar} turnos para que salgas hacia el lavadero.",
    "no_se_pudo": "No pudimos darte el turno: {motivo} Si necesitas ayuda, llama al {telefono}.",
    # --- Segundo turno ---
    "segundo_turno": "Ya tienes el turno *{numero}* en la fila de {fila}: {estado}. Por "
    "WhatsApp se puede tener un turno por fila. ¿Qué hacemos?",
    "mantener": "Perfecto, lo mantenemos. Te esperamos.",
    # --- La fila y los precios ---
    "fila": "Así está la fila ahora:\n\n{filas}",
    "fila_cerrada": "hoy no estamos atendiendo esta fila",
    "fila_libre": "hay puesto libre, puedes venir ya",
    "fila_sin_espera": "nadie está esperando; el próximo puesto se libera {espera}",
    "fila_con_espera": "{vehiculos} esperando; un turno nuevo entraría {espera}",
    "precios_tipo": "¿Para qué vehículo quieres ver los precios?",
    "precios_carro": "¿Automóvil o camioneta?",
    "precios": "Servicios para {vehiculo}:\n\n{servicios}\n\nEl pago es en el local.",
    # --- Mi turno ---
    "sin_turno": "No tienes turnos activos. ¿Quieres pedir uno?",
    "mi_turno_en_fila": "Tu turno *{numero}* ({fila}): {adelante}{espera}.",
    "mi_turno_llamado": "Tu turno *{numero}* ({fila}): ¡ya te estamos llamando! Tienes hasta "
    "{hora} para llegar.",
    "mi_turno_llego": "Tu turno *{numero}* ({fila}): ya estás en el local. Entras al próximo "
    "puesto libre.",
    "mi_turno_en_lavado": "Tu turno *{numero}* ({fila}): tu vehículo se está lavando.",
    "mi_turno_listo": "Tu turno *{numero}* ({fila}): tu vehículo está listo. Puedes recogerlo "
    "y pagar en el local.",
    # --- Cancelar ---
    "cancelar_confirmar": "¿Seguro que quieres cancelar tu turno *{numero}*? Si cancelas, "
    "pierdes tu lugar en la fila.",
    "cancelar_cual": "¿Cuál turno quieres cancelar? Si cancelas, pierdes tu lugar en esa fila.",
    "cancelado": "Listo, cancelamos tu turno *{numero}*. Gracias por avisarnos: así la fila "
    "avanza más rápido. Cuando quieras, pide otro.",
    "no_cancelado": "Perfecto, tu turno sigue igual.",
    "cancelar_en_el_local": "Tu vehículo ya está en el local, así que eso se resuelve allá. "
    "Si necesitas algo, llama al {telefono}.",
    "sin_turno_para_cancelar": "No tienes turnos para cancelar.",
    "ya_no_se_puede_cancelar": "Tu turno *{numero}* cambió y ya no se puede cancelar por aquí. "
    "Escribe *mi turno* para ver cómo va.",
    # --- Llegada y en camino ---
    "llegada": "¡Bienvenido! Ya te marcamos como llegado ({numeros}): entras al próximo "
    "puesto libre.",
    "ya_llego": "Ya te tenemos como llegado ({numeros}). Entras al próximo puesto libre.",
    "en_camino": "Gracias por avisar, te esperamos.",
    "en_camino_llamado": "Gracias por avisar, te esperamos. Recuerda que tienes hasta {hora}.",
    # --- Código QR del mostrador ---
    "vinculado": "Listo: quedaste conectado al turno *{numero}*. Te avisamos por aquí cuando "
    "tu vehículo esté listo.",
    "pedir_numero_de_turno": "No encontramos tu celular en la fila de hoy. ¿Cuál es tu número "
    "de turno? Te lo dan en el mostrador (por ejemplo, C-07).",
    "numero_invalido": "Escribe el número de turno como te lo dieron, por ejemplo C-07.",
    "numero_no_existe": "No encontramos el turno {numero} en la fila de hoy. Revísalo y "
    "escríbelo otra vez.",
    "numero_otro_celular": "Ese turno tiene otro celular registrado. Pídele al administrador "
    "que lo revise.",
    # --- Quejas y hablar con una persona (le llegan al administrador en Comentarios) ---
    "queja_cliente": "Qué pena, {nombre}, con lo que pasó. Ya le avisamos al administrador "
    "para que lo revise y te responda lo antes posible. Si quieres, cuéntanos más: qué pasó "
    "y cuándo.",
    "queja": "Qué pena con lo que pasó. Ya le avisamos al administrador. ¿Quieres que te "
    "responda? Para eso guardamos tu número de WhatsApp, solo para responderte. También "
    "puedes contarnos más de lo que pasó.",
    "queja_permiso": "¿Quieres que el administrador te responda? Para eso guardamos tu número "
    "de WhatsApp, solo para responderte.",
    "queja_anonima": "Entendido, no guardamos tu número. Tu comentario le llega al "
    "administrador sin tus datos. Gracias por ayudarnos a mejorar.",
    "persona": "Listo, {nombre}. Ya le avisamos al administrador para que te responda lo "
    "antes posible. Si quieres, cuéntanos de qué se trata. Si es urgente, llama al {telefono}.",
    "persona_permiso": "Con gusto te comunicamos con el administrador. Para que te responda "
    "guardamos tu número de WhatsApp, solo para eso. ¿Estás de acuerdo?",
    "persona_sin_permiso": "Entendido, no guardamos tu número. Si quieres hablar con el "
    "administrador, llama al {telefono}.",
    "contacto_si": "Listo, el administrador te responde lo antes posible. Si quieres, "
    "cuéntanos más mientras tanto.",
    "detalle_recibido": "Gracias, ya quedó anotado para el administrador.",
    "anotado": "Lo anotamos.",
    "detalle_solo_texto": "Por ahora no podemos ver fotos, audios ni videos por aquí. "
    "Escríbenos con palabras, por favor.",
    # --- Felicitaciones ---
    "felicitacion": "¡Muchas gracias! Nos alegra mucho que te guste el servicio. Se lo "
    "contamos al equipo.",
    "felicitacion_cliente": "¡Muchas gracias, {nombre}! Nos alegra mucho que te guste el "
    "servicio. Se lo contamos al equipo.",
    # --- Sus lavadas ---
    "mis_lavadas": "{nombre}, llevas {lavadas} con nosotros; la más reciente fue {ultima}. "
    "¡Gracias por preferirnos!",
    "sin_lavadas": "{nombre}, todavía no tienes lavadas con nosotros. ¿Pedimos la primera?",
    "mis_lavadas_sin_registro": "No encontramos lavadas con este número de WhatsApp. Las "
    "contamos cuando pides turno por aquí o das este número en el local.",
    # --- Preguntas sobre el negocio y charla (las demás respuestas las escribe Gemini) ---
    "charla": "Gracias por escribirnos. Por aquí te ayudamos con tu turno, la fila o los precios.",
    "pregunta_al_administrador": "Esa no la sé, {nombre}, pero ya se la pasé al administrador "
    "para que te responda lo antes posible.",
    "pregunta_sin_respuesta": "Esa no la sé todavía. ¿Quieres que el administrador te "
    "responda? Para eso guardamos tu número de WhatsApp, solo para responderte.",
    "pregunta_anonima": "Entendido, no guardamos tu número. Si quieres la respuesta, llama al "
    "{telefono}.",
    # --- Mensajes graves: el bot no improvisa (líneas verificadas en noviembre de 2026) ---
    "ayuda_urgente": "Siento mucho que te sientas así. No tienes que pasar por esto sin ayuda: "
    "puedes hablar ya mismo, gratis y a cualquier hora, con la Línea 106 de salud mental, o "
    "escribirle por WhatsApp a la Línea Espérame de Bucaramanga al 322 964 3755. Si estás en "
    "peligro ahora, llama al 123.",
    # --- Avisos de la fila (salen de la bandeja de salida) ---
    "aviso_anticipado": "Tu turno *{turno}* está cerca: {adelante}. Sal hacia el lavadero "
    "para que llegues a tiempo.",
    "aviso_llamado": "¡Es tu turno! Ya hay un puesto libre para *{turno}*. Tienes hasta {hora} "
    "para llegar; si no llegas, pasas al final de la fila.",
    "aviso_pasa_al_final": "No alcanzaste a llegar a tiempo, así que tu turno *{turno}* pasó al "
    "final de la fila: {adelante}. Si tampoco llegas la próxima vez, pierdes el turno.",
    "aviso_pasa_al_final_cerca": "No alcanzaste a llegar a tiempo, así que tu turno *{turno}* "
    "pasó al final de la fila, pero ya casi te toca otra vez ({adelante}): sal hacia el "
    "lavadero. Si tampoco llegas, pierdes el turno.",
    "aviso_no_llego": "No llegaste a tiempo por segunda vez, así que perdiste el turno "
    "*{turno}*. Si todavía quieres lavar tu vehículo, pide un turno nuevo.",
    "aviso_vehiculo_listo": "¡Tu vehículo está listo (turno *{turno}*)! Ya puedes recogerlo y "
    "pagar en el local. Gracias por elegir {negocio}.",
}

# Los botones y opciones que se repiten.
BOTONES: dict[str, str] = {
    "pedir": "Pedir turno",
    "fila": "Ver la fila",
    "precios": "Servicios y precios",
    "acepto": "Acepto",
    "no_acepto": "No acepto",
    "otro_vehiculo": "Otro vehículo",
    "ahora_no": "Ahora no",
    "ver_servicios": "Ver servicios",
    "en_el_local": "Lo decido en el local",
    "en_el_local_descripcion": "Lo eliges cuando llegues",
    "confirmar": "Confirmar turno",
    "cambiar": "Cambiar datos",
    "mejor_no": "Mejor no",
    "mantener": "Mantenerlo",
    "cancelarlo": "Cancelarlo",
    "si_cancelar": "Sí, cancelar",
    "no_cancelar": "No, mantenerlo",
    "en_camino": "En camino",
    "llegue": "Ya estoy aquí",
    "responder_si": "Sí, que me responda",
    "responder_no": "No, gracias",
}


def huecos(plantilla: str) -> list[str]:
    """Los nombres de los huecos de un texto: "Tu turno {numero}" -> ["numero"].

    Solo vale el nombre de un dato entre llaves. Si las llaves están mal cerradas, o entre
    ellas va algo más (un punto, una conversión...), lanza ValueError.
    """
    nombres = []
    for _, nombre, formato, conversion in string.Formatter().parse(plantilla):
        if nombre is None:
            continue
        if not nombre.isidentifier() or formato or conversion:
            raise ValueError(f"hueco no permitido: {{{nombre}}}")
        nombres.append(nombre)
    return nombres


def llenar(plantilla: str, datos: Mapping[str, object]) -> str:
    """El texto con sus huecos llenos. Si le falta un dato o tiene un hueco raro, falla."""
    faltan = set(huecos(plantilla)) - datos.keys()
    if faltan:
        raise KeyError(", ".join(sorted(faltan)))
    lleno = plantilla.format_map(datos)
    # Una hora al final de una frase no lleva dos puntos: "hasta las 7:30 p.m."
    return lleno.replace("a.m..", "a.m.").replace("p.m..", "p.m.")


class Textos:
    """Lo que dice el bot: los textos que cambió el administrador y, los demás, los originales.

    Se usa como una función: `texto("saludo", negocio=..., horario=...)`. Si un texto
    cambiado no se puede llenar (le falta un dato que el bot ya no tiene, por ejemplo),
    el bot usa el original y lo anota en el registro: el cliente nunca se queda sin
    respuesta por un texto mal escrito.
    """

    def __init__(self, cambiados: Mapping[str, str] | None = None) -> None:
        self.cambiados = dict(cambiados or {})

    def __call__(self, clave: str, /, **datos: object) -> str:
        cambiado = self.cambiados.get(clave)
        if cambiado is not None:
            try:
                return llenar(cambiado, datos)
            except (KeyError, IndexError, ValueError):
                logger.warning(
                    "El texto del bot «%s» cambiado en Configuración no se pudo usar: "
                    "el bot usó el original.",
                    clave,
                )
        return llenar(TEXTOS[clave], datos)


# Los textos originales, para lo que no depende de lo que cambió el administrador.
texto = Textos()


def adelante(cantidad: int) -> str:
    """0 -> "eres el siguiente"; 1 -> "tienes 1 turno adelante"; 3 -> "tienes 3 turnos..." """
    if cantidad == 0:
        return "eres el siguiente"
    return f"tienes {cantidad} turno{'s' if cantidad != 1 else ''} adelante"


def botones_del_llamado() -> tuple[Opcion, ...]:
    """Lo que puede contestar alguien a quien llamaron: así el administrador sabe que viene."""
    return (
        Opcion("turno.en_camino", BOTONES["en_camino"]),
        Opcion("turno.llegue", BOTONES["llegue"]),
    )
