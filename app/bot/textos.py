"""Lo que dice el bot. Los textos son propios: siempre iguales, nunca los inventa una IA.

Cada texto tiene un nombre y huecos entre llaves ({numero}, {hora}...) que el bot llena
con los datos del sistema. *así* sale en negrilla en WhatsApp. En una parte siguiente del
hito 5 estos textos se podrán cambiar desde Configuración sin tocar el código.
"""

from app.bot.mensajes import Opcion

TEXTOS: dict[str, str] = {
    # --- Saludo y menú ---
    "saludo": "¡Hola! Bienvenido a {negocio}. Por WhatsApp damos turnos {horario}. "
    "¿En qué te ayudamos?",
    "saludo_cliente": "¡Hola, {nombre}! Qué bueno saludarte de nuevo. ¿En qué te ayudamos?",
    "menu": "¿En qué más te ayudamos?",
    "no_entendi": "Por ahora entiendo los botones y las palabras *mi turno* y *cancelar*. "
    "¿En qué te ayudamos?",
    "solo_texto": "Por ahora solo entiendo mensajes escritos y los botones. ¿En qué te ayudamos?",
    "boton_vencido": "Ese botón ya no está vigente. ¿En qué te ayudamos?",
    "toca_una_opcion": "Toca una de las opciones, por favor.",
    "despedida": "¡Con gusto! Que tengas un buen día.",
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
}


def texto(clave: str, /, **datos: object) -> str:
    """El texto `clave` con sus huecos llenos."""
    lleno = TEXTOS[clave].format_map(datos)
    # Una hora al final de una frase no lleva dos puntos: "hasta las 7:30 p.m."
    return lleno.replace("a.m..", "a.m.").replace("p.m..", "p.m.")


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
