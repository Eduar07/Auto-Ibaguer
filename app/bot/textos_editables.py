"""Los textos del bot que el administrador puede cambiar (Configuración > Textos del bot).

Son los que más leen los clientes: el saludo, el turno confirmado, los avisos de la
fila, lo que contesta a una queja... Los demás (un número de turno mal escrito, una
placa de otro cliente...) se quedan en app/bot/textos.py.

Un texto cambiado se guarda en la tabla `configuracion` ("texto_bot.saludo") y el bot lo
usa desde el siguiente mensaje (app.bot.textos.Textos). Si se guarda igual al original,
la fila se borra: así, si algún día mejora el original, le llega a quien no lo cambió.

Antes de guardar se revisa que el texto solo use los datos que el bot tiene para ese
mensaje y que no le falte ninguno de los necesarios: un texto mal escrito nunca deja a
un cliente sin su número de turno ni su hora límite.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from app import formato, tiempo
from app.bot import textos
from app.bot.mensajes import Lista, Mensaje, Opcion
from app.bot.textos import BOTONES, CON_SUS_PALABRAS, TEXTOS, Textos, huecos, llenar
from app.catalogo.parametros import PREFIJO_TEXTO_DEL_BOT, Parametros
from app.catalogo.tablas import Configuracion
from app.errores import ReglaDelNegocio

# Qué es cada dato que el bot llena, para explicárselo al administrador.
DATOS: dict[str, str] = {
    "negocio": "el nombre del lavadero",
    "horario": "los días y las horas en que se dan turnos",
    "apertura": "cuándo vuelve a abrir",
    "telefono": "el teléfono del lavadero",
    "nombre": "el nombre del cliente",
    "vehiculo": "el vehículo",
    "servicio": "el servicio que eligió",
    "fila": "la fila: carros o motos",
    "como_va": "cómo está la fila",
    "numero": "el número de turno",
    "numeros": "el número de turno",
    "turno": "el número de turno",
    "situacion": "si puede venir ya o cuántos turnos tiene adelante",
    "gracia": "los minutos que tiene para llegar cuando lo llaman",
    "hora": "la hora límite para llegar",
    "adelante": "cuántos turnos tiene adelante",
    "adelante_mayuscula": "cuántos turnos tiene adelante, con mayúscula al comienzo",
    "espera": "cuánto falta para que entre",
    "avisar": "cuántos turnos faltan cuando se le avisa",
    "filas": "cómo está cada fila",
    "servicios": "los servicios con su precio",
    "lavadas": "cuántas lavadas lleva (por ejemplo, 7 lavadas)",
    "ultima": "cuándo fue su última lavada (hoy, ayer, el sábado...)",
}

_MENU = (BOTONES["pedir"], BOTONES["fila"], BOTONES["precios"])
_FILA_Y_PRECIOS = (BOTONES["fila"], BOTONES["precios"])
_LLAMADO = (BOTONES["en_camino"], BOTONES["llegue"])
_PERMISO = (BOTONES["responder_si"], BOTONES["responder_no"])


@dataclass(frozen=True)
class Editable:
    clave: str  # el nombre del texto en app.bot.textos.TEXTOS
    titulo: str
    cuando: str  # cuándo lo manda el bot
    opcionales: frozenset[str] = frozenset()  # datos que se pueden quitar del texto
    # Hasta cuántos caracteres: con los datos ya llenos, el mensaje cabe en WhatsApp
    # (1.024 con botones). Lo revisa una prueba.
    maximo: int = 500
    botones: tuple[str, ...] = ()  # los que van con el mensaje, para la vista previa
    lista: str | None = None  # o el botón de su lista
    dentro_de: str | None = None  # si es una parte de otro texto (en su hueco {situacion})
    ejemplos: Mapping[str, object] = field(default_factory=dict, hash=False, compare=False)

    @property
    def original(self) -> str:
        return TEXTOS[self.clave]

    @property
    def datos(self) -> tuple[str, ...]:
        """Los datos que puede llevar, en el orden del original."""
        return tuple(dict.fromkeys(huecos(self.original)))

    @property
    def obligatorios(self) -> frozenset[str]:
        return frozenset(self.datos) - self.opcionales

    @property
    def con_sus_palabras(self) -> bool:
        """Con Gemini, el bot lo dice con sus palabras (app.bot.textos.CON_SUS_PALABRAS)."""
        return self.clave in CON_SUS_PALABRAS


@dataclass(frozen=True)
class Grupo:
    id: str
    titulo: str
    textos: tuple[Editable, ...]


GRUPOS: tuple[Grupo, ...] = (
    Grupo(
        "saludo",
        "Saludo y menú",
        (
            Editable(
                "saludo",
                "Saludo a un cliente nuevo",
                "Cuando alguien escribe por primera vez.",
                opcionales=frozenset({"negocio", "horario"}),
                botones=_MENU,
            ),
            Editable(
                "saludo_cliente",
                "Saludo a un cliente que ya conoce",
                "Cuando escribe alguien que ya pidió turno antes.",
                opcionales=frozenset({"nombre"}),
                botones=_MENU,
            ),
            Editable(
                "menu",
                "Volver al menú",
                "Cuando el cliente vuelve a escribir después de terminar algo.",
                botones=_MENU,
            ),
            Editable(
                "no_entendi",
                "Cuando no entiende",
                "Cuando el cliente escribe algo que el bot no reconoce.",
                botones=_MENU,
            ),
            Editable(
                "sin_gemini",
                "Cuando Gemini no responde",
                "Cuando Gemini está conectado pero no respondió a tiempo o se acabó la cuota: "
                "el mensaje puede estar bien escrito, así que se le pide intentar de nuevo.",
                opcionales=frozenset({"telefono"}),
                botones=_MENU,
            ),
            Editable(
                "despedida",
                "Despedida",
                "Cuando el cliente escribe gracias o chao.",
            ),
            Editable(
                "pico_y_placa",
                "Pico y placa",
                "Cuando el cliente pregunta por el pico y placa (lo entiende Gemini).",
                botones=_MENU,
            ),
        ),
    ),
    Grupo(
        "sin_turnos",
        "Cuando no se dan turnos",
        (
            Editable(
                "fuera_de_horario",
                "Fuera del horario",
                "Cuando piden turno fuera del horario de atención.",
                opcionales=frozenset({"horario", "apertura"}),
                botones=_FILA_Y_PRECIOS,
            ),
            Editable(
                "bot_apagado",
                "Bot apagado",
                "Cuando piden turno con el bot apagado en Configuración.",
                opcionales=frozenset({"telefono"}),
                botones=_FILA_Y_PRECIOS,
            ),
            Editable(
                "datos_no",
                "Si no acepta el aviso de datos",
                "Cuando un cliente nuevo toca No acepto.",
                botones=_FILA_Y_PRECIOS,
            ),
        ),
    ),
    Grupo(
        "pedir_turno",
        "Pedir turno",
        (
            Editable(
                "pedir_nombre",
                "Pregunta por el nombre",
                "A un cliente nuevo, después de aceptar el aviso de datos.",
            ),
            Editable(
                "pedir_vehiculo",
                "Pregunta por el vehículo",
                "Cuando el bot todavía no sabe qué vehículo trae.",
                botones=("Automóvil", "Camioneta", "Moto"),
            ),
            Editable(
                "pedir_placa",
                "Pregunta por la placa",
                "Después de elegir el vehículo. Puede contestar Ahora no.",
                opcionales=frozenset({"vehiculo"}),
                botones=(BOTONES["ahora_no"],),
            ),
            Editable(
                "pedir_servicio",
                "Pregunta por el servicio",
                "Con la lista de servicios de su vehículo.",
                opcionales=frozenset({"vehiculo"}),
                lista=BOTONES["ver_servicios"],
            ),
            Editable(
                "resumen",
                "Resumen antes de confirmar",
                "Lo último antes de reservar: el cliente revisa sus datos.",
                maximo=400,
                botones=(BOTONES["confirmar"], BOTONES["cambiar"], BOTONES["mejor_no"]),
                ejemplos={"vehiculo": "Automóvil ABC123", "nombre": "Ana Ruiz"},
            ),
            Editable(
                "turno_confirmado",
                "Turno confirmado",
                "Cuando el cliente confirma y queda en la fila.",
                opcionales=frozenset({"nombre", "gracia"}),
                botones=_LLAMADO,
            ),
            Editable(
                "confirmado_llamado",
                "Turno confirmado: hay puesto libre",
                "Va dentro del turno confirmado cuando puede venir ya.",
                maximo=200,
                botones=_LLAMADO,
                dentro_de="turno_confirmado",
            ),
            Editable(
                "confirmado_cerca",
                "Turno confirmado: ya casi le toca",
                "Va dentro del turno confirmado cuando tiene pocos turnos adelante.",
                maximo=200,
                dentro_de="turno_confirmado",
            ),
            Editable(
                "confirmado_en_fila",
                "Turno confirmado: queda en la fila",
                "Va dentro del turno confirmado cuando tiene que esperar.",
                opcionales=frozenset({"espera", "avisar"}),
                maximo=250,
                dentro_de="turno_confirmado",
            ),
            Editable(
                "mejor_no",
                "Si no confirma",
                "Cuando toca Mejor no en el resumen.",
            ),
        ),
    ),
    Grupo(
        "mi_turno",
        "Mi turno, cancelar y llegada",
        (
            Editable(
                "sin_turno",
                "Mi turno, sin turnos",
                "Cuando escribe mi turno y no tiene turnos activos.",
                botones=(BOTONES["pedir"], BOTONES["fila"]),
            ),
            Editable(
                "mi_turno_en_fila",
                "Mi turno: esperando en la fila",
                "Cuando escribe mi turno y está esperando.",
                opcionales=frozenset({"fila", "espera"}),
                maximo=300,
            ),
            Editable(
                "mi_turno_llamado",
                "Mi turno: lo están llamando",
                "Cuando escribe mi turno y ya lo llamaron.",
                opcionales=frozenset({"fila"}),
                maximo=300,
                botones=_LLAMADO,
            ),
            Editable(
                "mi_turno_llego",
                "Mi turno: ya llegó",
                "Cuando escribe mi turno y ya está en el local.",
                opcionales=frozenset({"fila"}),
                maximo=300,
            ),
            Editable(
                "mi_turno_en_lavado",
                "Mi turno: lavándose",
                "Cuando escribe mi turno y su vehículo está en un puesto.",
                opcionales=frozenset({"fila"}),
                maximo=300,
            ),
            Editable(
                "mi_turno_listo",
                "Mi turno: listo",
                "Cuando escribe mi turno y su vehículo ya está listo.",
                opcionales=frozenset({"fila"}),
                maximo=300,
            ),
            Editable(
                "cancelar_confirmar",
                "Pregunta antes de cancelar",
                "Cuando escribe cancelar: siempre se pregunta antes.",
                botones=(BOTONES["si_cancelar"], BOTONES["no_cancelar"]),
            ),
            Editable(
                "cancelado",
                "Turno cancelado",
                "Después de cancelar.",
                botones=(BOTONES["pedir"],),
            ),
            Editable(
                "no_cancelado",
                "Si decide no cancelar",
                "Cuando toca No, mantenerlo.",
            ),
            Editable(
                "llegada",
                "Cuando avisa que llegó",
                "Cuando toca Ya estoy aquí o escribe que llegó.",
            ),
            Editable(
                "en_camino",
                "Cuando avisa que va en camino",
                "Cuando toca En camino antes de que lo llamen.",
            ),
            Editable(
                "en_camino_llamado",
                "En camino, cuando ya lo llamaron",
                "Cuando toca En camino y lo están llamando.",
            ),
        ),
    ),
    Grupo(
        "avisos",
        "Avisos de la fila",
        (
            Editable(
                "aviso_anticipado",
                "Ya casi le toca",
                "Cuando le faltan los turnos que digas en Configuración para que lo llamen.",
            ),
            Editable(
                "aviso_llamado",
                "Lo están llamando",
                "Cuando se libera un puesto para su turno.",
                botones=_LLAMADO,
            ),
            Editable(
                "aviso_pasa_al_final",
                "Pasó al final de la fila",
                "Cuando no llegó en su tiempo de gracia la primera vez.",
                opcionales=frozenset({"adelante"}),
            ),
            Editable(
                "aviso_pasa_al_final_cerca",
                "Pasó al final, pero ya casi le toca",
                "Igual que el anterior, cuando quedó con pocos turnos adelante.",
                opcionales=frozenset({"adelante"}),
            ),
            Editable(
                "aviso_no_llego",
                "Perdió el turno",
                "Cuando no llegó a tiempo por segunda vez.",
                botones=(BOTONES["pedir"],),
            ),
            Editable(
                "aviso_vehiculo_listo",
                "Vehículo listo",
                "Cuando marcas su vehículo como listo en el dashboard.",
                opcionales=frozenset({"negocio"}),
            ),
        ),
    ),
    Grupo(
        "fila_y_precios",
        "La fila y los precios",
        (
            Editable(
                "fila",
                "Cómo está la fila",
                "Cuando toca Ver la fila.",
                maximo=300,
                botones=(BOTONES["pedir"], BOTONES["precios"]),
            ),
            Editable(
                "precios",
                "Servicios y precios",
                "Cuando elige un vehículo en Servicios y precios.",
                opcionales=frozenset({"vehiculo"}),
                maximo=300,
                botones=(BOTONES["pedir"], BOTONES["fila"]),
            ),
        ),
    ),
    Grupo(
        "quejas",
        "Quejas y hablar con una persona",
        (
            Editable(
                "queja_cliente",
                "Queja de un cliente",
                "Cuando un cliente se queja. La queja te llega en Comentarios.",
                opcionales=frozenset({"nombre"}),
            ),
            Editable(
                "queja",
                "Queja de alguien que no es cliente",
                "Igual, pero de alguien que nunca ha pedido turno: se le pide permiso para "
                "guardar su número.",
                botones=_PERMISO,
            ),
            Editable(
                "queja_permiso",
                "Vuelve a pedir el permiso",
                "Si cuenta más de lo que pasó en vez de tocar un botón.",
                botones=_PERMISO,
            ),
            Editable(
                "queja_anonima",
                "Si no da permiso",
                "Cuando toca No, gracias: la queja te llega sin su número.",
            ),
            Editable(
                "persona",
                "Pide hablar con una persona",
                "Cuando un cliente pide hablar con alguien. Te llega en Comentarios.",
                opcionales=frozenset({"nombre", "telefono"}),
            ),
            Editable(
                "persona_permiso",
                "Pide hablar con una persona, sin ser cliente",
                "Se le pide permiso para guardar su número.",
                botones=_PERMISO,
            ),
            Editable(
                "persona_sin_permiso",
                "Si no da permiso para responderle",
                "Cuando toca No, gracias después de pedir hablar con alguien.",
            ),
            Editable(
                "contacto_si",
                "Si da permiso",
                "Cuando toca Sí, que me responda.",
            ),
            Editable(
                "detalle_recibido",
                "Cuando cuenta más",
                "Después de la queja o del pedido de hablar con alguien, cuando escribe más.",
            ),
        ),
    ),
    Grupo(
        "felicitaciones",
        "Felicitaciones y lavadas",
        (
            Editable(
                "felicitacion_cliente",
                "Felicitación de un cliente",
                "Cuando un cliente felicita el servicio. Te llega en Comentarios.",
                opcionales=frozenset({"nombre"}),
                botones=_MENU,
            ),
            Editable(
                "felicitacion",
                "Felicitación de alguien que no es cliente",
                "Igual, de alguien que nunca ha pedido turno.",
                botones=_MENU,
            ),
            Editable(
                "mis_lavadas",
                "Cuántas lavadas lleva",
                "Cuando un cliente pregunta cuántas lavadas lleva.",
                opcionales=frozenset({"nombre", "ultima"}),
                botones=(BOTONES["pedir"],),
            ),
            Editable(
                "sin_lavadas",
                "Todavía no tiene lavadas",
                "Cuando pregunta y todavía no ha lavado con ustedes.",
                opcionales=frozenset({"nombre"}),
                botones=(BOTONES["pedir"],),
            ),
            Editable(
                "mis_lavadas_sin_registro",
                "Lavadas de alguien que no es cliente",
                "Cuando pregunta alguien con un número que no está entre los clientes.",
                botones=(BOTONES["pedir"],),
            ),
        ),
    ),
    Grupo(
        "preguntas",
        "Preguntas y charla",
        (
            Editable(
                "charla",
                "Charla, si Gemini no responde",
                'Gemini escribe la respuesta a la charla ("estoy cansado", "te amo"); este '
                "texto sale solo si no responde o si lo que escribió rompía una regla.",
                botones=_MENU,
            ),
            Editable(
                "pregunta_al_administrador",
                "Pregunta que la ficha no sabe",
                "Cuando un cliente pregunta algo que no está en la ficha del negocio. La "
                "pregunta te llega en Comentarios.",
                opcionales=frozenset({"nombre"}),
                botones=_MENU,
            ),
            Editable(
                "pregunta_pasada",
                "Pregunta que la ficha no sabe, después de Gemini",
                "Con Gemini, después de que dice con sus palabras que eso no lo tiene "
                "confirmado. La pregunta te llega en Comentarios.",
                opcionales=frozenset({"nombre"}),
            ),
            Editable(
                "pregunta_en_el_camino",
                "Pregunta que la ficha no sabe, mientras pide turno",
                "Si pregunta algo que la ficha no sabe en medio de pedir un turno: se anota y "
                "el bot sigue con el turno.",
            ),
            Editable(
                "pregunta_sin_respuesta",
                "Pregunta que la ficha no sabe, sin ser cliente",
                "Igual, de alguien que nunca ha pedido turno: se le pide permiso para guardar "
                "su número.",
                botones=_PERMISO,
            ),
            Editable(
                "pregunta_anonima",
                "Si no da permiso para responder su pregunta",
                "Cuando toca No, gracias después de una pregunta que el bot no supo.",
            ),
            Editable(
                "ayuda_urgente",
                "Mensajes graves",
                "Cuando alguien escribe que quiere morir o hacerse daño. El bot no improvisa: "
                "manda este texto, sin botones. Revisa que los números sigan vigentes.",
            ),
        ),
    ),
)

EDITABLES: dict[str, Editable] = {
    editable.clave: editable for grupo in GRUPOS for editable in grupo.textos
}


# --- Revisar lo que escribió el administrador ------------------------------------------------


class TextoInvalido(ReglaDelNegocio):
    """El texto no se puede usar así; el mensaje dice por qué."""


def limpiar(escrito: str) -> str:
    """Sin espacios de más ni líneas vacías de sobra, como lo escribiría una persona."""
    texto = escrito.replace("\r\n", "\n").replace("\r", "\n")
    texto = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", texto)  # caracteres invisibles
    texto = "\n".join(" ".join(linea.split()) for linea in texto.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _lista(nombres: list[str]) -> str:
    llaves = [f"{{{nombre}}}" for nombre in nombres]
    return llaves[0] if len(llaves) == 1 else ", ".join(llaves[:-1]) + " y " + llaves[-1]


def validar(editable: Editable, escrito: str) -> str:
    """El texto listo para guardar. Si no sirve, lanza TextoInvalido con el porqué."""
    texto = limpiar(escrito)
    if not texto:
        raise TextoInvalido("Escribe el texto: el bot no puede mandar un mensaje vacío.")
    if len(texto) > editable.maximo:
        raise TextoInvalido(
            f"Puede tener hasta {editable.maximo} caracteres y este tiene {len(texto)}."
        )
    try:
        usados = huecos(texto)
    except ValueError as error:
        ejemplo = editable.datos[0] if editable.datos else "numero"
        raise TextoInvalido(
            "Revisa las llaves: entre una { y una } solo va el nombre de un dato, por "
            f"ejemplo {{{ejemplo}}}."
        ) from error
    desconocidos = [nombre for nombre in dict.fromkeys(usados) if nombre not in editable.datos]
    if desconocidos:
        if not editable.datos:
            raise TextoInvalido(f"Este mensaje no lleva datos: quita {_lista(desconocidos)}.")
        no_es = "no es un dato" if len(desconocidos) == 1 else "no son datos"
        raise TextoInvalido(
            f"{_lista(desconocidos)} {no_es} de este mensaje. Puedes usar "
            f"{_lista(list(editable.datos))}."
        )
    faltan = [n for n in editable.datos if n in editable.obligatorios and n not in usados]
    if faltan:
        que_es = "; ".join(f"{{{nombre}}} es {DATOS[nombre]}" for nombre in faltan)
        falta, lo = ("Falta", "lo") if len(faltan) == 1 else ("Faltan", "los")
        raise TextoInvalido(f"{falta} {_lista(faltan)}: el cliente {lo} necesita ({que_es}).")
    return texto


# --- Vista previa --------------------------------------------------------------------------


def ejemplos(parametros: Parametros) -> dict[str, object]:
    """Datos de ejemplo para ver cómo queda un texto: los del negocio son los de verdad."""
    p = parametros
    return {
        "negocio": p.nombre_negocio,
        "horario": formato.horario(p.dias_atencion, p.hora_apertura, p.hora_cierre),
        "apertura": f"mañana a {formato.hora_con_articulo(p.hora_apertura)}",
        "telefono": formato.telefono(p.telefono_contacto),
        "gracia": int(p.gracia.total_seconds() // 60),
        "avisar": p.avisar_cuando_falten,
        "nombre": "Ana",
        "vehiculo": "automóvil",
        "servicio": "Básico · $40.000",
        "fila": "carros",
        "como_va": TEXTOS["fila_libre"],
        "numero": "C-07",
        "numeros": "C-07",
        "turno": "C-07",
        "hora": "las 8:15 a.m.",
        "adelante": textos.adelante(2),
        "adelante_mayuscula": "Tienes 3 turnos adelante",
        "espera": "; entras en aprox. 45 min",
        "filas": "*Carros:* hay puesto libre, puedes venir ya\n"
        "*Motos:* 2 vehículos esperando; un turno nuevo entraría en aprox. 50 min",
        "servicios": "*Básico* · $40.000\n"
        "Lavado exterior a presión, champú, secado y brillo de llantas\n\n"
        "*Encerado* · $60.000\nBásico + cera protectora para la pintura",
        "lavadas": "7 lavadas",
        "ultima": "el sábado",
    }


def vista_previa(editable: Editable, escrito: str, parametros: Parametros) -> Mensaje:
    """Cómo le llega el mensaje al cliente, con datos de ejemplo. Si el texto no sirve,
    lanza TextoInvalido."""
    texto = validar(editable, escrito)
    # Los demás textos, como están guardados, y este como quedaría.
    con_este = Textos({**parametros.textos_bot, editable.clave: texto})
    datos = {**ejemplos(parametros), **editable.ejemplos}
    if editable.dentro_de:
        # Una parte del turno confirmado: se ve el mensaje entero, con esta parte adentro.
        datos["situacion"] = llenar(texto, datos)
        lleno = con_este(editable.dentro_de, **datos)
    else:
        if "situacion" in editable.datos:
            datos["situacion"] = con_este("confirmado_llamado", **datos)
        lleno = llenar(texto, datos)
    try:
        if editable.lista:
            servicios = (
                Opcion("vista.1", "Básico", "$40.000"),
                Opcion("vista.2", "Encerado", "$60.000"),
            )
            return Mensaje(lleno, lista=Lista(editable.lista, servicios))
        botones = tuple(Opcion(f"vista.{i}", titulo) for i, titulo in enumerate(editable.botones))
        return Mensaje(lleno, botones=botones)
    except ValueError as error:  # no cabe en un mensaje de WhatsApp
        raise TextoInvalido("Con sus datos, el mensaje queda muy largo para WhatsApp.") from error


# --- Guardar -------------------------------------------------------------------------------


def guardar(sesion: Session, clave: str, escrito: str, *, ahora: datetime | None = None) -> bool:
    """Guarda el texto cambiado. Devuelve si quedó distinto del original.

    Si queda igual al original, se borra el cambio: el bot vuelve a usar el original.
    """
    editable = EDITABLES[clave]
    texto = validar(editable, escrito)
    fila = sesion.get(Configuracion, PREFIJO_TEXTO_DEL_BOT + clave)
    if texto == editable.original:
        if fila is not None:
            sesion.delete(fila)
            sesion.flush()
        return False
    if fila is None:
        sesion.add(
            Configuracion(
                clave=PREFIJO_TEXTO_DEL_BOT + clave,
                valor=texto,
                descripcion=f"Texto del bot cambiado en Configuración: {editable.titulo}",
                actualizado_en=ahora or tiempo.ahora(),
            )
        )
    elif fila.valor != texto:
        fila.valor = texto
        fila.actualizado_en = ahora or tiempo.ahora()
    sesion.flush()
    return True
