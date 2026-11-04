"""La conversación del bot por WhatsApp: qué contesta a cada mensaje y qué hace en la fila.

Funciona con botones y con lo que el cliente escribe con sus palabras. Lo que escribe lo
lee Gemini (app.bot.interprete) en una sola consulta: dice qué quiere el cliente y, si
toca conversar, escribe la respuesta. Lo que el bot hace es lo mismo que si hubiera tocado
el botón: los precios, la fila y los turnos salen del sistema, nunca de Gemini.

Antes de preguntarle a Gemini, el bot resuelve solo lo que no deja dudas: un botón, una
orden corta ("mi turno", "cancelar", "ya llegué") y la respuesta clara a lo que preguntó
(un sí o un no, el nombre, la placa). Todo lo demás lo lee Gemini, en cualquier paso: una
pregunta en medio de pedir el turno se contesta y el bot sigue con lo que iba. Si Gemini
no está o no respondió, el bot sigue solo, con botones y unas palabras que reconoce, y no
le dice al cliente "no te entendí" cuando el que falló fue Gemini.

Las decisiones que no se deshacen no se adivinan: aceptar el aviso de datos, dar permiso
para guardar el número o cancelar un turno necesitan el botón o un sí claro. Un "no", en
cambio, siempre vale.

Las reglas no viven aquí: el bot llama a los mismos casos de uso que el dashboard (pedir
turno, cancelar, marcar llegada...), así que lo que pasa en el chat se ve al instante en
la fila y al revés.

Las quejas, las felicitaciones y los pedidos de hablar con una persona le llegan al
administrador en el dashboard (Comentarios, app.comentarios). De quien no es cliente no
se guarda el número sin su permiso. Mientras el administrador le responde a un cliente,
el bot no se mete en la conversación (app.bot.pausa), pero sí atiende lo de la fila.

`atender` recibe lo que el cliente escribió o tocó y devuelve los mensajes para
contestarle. No los envía ni confirma nada: quien lo llama (WhatsApp, o la página
"Probar el bot") hace el commit y después envía las respuestas. `atender_con_traza`
devuelve además cómo se decidió la respuesta: la ve el administrador en Probar el bot.

En qué va cada conversación se guarda en `sesion_bot`: el paso y lo que el cliente ha
respondido. Si contesta horas después, el bot sabe dónde iba; si pasó más de media hora
(se cambia en Configuración), vuelve a empezar.
"""

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import tiempo
from app.bot import pausa, textos
from app.bot.interprete import (
    CARRO,
    NO,
    OTRO_VEHICULO,
    SI,
    Anterior,
    Catalogo,
    Contexto,
    Intencion,
    Interpretacion,
    Interprete,
)
from app.bot.mensajes import Entrada, Lista, Mensaje, Opcion, recortar
from app.bot.redactor import ficha_completa, pide_ayuda_urgente, revisar_con_motivo
from app.bot.sesiones import PASO_INICIAL
from app.bot.tablas import PausaDelBot, SesionBot
from app.bot.textos import BOTONES, Textos
from app.catalogo.parametros import Parametros, leer_parametros
from app.catalogo.tablas import Fila, Servicio, ServicioPrecio, TipoVehiculo
from app.clientes import servicio as clientes
from app.clientes.errores import NombreInvalido, PlacaDeOtroCliente, PlacaInvalida
from app.clientes.reglas import (
    nombre_bonito,
    normalizar_telefono,
    placa_en_el_texto,
    validar_placa,
)
from app.clientes.tablas import Cliente, TipoConsulta, Vehiculo
from app.comentarios import servicio as comentarios
from app.comentarios.tablas import LARGO_MAXIMO, TipoComentario
from app.errores import ReglaDelNegocio
from app.formato import DIAS, cuando_fue, duracion, hora_con_articulo, horario, pesos
from app.formato import telefono as telefono_legible
from app.integraciones import historial
from app.turnos import casos_de_uso as casos
from app.turnos import consultas, reglas
from app.turnos.consultas import PosicionDelTurno, ResumenDeLaFila
from app.turnos.errores import (
    CambioNoPermitido,
    FueraDeHorario,
    ServicioNoDisponible,
    YaTieneTurnoActivo,
)
from app.turnos.estados import ESTADOS_ACTIVOS, NOMBRE_DEL_ESTADO, Actor, EstadoTurno, OrigenTurno
from app.turnos.tablas import Turno

E = EstadoTurno

logger = logging.getLogger(__name__)


class Paso(StrEnum):
    """En qué va la conversación: qué le preguntó el bot al cliente."""

    MENU = PASO_INICIAL
    AVISO_DATOS = "aviso_datos"
    NOMBRE = "nombre"
    VEHICULO = "vehiculo"
    PLACA = "placa"
    SERVICIO = "servicio"
    CONFIRMAR = "confirmar"
    SEGUNDO_TURNO = "segundo_turno"
    PRECIOS = "precios"
    CANCELAR = "cancelar"
    NUMERO_DE_TURNO = "numero_de_turno"
    # Después de una queja o de pedir hablar con alguien: lo que escriba se agrega a ese
    # comentario, para el administrador.
    DETALLE = "detalle"
    # A quien no es cliente se le pregunta si guardamos su número para responderle.
    PERMISO_CONTACTO = "permiso_contacto"


# En estos pasos el bot espera que el cliente escriba; en los demás, que toque un botón.
PASOS_DE_TEXTO = frozenset({Paso.NOMBRE, Paso.PLACA, Paso.NUMERO_DE_TURNO})
# Las preguntas que también se contestan escribiendo un sí o un no ("no gracias", "dale").
PASOS_DE_SI_O_NO = frozenset({Paso.AVISO_DATOS, Paso.CONFIRMAR, Paso.PERMISO_CONTACTO})
# Decisiones que no se deshacen: dar los datos, dar el número, cancelar un turno. Ahí un sí
# tiene que ser claro (el botón, o un sí que el bot reconoce solo): el sí que entienda
# Gemini no basta. Un no siempre vale.
PASOS_DE_PERMISO = frozenset({Paso.AVISO_DATOS, Paso.PERMISO_CONTACTO, Paso.CANCELAR})
# Los pasos de pedir un turno: pedir turno en medio de ellos no es cambiar de tema.
PASOS_DEL_TURNO = frozenset(
    {Paso.AVISO_DATOS, Paso.NOMBRE, Paso.VEHICULO, Paso.PLACA, Paso.SERVICIO, Paso.CONFIRMAR}
)

# La memoria del bot: cuántos mensajes de lo último que se dijeron lee Gemini, y de cuánto
# tiempo atrás (lo mismo que WhatsApp deja escribirle: 24 horas).
MEMORIA = 10
MEMORIA_HORAS = timedelta(hours=24)

# En estos el bot no le está preguntando nada: lo que escriba es conversación. Mientras el
# administrador le responde (app.bot.pausa), eso le llega a él y el bot no lo contesta. En
# los demás pasos el cliente va en algo con el bot (tocó "Pedir turno"...) y el bot sigue.
PASOS_DE_CHARLA = frozenset({Paso.MENU, Paso.DETALLE})

# El prefijo del id de cada botón dice a qué paso pertenece: un botón de un mensaje
# viejo ya no sirve. Los del menú y los del turno ("En camino", "Ya estoy aquí") sirven
# en cualquier momento.
PASO_DEL_BOTON = {
    "datos": Paso.AVISO_DATOS,
    "vehiculo": Paso.VEHICULO,
    "placa": Paso.PLACA,
    "servicio": Paso.SERVICIO,
    "confirmar": Paso.CONFIRMAR,
    "segundo": Paso.SEGUNDO_TURNO,
    "precios": Paso.PRECIOS,
    "cancelar": Paso.CANCELAR,
    "responder": Paso.PERMISO_CONTACTO,
}

# Lo que el bot está esperando en cada paso, como se lo cuenta a Gemini (EN QUÉ VA): así
# entiende una respuesta corta ("sí", "la moto") y no repite la pregunta del bot.
QUE_ESPERA: dict[Paso, str] = {
    Paso.AVISO_DATOS: "Está pidiendo turno. El bot le mostró el aviso de datos personales y le "
    "preguntó si acepta (pregunta de sí o no).",
    Paso.NOMBRE: "Está pidiendo turno. El bot le preguntó a nombre de quién lo hace.",
    Paso.VEHICULO: "Está pidiendo turno. El bot le preguntó qué vehículo trae.",
    Paso.PLACA: "Está pidiendo turno. El bot le preguntó la placa del vehículo (la pide por "
    "seguridad; si no la tiene a mano, la da en el local).",
    Paso.SERVICIO: "Está pidiendo turno. El bot le preguntó qué servicio quiere; la lista trae "
    "los precios.",
    Paso.CONFIRMAR: "Está pidiendo turno. El bot le mostró el resumen y le preguntó si lo "
    "confirma (pregunta de sí o no; también puede cambiar los datos).",
    Paso.SEGUNDO_TURNO: "Quiso pedir otro turno, pero ya tiene uno en esa fila. El bot le "
    "preguntó si lo mantiene o lo cancela.",
    Paso.PRECIOS: "El bot le preguntó para qué vehículo quiere ver los precios.",
    Paso.CANCELAR: "El bot le preguntó si de verdad cancela su turno (pregunta de sí o no).",
    Paso.NUMERO_DE_TURNO: "El bot le preguntó su número de turno, el que le dieron en el local "
    "(por ejemplo, C-07).",
    Paso.PERMISO_CONTACTO: "El bot le preguntó si quiere que el administrador le responda; para "
    "eso se guarda su número de WhatsApp, solo para responderle (pregunta de sí o no).",
}

# Lo que el cliente pide con sus palabras mientras cuenta lo que pasó, y el bot también
# hace (después de anotarlo): ver su turno, la fila, los precios, cancelar...
PIDE_ALGO_MAS = frozenset(
    {
        Intencion.PEDIR_TURNO,
        Intencion.VER_FILA,
        Intencion.PRECIOS,
        Intencion.MI_TURNO,
        Intencion.CANCELAR,
        Intencion.PICO_Y_PLACA,
        Intencion.MIS_LAVADAS,
    }
)

# --- Palabras que el bot reconoce ------------------------------------------------------------

# Lo que dice el código QR del mostrador: "Hola, ya estoy en la fila".
FRASE_DEL_QR = "ya estoy en la fila"
LLEGADA = ("ya estoy aqui", "ya estoy aca", "ya llegue", "ya llegamos")
LLEGADA_EXACTA = frozenset({"llegue", "estoy aqui", "aqui estoy", "ya estoy"})
EN_CAMINO = ("en camino", "voy para alla", "voy saliendo")
EN_CAMINO_EXACTO = frozenset({"ya voy", "voy"})
DESPEDIDAS = frozenset({"gracias", "chao", "chau", "adios", "bye"})
SALUDOS = frozenset({"hola", "buenas", "buenos", "buen", "menu", "inicio", "ola"})
# Un mensaje hecho solo de estas palabras es un saludo: no hace falta preguntarle a Gemini.
SOLO_SALUDO = SALUDOS | {"dias", "tardes", "noches", "que", "tal", "como", "esta", "estas"}
SOLO_SALUDO |= {"holi", "hey", "saludos", "vecino", "amigo", "senor", "senora", "don", "dona"}
# Un sí o un no claros, aunque vengan con cortesías ("sí porfa", "de una", "no gracias").
FRASES_DE_SI = (
    "de acuerdo",
    "de una",
    "por supuesto",
    "esta bien",
    "me parece bien",
    "claro que si",
    "asi es",
    "con gusto",
)
PALABRAS_DE_SI = frozenset(
    {"si", "sii", "siii", "sip", "dale", "ok", "okey", "okay", "listo", "claro", "acepto"}
    | {"aceptar", "confirmo", "confirmar", "confirmado", "bueno", "va", "vale", "perfecto"}
    | {"obvio", "correcto", "exacto", "hagale", "hagamosle", "autorizo"}
)
FRASES_DE_NO = (
    "no gracias",
    "mejor no",
    "ahora no",
    "todavia no",
    "por ahora no",
    "para nada",
    "no acepto",
    "no quiero",
    "no autorizo",
)
PALABRAS_DE_NO = frozenset({"no", "nop", "nel", "nunca", "negativo", "tampoco"})
# Lo que acompaña un sí o un no sin cambiarlo.
CORTESIAS = frozenset(
    {"por", "favor", "porfa", "porfis", "pls", "please", "gracias", "muchas", "mil", "pues"}
    | {"senor", "senora", "don", "dona", "hermano", "mano", "parce", "amigo", "amiga", "mijo"}
    | {"mija", "vecino", "vecina", "jaja", "jajaja", "ya", "y", "eso", "todo", "los", "las"}
    | {"el", "la", "terminos", "condiciones", "datos"}
)
PALABRAS_DE_CARRO = frozenset({"carro", "carros", "auto", "autos"})
# "quiero pedir mi turno" no es ver el turno que ya tiene: es pedir uno.
PEDIR = frozenset({"pedir", "sacar", "separar", "apartar", "reservar", "agendar", "separa"})

# Pedir hablar con una persona y no con el bot ("quiero hablar con un asesor"). Se
# reconoce aunque Gemini no esté: es la salida que el cliente siempre debe tener.
SIEMPRE_ES_UNA_PERSONA = frozenset({"asesor", "asesora", "humano", "humana"})
PERSONAS = frozenset(
    {
        "persona",
        "alguien",
        "administrador",
        "administradora",
        "encargado",
        "encargada",
        "dueno",
        "duena",
        "gerente",
        "jefe",
    }
)
HABLAR = frozenset({"hablar", "hablo", "comunicar", "comunicarme", "comunicarse", "atienda"})
MIS_LAVADAS = ("mis lavadas", "cuantas lavadas", "mis lavados", "cuantos lavados")
# Si Gemini no está o no respondió, estas palabras bastan para saber que es una queja: a
# quien se queja no se le contesta "no te entendí".
QUEJAS = (
    "queja",
    "quejo",
    "reclamo",
    "pesimo",
    "pesima",
    "mal servicio",
    "atendieron mal",
    "atienden mal",
    "mal atendid",
    "mal lavad",
    "quedo mal",
    "quedaron mal",
    "quedo sucio",
    "quedo sucia",
    "groser",
    "me rayaron",
    "me danaron",
    "no me gusto",
)
# Lo que se escribe para cerrar, sin contar nada más: "ok", "gracias", "eso es todo".
SOLO_PARA_CERRAR = DESPEDIDAS | {"ok", "okey", "listo", "dale", "bueno", "vale", "bien"}
SOLO_PARA_CERRAR |= {"si", "no", "nada", "ya", "eso", "es", "todo", "muchas", "mil", "perfecto"}


def normalizar(texto_escrito: str) -> str:
    """ "¡Ya llegué!" -> "ya llegue": minúsculas, sin tildes ni signos."""
    descompuesto = unicodedata.normalize("NFKD", texto_escrito.lower())
    sin_tildes = "".join(letra for letra in descompuesto if not unicodedata.combining(letra))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", sin_tildes).split())


NEGACIONES = frozenset({"no", "nunca", "tampoco", "ni"})


def _dice(normal: str, frases: tuple[str, ...]) -> bool:
    """Si dice una de las frases sin negarla justo antes: "voy para allá" sí, pero "ya no
    voy para allá" no (eso es que no viene, y lo entiende Gemini)."""
    for frase in frases:
        inicio = normal.find(frase)
        while inicio != -1:
            if not NEGACIONES & set(normal[:inicio].split()[-2:]):
                return True
            inicio = normal.find(frase, inicio + 1)
    return False


def _avisa_que_llego(normal: str) -> bool:
    return normal in LLEGADA_EXACTA or _dice(normal, LLEGADA)


def _avisa_que_va_en_camino(normal: str) -> bool:
    return normal in EN_CAMINO_EXACTO or _dice(normal, EN_CAMINO)


def _pide_hablar_con_alguien(normal: str) -> bool:
    """ "quiero hablar con alguien", "que me atienda una persona", "un asesor por favor"."""
    palabras = set(normal.split())
    if palabras & SIEMPRE_ES_UNA_PERSONA:
        return True
    return bool(palabras & HABLAR) and bool(palabras & PERSONAS)


def _pregunta_sus_lavadas(normal: str) -> bool:
    return any(frase in normal for frase in MIS_LAVADAS)


def _solo_para_cerrar(normal: str) -> bool:
    palabras = normal.split()
    return 0 < len(palabras) <= 4 and set(palabras) <= SOLO_PARA_CERRAR


def elegir_por_texto(escrito: str, pregunta: Mensaje) -> str | None:
    """Si el cliente escribió el nombre de una de las opciones en vez de tocarla."""
    for opcion in pregunta.opciones:
        if normalizar(opcion.titulo) == escrito:
            return opcion.id
    return None


def si_o_no(normal: str) -> str | None:
    """SI o NO si el mensaje es solo eso ("sí porfa", "de una", "no gracias", "dale
    pues"); None si dice algo más: eso lo lee Gemini. `normal`, como sale de normalizar."""
    texto = f" {normal} "
    for frase in FRASES_DE_NO:
        texto = texto.replace(f" {frase} ", " no ")
    for frase in FRASES_DE_SI:
        texto = texto.replace(f" {frase} ", " si ")
    palabras = {palabra for palabra in texto.split() if palabra not in CORTESIAS}
    if palabras and palabras <= PALABRAS_DE_SI:
        return SI
    if palabras and palabras <= PALABRAS_DE_NO:
        return NO
    return None


# Lo que no cuenta para saber si un mensaje es una orden corta.
_RELLENO = frozenset(
    {"por", "favor", "porfa", "porfis", "gracias", "hola", "buenas", "buenos", "dias"}
    | {"tardes", "noches", "oiga", "oye", "senor", "senora"}
)


def _es_corto(normal: str) -> bool:
    """Una orden ("mi turno", "ya llegué, gracias"), no una frase que dice más cosas: esa
    la lee Gemini."""
    palabras = [palabra for palabra in normal.split() if palabra not in _RELLENO]
    return 0 < len(palabras) <= 4


_CANCELAR = re.compile(
    r"(?:(?:quiero|necesito|deseo|quisiera|voy a|vamos a) )?"
    r"(?:cancelar|cancela|cancelame|cancelen|cancelalo|cancelamelo|anular|anula)"
    r"(?: (?:el|mi|ese|este|los|mis))?(?: turnos?)?"
)


def _pide_cancelar(normal: str) -> bool:
    """ "cancelar", "quiero cancelar mi turno". No: "¿puedo cancelar con Nequi?" (en
    Colombia, cancelar también es pagar): eso lo lee Gemini."""
    palabras = " ".join(palabra for palabra in normal.split() if palabra not in _RELLENO)
    return _CANCELAR.fullmatch(palabras) is not None


# Así empieza una pregunta, no un nombre.
PALABRAS_DE_PREGUNTA = frozenset(
    {"que", "porque", "como", "cuanto", "cuantos", "cuanta", "cuantas", "donde", "cuando"}
    | {"quien", "cual", "cuales", "puedo", "pueden", "tienen", "hay"}
)
# "me llamo Ana", "soy Ana Ruiz", "a nombre de Ana": el nombre es lo que sigue.
_ANTES_DEL_NOMBRE = re.compile(
    r"^(?:(?:hola|buenas|buenos d[ií]as|buenas tardes|buenas noches)\W+)?"
    r"(?:me llamo|mi nombre es|el nombre es|soy|a nombre de|es para)\s+",
    re.IGNORECASE,
)


def _el_nombre(escrito: str, normal: str) -> str | None:
    """El nombre que escribió ("Ana Ruiz", "me llamo Ana"), o None si no parece un nombre
    (una pregunta, un saludo, un sí o un no): eso lo lee Gemini."""
    palabras = normal.split()
    if not palabras or "?" in escrito or "¿" in escrito or any(c.isdigit() for c in escrito):
        return None
    if palabras[0] in PALABRAS_DE_PREGUNTA or normal.startswith(("para que", "por que")):
        return None
    if palabras[0] in PALABRAS_DE_NO or si_o_no(normal) is not None:
        return None
    if set(palabras) <= SOLO_SALUDO | SOLO_PARA_CERRAR:
        return None
    nombre = _ANTES_DEL_NOMBRE.sub("", escrito).strip(" .,!¡")
    if not nombre or len(nombre.split()) > 5:
        return None
    return nombre


# "no la tengo", "después se la doy": la placa queda pendiente, como al tocar "Ahora no".
_SIN_PLACA = (
    "no la tengo",
    "no la se",
    "no me la se",
    "no me acuerdo",
    "no se",
    "despues",
    "luego",
    "mas tarde",
    "en el local",
    "ahorita no",
)


def _no_tiene_la_placa(escrito: str, normal: str) -> bool:
    if si_o_no(normal) == NO:
        return True
    corto = len(normal.split()) <= 5 and "?" not in escrito
    return corto and any(frase in normal for frase in _SIN_PLACA)


def _la_placa(escrito: str) -> str | None:
    """La placa que escribió ("ABC123", "es la abc 12d"), o lo que parece un intento de
    placa (corto y con números): validar_placa dice después si está bien escrita."""
    placa = placa_en_el_texto(escrito)
    if placa is not None:
        return placa
    compacto = re.sub(r"[\s.\-]", "", escrito)
    if compacto.isalnum() and len(compacto) <= 8 and any(c.isdigit() for c in compacto):
        return escrito
    return None


# Un número de turno: "C-07", "c7", "M 12".
_NUMERO_DE_TURNO = re.compile(r"([a-z])\s*-?\s*0*(\d{1,3})")


def _parece_numero_de_turno(escrito: str) -> bool:
    return _NUMERO_DE_TURNO.fullmatch(normalizar(escrito).replace(" ", "")) is not None


def _quiere_cambiar(normal: str) -> bool:
    """En el resumen del turno: "cambiar", "quiero cambiar el servicio"."""
    cambia = any(palabra.startswith(("cambi", "corregi", "modific")) for palabra in normal.split())
    return cambia and _es_corto(normal)


def _lo_que_entendio(entendido: Interpretacion) -> str:
    """ "«precios» (Moto)", "«pregunta» (la ficha no lo dice)": para la traza."""
    vehiculo = {
        OTRO_VEHICULO: "un vehículo que el lavadero no tiene",
        CARRO: "carro, sin decir cuál",
    }.get(entendido.vehiculo or "", entendido.vehiculo)
    detalles = [dato for dato in (vehiculo, entendido.servicio) if dato]
    if entendido.si_o_no is not None:
        detalles.append("contesta que sí" if entendido.si_o_no == SI else "contesta que no")
    if entendido.intencion == Intencion.PREGUNTA and not entendido.sabe:
        detalles.append("la ficha no lo dice")
    nombre = f"«{entendido.intencion.value.replace('_', ' ')}»"
    return f"{nombre} ({', '.join(detalles)})" if detalles else nombre


def _con_y(cosas: list[str]) -> str:
    return cosas[0] if len(cosas) == 1 else ", ".join(cosas[:-1]) + " y " + cosas[-1]


# --- Atender un mensaje ------------------------------------------------------------------------


@dataclass
class Traza:
    """Cómo decidió el bot lo que contestó: si le preguntó a Gemini, qué entendió y, si
    salió un texto fijo, por qué. La ve el administrador en Probar el bot. Nunca lleva lo
    que escribió el cliente ni lo que contestó Gemini."""

    como: str = ""
    notas: list[str] = field(default_factory=list)

    def texto(self) -> str:
        return " ".join(parte for parte in (self.como, *self.notas) if parte)


def atender(
    sesion: Session,
    entrada: Entrada,
    *,
    ahora: datetime | None = None,
    interprete: Interprete | None = None,
) -> list[Mensaje]:
    """Lo que el bot contesta a un mensaje del cliente, y lo que hace en el sistema.

    `interprete` lee lo que el cliente escribe con sus palabras (Gemini): entiende qué
    quiere y escribe la conversación. Sin él, el bot entiende los botones y unas pocas
    palabras.
    """
    return atender_con_traza(sesion, entrada, ahora=ahora, interprete=interprete)[0]


def atender_con_traza(
    sesion: Session,
    entrada: Entrada,
    *,
    ahora: datetime | None = None,
    interprete: Interprete | None = None,
) -> tuple[list[Mensaje], Traza]:
    """Como `atender`, y además cómo se decidió la respuesta (Traza)."""
    atencion = _Atencion(sesion, entrada, ahora or tiempo.ahora(), interprete)
    respuesta = atencion.responder()
    atencion.guardar()
    return respuesta, atencion.traza


def catalogo_del_bot(sesion: Session) -> Catalogo:
    """Los vehículos y servicios de los que puede hablar el intérprete."""
    tipos = sesion.scalars(select(TipoVehiculo.nombre).order_by(TipoVehiculo.id))
    servicios = sesion.scalars(
        select(Servicio.nombre).where(Servicio.activo).order_by(Servicio.orden, Servicio.id)
    )
    return Catalogo(tuple(tipos), tuple(dict.fromkeys(servicios)))


def servicios_de_la_ficha(sesion: Session) -> tuple[str, ...]:
    """Para la ficha que lee Gemini: una línea por fila con sus servicios y lo que incluye
    cada uno, sin precios ("Automóvil y Camioneta: Básico (lavado por fuera); ...")."""
    lineas = []
    for fila in sesion.scalars(select(Fila).order_by(Fila.id)):
        tipos = list(
            sesion.scalars(
                select(TipoVehiculo.nombre)
                .where(TipoVehiculo.fila_id == fila.id)
                .order_by(TipoVehiculo.id)
            )
        )
        servicios = list(
            sesion.scalars(
                select(Servicio)
                .where(Servicio.fila_id == fila.id, Servicio.activo)
                .order_by(Servicio.orden, Servicio.id)
            )
        )
        if not tipos or not servicios:
            continue
        cada_uno = "; ".join(
            f"{servicio.nombre} ({recortar(' '.join(servicio.descripcion.split()), 100)})"
            if servicio.descripcion.strip()
            else servicio.nombre
            for servicio in servicios
        )
        lineas.append(f"{_con_y(tipos)}: {cada_uno}.")
    return tuple(lineas)


def _abrir_conversacion(sesion: Session, telefono: str, ahora: datetime) -> tuple[SesionBot, bool]:
    """La sesión del bot de ese teléfono, bloqueada mientras se atiende el mensaje.

    El bloqueo (FOR UPDATE) hace que dos mensajes del mismo cliente que llegan a la vez
    se atiendan uno detrás del otro. Devuelve también si la conversación empieza de cero
    (es nueva o venció).
    """
    sesion.execute(
        insert(SesionBot)
        .values(telefono=telefono, paso_actual=PASO_INICIAL, datos_temporales={}, expira_en=ahora)
        .on_conflict_do_nothing(index_elements=[SesionBot.telefono])
    )
    conversacion = sesion.scalars(
        select(SesionBot)
        .where(SesionBot.telefono == telefono)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    empieza = conversacion.expira_en <= ahora
    if empieza:
        conversacion.paso_actual = PASO_INICIAL
        conversacion.datos_temporales = {}
    return conversacion, empieza


def _pistas(entendido: Interpretacion, escrito: str) -> dict[str, str]:
    """Lo que el cliente ya dijo al pedir el turno con sus palabras: el vehículo y el
    servicio que entendió Gemini, y la placa que escribió (a Gemini le llega tapada)."""
    pistas = {
        "vehiculo": entendido.vehiculo,
        "servicio": entendido.servicio,
        "placa": placa_en_el_texto(escrito),
    }
    return {clave: valor for clave, valor in pistas.items() if valor}


class _Atencion:
    """Todo lo que hace falta para contestar un mensaje."""

    def __init__(
        self,
        sesion: Session,
        entrada: Entrada,
        ahora: datetime,
        interprete: Interprete | None,
    ) -> None:
        self.sesion = sesion
        self.entrada = entrada
        self.ahora = ahora
        self.interprete = interprete
        self.traza = Traza()
        self.parametros: Parametros = leer_parametros(sesion)
        # Con lo que el administrador cambió en Configuración > Textos del bot.
        self.texto = Textos(self.parametros.textos_bot)
        self.telefono = normalizar_telefono(entrada.telefono)
        self.conversacion, self.empieza = _abrir_conversacion(sesion, self.telefono, ahora)
        try:
            self.paso = Paso(self.conversacion.paso_actual)
        except ValueError:
            self.paso = Paso.MENU
        self.datos: dict[str, Any] = dict(self.conversacion.datos_temporales)
        self.cliente = clientes.buscar_por_telefono(sesion, self.telefono)
        if self.cliente is not None:
            self._vincular(self.cliente)
        self._lo_anterior: tuple[Anterior, ...] | None = None
        self._la_ficha: str | None = None
        self._el_catalogo: Catalogo | None = None
        # Lo que entendió Gemini de este mensaje: se le pregunta una sola vez.
        self._ya_le_pregunto = False
        self._lo_entendido: Interpretacion | None = None

    def guardar(self) -> None:
        self.conversacion.paso_actual = self.paso
        self.conversacion.datos_temporales = self.datos
        self.conversacion.expira_en = self.ahora + self.parametros.sesion_bot
        self.sesion.flush()

    def _vincular(self, cliente: Cliente) -> None:
        """La sesión queda a nombre del cliente: así se sabe que ese celular le escribe al bot."""
        if self.conversacion.cliente_id == cliente.id:
            return
        # Un cliente tiene una sola sesión: si quedó en otra (cambió de celular), se suelta.
        self.sesion.execute(
            update(SesionBot)
            .where(SesionBot.cliente_id == cliente.id, SesionBot.id != self.conversacion.id)
            .values(cliente_id=None)
            .execution_options(synchronize_session=False)
        )
        self.conversacion.cliente_id = cliente.id

    def _volver_al_menu(self) -> None:
        self.paso = Paso.MENU
        self.datos = {}

    # --- Qué le llegó --------------------------------------------------------------------

    def responder(self) -> list[Mensaje]:
        if self.entrada.opcion:
            self.traza.como = "Tocó un botón: el bot no le preguntó a Gemini."
            return self._boton(self.entrada.opcion)
        escrito = (self.entrada.texto or "").strip()
        normal = normalizar(escrito)
        pausa_vigente = pausa.pausa_de(self.sesion, self.telefono, self.ahora)
        if normal and pide_ayuda_urgente(normal):
            self.traza.como = "Mensaje grave: sale el texto fijo de ayuda, sin Gemini."
            if pausa_vigente is not None:
                self._avisar_a_quien_responde(pausa_vigente)  # que lo vea la persona
            return self._ayuda_urgente()  # antes que todo, en cualquier paso
        if pausa_vigente is not None and self.paso in PASOS_DE_CHARLA:
            self.traza.como = "Le está respondiendo una persona: el bot no se mete."
            return self._mientras_responde_una_persona(pausa_vigente, normal)
        if self.paso == Paso.DETALLE:
            return self._detalle(escrito, normal)
        if self.entrada.otro or not escrito:
            self.traza.como = "Mandó algo que no es texto (una foto, un audio...): no lo lee nadie."
            if self.paso == Paso.PERMISO_CONTACTO:
                return [_con_nota(self.texto("detalle_solo_texto"), self._pregunta())]
            return self._menu("solo_texto")
        if normal:
            orden = self._orden_corta(normal, escrito)
            if orden is not None:
                self.traza.como = "Una orden corta que el bot reconoce: no le preguntó a Gemini."
                return orden
            directa = self._respuesta_clara(normal, escrito)
            if directa is not None:
                self.traza.como = "Contestó claro lo que el bot preguntó: no le preguntó a Gemini."
                return directa
        entendido = self._entender(escrito)
        if entendido is not None:
            return self._con_lo_entendido(entendido, escrito)
        return self._sin_gemini(normal, escrito)

    def _orden_corta(self, normal: str, escrito: str) -> list[Mensaje] | None:
        """ "mi turno", "cancelar", "ya llegué", "un asesor": se hace sin preguntarle a
        Gemini. Un mensaje que dice más cosas sí lo lee él ("¿puedo cancelar con Nequi?" es
        pagar, no cancelar el turno; "gracias, ¿abren mañana?" no es una despedida)."""
        if FRASE_DEL_QR in normal:
            return self._llego_por_el_qr()
        if not _es_corto(normal):
            return None
        if _pide_cancelar(normal):
            return self._preguntar_si_cancela()
        if "mi turno" in normal and not PEDIR & set(normal.split()):
            return self._mi_turno()  # "quiero pedir mi turno" es pedir uno: lo lee Gemini
        if _avisa_que_llego(normal):
            return self._llegada()
        if _avisa_que_va_en_camino(normal):
            return self._en_camino()
        if _pide_hablar_con_alguien(normal):
            return self._hablar_con_una_persona(escrito)
        if _pregunta_sus_lavadas(normal):
            return self._mis_lavadas()
        return None

    def _respuesta_clara(self, normal: str, escrito: str) -> list[Mensaje] | None:
        """Contestó lo que el bot le preguntó sin dejar dudas: escribió una de las opciones,
        un sí o un no, su nombre, la placa o el número de turno. Para eso no hace falta
        Gemini (ni gastar una consulta)."""
        if self.paso == Paso.MENU:
            elegida = elegir_por_texto(normal, self._mensaje_del_menu("menu"))
            return self._boton(elegida) if elegida is not None else None
        pregunta = self._pregunta()
        elegida = elegir_por_texto(normal, pregunta)
        if elegida is not None:
            return self._boton(elegida)
        if self.paso == Paso.NOMBRE:
            nombre = _el_nombre(escrito, normal)
            return self._escribio_nombre(nombre) if nombre is not None else None
        if self.paso == Paso.PLACA:
            placa = _la_placa(escrito)
            if placa is not None:
                return self._escribio_placa(placa)
            return self._boton("placa.no") if _no_tiene_la_placa(escrito, normal) else None
        if self.paso == Paso.NUMERO_DE_TURNO:
            if _parece_numero_de_turno(escrito):
                return self._escribio_numero_de_turno(escrito)
            return None
        if self.paso == Paso.PRECIOS and normal in PALABRAS_DE_CARRO:
            return self._preguntar_cual_carro()
        if self.paso == Paso.CONFIRMAR and _quiere_cambiar(normal):
            return self._boton("confirmar.cambiar")
        respuesta = si_o_no(normal)
        if respuesta == NO and (self.paso == Paso.CANCELAR or self._es_de_si_o_no(pregunta)):
            return self._boton(pregunta.opciones[-1].id)
        if respuesta == SI and self._es_de_si_o_no(pregunta):
            return self._boton(pregunta.opciones[0].id)
        return None

    def _es_de_si_o_no(self, pregunta: Mensaje) -> bool:
        """Con dos turnos para cancelar, un sí no dice cuál: ahí toca usar los botones."""
        return self.paso in PASOS_DE_SI_O_NO or (
            self.paso == Paso.CANCELAR and len(pregunta.opciones) == 2
        )

    def _con_lo_entendido(self, entendido: Interpretacion, escrito: str) -> list[Mensaje]:
        """Lo que entendió Gemini. En el menú, se hace. En medio de una pregunta del bot:
        si la contestó, vale como el botón; si cambió de tema, se cambia; si preguntó algo o
        está conversando, se le contesta y el bot vuelve a hacer su pregunta."""
        if self.paso == Paso.MENU:
            return self._hacer(entendido, escrito)
        elegida = self._opcion_entendida(entendido)
        if elegida is not None:
            return self._boton(elegida)
        if self.paso == Paso.PRECIOS and entendido.vehiculo == CARRO:
            return self._preguntar_cual_carro()
        if entendido.si_o_no == SI and self.paso in PASOS_DE_PERMISO:
            # Un sí que no se deshace: con el botón (o un sí que el bot reconoce solo).
            self.traza.notas.append("Para esto, un sí escrito no basta: tiene que tocar el botón.")
            pregunta = self._pregunta()
            if len(pregunta.opciones) == 2:
                nota = self.texto("toca_el_si", opcion=pregunta.opciones[0].titulo)
            else:
                nota = self.texto("toca_una_opcion")
            return [_con_nota(nota, pregunta)]
        if self._cambia_de_tema(entendido):
            return self._hacer(entendido, escrito)
        match entendido.intencion:
            case Intencion.DESPEDIDA:
                return self._despedida(entendido)
            case Intencion.FELICITACION:
                return self._felicitacion(escrito, entendido)
            case Intencion.PREGUNTA | Intencion.CHARLA | Intencion.SALUDO | Intencion.OTRO:
                return self._con_la_ficha(entendido, escrito)
        # Pidió algo que no contesta la pregunta (otro turno, los precios mientras elige el
        # servicio, que ya trae los precios): se le vuelve a hacer.
        nota = self._de_gemini(entendido) or self._fijo("toca_una_opcion")
        return [_con_nota(nota, self._pregunta())]

    def _sin_gemini(self, normal: str, escrito: str) -> list[Mensaje]:
        """Gemini no está o no respondió: el bot sigue con lo que reconoce solo. Si Gemini
        está conectado y el que falló fue él, al cliente no se le dice "no te entendí": se
        le pide intentar de nuevo ("sin_gemini")."""
        if normal:
            palabra_clave = self._palabra_clave(normal, escrito)
            if palabra_clave is not None:
                self.traza.notas.append("Siguió con una palabra que el bot reconoce solo.")
                return palabra_clave
        if self.paso in PASOS_DE_TEXTO:
            if self.paso == Paso.NOMBRE:
                return [Mensaje(self._fijo("nombre_invalido"))]
            return self._lo_que_escribio(escrito)
        if self.paso == Paso.MENU:
            if _dice(normal, QUEJAS):
                # A quien se queja no se le contesta "no te entendí".
                return self._queja(escrito)
            palabras = normal.split()
            saluda = (
                self.empieza
                or bool(palabras)
                and (palabras[0] in SALUDOS or set(palabras) <= SOLO_SALUDO)
            )
            if saluda:
                return self._saludo()
            self._volver_al_menu()
            if self.interprete is None:
                return [_con_el_menu(self._fijo("no_entendi"))]
            telefono = self._telefono_del_negocio()
            return [_con_el_menu(self._fijo("sin_gemini", telefono=telefono))]
        if self.paso == Paso.PERMISO_CONTACTO:
            # Contó más de lo que pasó en vez de tocar un botón: se anota y se pregunta otra vez.
            self._anotar(escrito)
            return [_con_nota(self.texto("anotado"), self._pregunta())]
        return [_con_nota(self._fijo("toca_una_opcion"), self._pregunta())]

    def _palabra_clave(self, normal: str, escrito: str) -> list[Mensaje] | None:
        """Sin Gemini, unas palabras alcanzan: "cancel" en cualquier parte, "gracias"..."""
        palabras = normal.split()
        # Primero cancelar: "quiero cancelar mi turno" es cancelar, no preguntar cómo va.
        if "cancel" in normal:
            return self._preguntar_si_cancela()  # siempre pregunta antes, con botones
        if "mi turno" in normal:
            return self._mi_turno()
        if _avisa_que_llego(normal):
            return self._llegada()
        if _avisa_que_va_en_camino(normal):
            return self._en_camino()
        if _pide_hablar_con_alguien(normal):
            return self._hablar_con_una_persona(escrito)
        if _pregunta_sus_lavadas(normal):
            return self._mis_lavadas()
        if DESPEDIDAS & set(palabras) and len(palabras) <= 4:
            return self._despedida()
        return None

    # --- Lo que escribió con sus palabras (Gemini) ------------------------------------------

    def _antes(self) -> tuple[Anterior, ...]:
        """La memoria del bot: lo último que se dijeron con este celular (sin el mensaje que
        está contestando), para que Gemini entienda el nuevo y siga la conversación. Se lee
        una sola vez por mensaje, y solo si se le va a preguntar a Gemini."""
        if self._lo_anterior is None:
            lineas = historial.conversacion(
                self.sesion,
                self.telefono,
                self.parametros,
                cuantos=MEMORIA,
                desde=self.ahora - MEMORIA_HORAS,
                solo_lo_que_vio=True,
            )
            self._lo_anterior = tuple(
                Anterior(linea.quien.value, linea.dice, linea.opciones) for linea in lineas
            )
        return self._lo_anterior

    def _catalogo(self) -> Catalogo:
        if self._el_catalogo is None:
            self._el_catalogo = catalogo_del_bot(self.sesion)
        return self._el_catalogo

    def _entender(self, escrito: str) -> Interpretacion | None:
        """Lo que entendió Gemini del mensaje y, si toca conversar, lo que escribió. Se le
        pregunta una sola vez por mensaje. None si no hay Gemini o no respondió: el bot
        sigue sin él."""
        if self._ya_le_pregunto:
            return self._lo_entendido
        self._ya_le_pregunto = True
        if self.interprete is None:
            self.traza.como = "Gemini no está conectado (Configuración): el bot siguió solo."
            return None
        contexto = Contexto(
            catalogo=self._catalogo(),
            ficha=self._ficha(),
            antes=self._antes(),
            en_que_va=self._en_que_va(),
            guias=self._guias(),
        )
        inicio = time.perf_counter()
        try:
            entendido = self.interprete.interpretar(escrito, contexto)
            problema = getattr(self.interprete, "ultimo_problema", None) or "no respondió."
        except Exception:
            # Un error de Gemini nunca deja al cliente sin respuesta: el bot sigue sin él.
            logger.exception("Gemini falló; el bot siguió sin él.")
            entendido, problema = None, "falló por un error del programa (está en el registro)."
        segundos = f"{time.perf_counter() - inicio:.1f}".replace(".", ",")
        if entendido is None:
            self.traza.como = f"Gemini no respondió ({segundos} s): {problema}"
        else:
            self.traza.como = (
                f"Gemini respondió en {segundos} s: entendió {_lo_que_entendio(entendido)}."
            )
        self._lo_entendido = entendido
        return entendido

    def _en_que_va(self) -> str:
        """Lo que el bot está esperando, para que Gemini entienda una respuesta corta ("sí",
        "la moto") y no repita la pregunta, que el bot vuelve a hacer después."""
        if self.paso == Paso.MENU:
            return ""
        if self.paso == Paso.DETALLE:
            return (
                "Acaba de dejar una queja o de pedir hablar con alguien: lo que escriba ahora "
                "se le pasa al administrador. Si solo cuenta más de lo que pasó, tu respuesta "
                f"dice con tus palabras: «{self.texto('detalle_recibido')}»."
            )
        texto = QUE_ESPERA.get(self.paso, "")
        opciones = [opcion.titulo for opcion in self._pregunta().opciones]
        return f"{texto} Opciones: {' | '.join(opciones)}." if opciones else texto

    def _guias(self) -> tuple[tuple[str, str], ...]:
        """Lo que el bot diría con un texto fijo al saludar, despedirse o agradecer una
        felicitación: Gemini lo dice con sus palabras (CON_SUS_PALABRAS)."""
        if self.cliente is not None:
            nombre = _primer_nombre(self.cliente)
            saludo = self.texto("saludo_cliente", nombre=nombre)
            felicitacion = self.texto("felicitacion_cliente", nombre=nombre)
        else:
            negocio = self.parametros.nombre_negocio
            saludo = self.texto("saludo", negocio=negocio, horario=self._horario())
            felicitacion = self.texto("felicitacion")
        return (
            ("saludo", saludo),
            ("despedida", self.texto("despedida")),
            ("felicitacion", felicitacion),
        )

    def _permitido(self) -> str:
        """Lo que Gemini puede decir: la ficha y los textos que dice con sus palabras."""
        textos_fijos = [texto for _, texto in self._guias()]
        return "\n".join([self._ficha(), *textos_fijos, self.texto("detalle_recibido")])

    def _de_gemini(self, entendido: Interpretacion | None) -> str | None:
        """Lo que escribió Gemini, revisado con reglas fijas (app.bot.redactor). None si no
        escribió nada o si rompía una regla: ahí sale un texto fijo."""
        if entendido is None or not entendido.respuesta:
            return None
        revision = revisar_con_motivo(entendido.respuesta, self._permitido())
        if revision.texto is None:
            # Nunca se escribe lo que dijo: solo que no pasó la revisión.
            logger.warning("Lo que escribió Gemini rompía una regla; salió un texto fijo.")
            self.traza.notas.append(f"Lo que escribió no pasó la revisión: {revision.motivo}")
        return revision.texto

    def _fijo(self, nombre_del_texto: str, **datos: object) -> str:
        """Un texto fijo donde podía hablar Gemini: queda en la traza."""
        self.traza.notas.append(f"Salió el texto fijo «{nombre_del_texto}».")
        return self.texto(nombre_del_texto, **datos)

    def _hacer(self, entendido: Interpretacion, escrito: str) -> list[Mensaje]:
        """Lo que el cliente pidió con sus palabras, como si hubiera tocado el botón."""
        match entendido.intencion:
            case Intencion.PEDIR_TURNO if entendido.vehiculo == OTRO_VEHICULO:
                return self._vehiculo_que_no_lava(entendido)
            case Intencion.PEDIR_TURNO:
                return self._empezar_turno(_pistas(entendido, escrito))
            case Intencion.VER_FILA:
                return self._ver_la_fila()
            case Intencion.PRECIOS if entendido.vehiculo == OTRO_VEHICULO:
                return self._vehiculo_que_no_lava(entendido)
            case Intencion.PRECIOS:
                return self._precios_de(entendido.vehiculo)
            case Intencion.MI_TURNO:
                return self._mi_turno()
            case Intencion.CANCELAR:
                return self._preguntar_si_cancela()  # siempre pregunta antes, con botones
            case Intencion.LLEGUE:
                return self._llegada()
            case Intencion.EN_CAMINO:
                return self._en_camino()
            case Intencion.PICO_Y_PLACA:
                return self._menu("pico_y_placa")
            case Intencion.MIS_LAVADAS:
                return self._mis_lavadas()
            case Intencion.QUEJA:
                return self._queja(escrito)
            case Intencion.FELICITACION:
                return self._felicitacion(escrito, entendido)
            case Intencion.HABLAR_CON_PERSONA:
                return self._hablar_con_una_persona(escrito)
            case Intencion.SALUDO:
                return self._saludo(entendido)
            case Intencion.DESPEDIDA:
                return self._despedida(entendido)
            case Intencion.PREGUNTA | Intencion.CHARLA | Intencion.OTRO:
                return self._con_la_ficha(entendido, escrito)
            case Intencion.CRISIS:
                return self._ayuda_urgente()
        return self._menu("no_entendi")

    def _cambia_de_tema(self, entendido: Interpretacion) -> bool:
        """En medio de una pregunta, ¿pidió otra cosa? Preguntar el precio mientras pide
        turno no cuenta: la lista de servicios ya trae los precios."""
        otra_cosa = (
            Intencion.VER_FILA,
            Intencion.MI_TURNO,
            Intencion.CANCELAR,
            Intencion.LLEGUE,
            Intencion.EN_CAMINO,
            Intencion.PICO_Y_PLACA,
            Intencion.MIS_LAVADAS,
            Intencion.QUEJA,
            Intencion.HABLAR_CON_PERSONA,
            Intencion.CRISIS,
        )
        if entendido.intencion in otra_cosa:
            return True
        fuera_del_turno = self.paso not in PASOS_DEL_TURNO
        return fuera_del_turno and entendido.intencion in (Intencion.PEDIR_TURNO, Intencion.PRECIOS)

    def _opcion_entendida(self, entendido: Interpretacion) -> str | None:
        """El botón de la pregunta que corresponde a lo que entendió Gemini, si hay uno."""
        if self.paso == Paso.SERVICIO:
            if not entendido.servicio or entendido.intencion == Intencion.PRECIOS:
                return None  # preguntar cuánto vale uno no es elegirlo
            for servicio, _ in self._servicios(self._tipo()):
                if normalizar(servicio.nombre) == normalizar(entendido.servicio):
                    return f"servicio.{servicio.id}"
            return None
        if self.paso in (Paso.PRECIOS, Paso.VEHICULO):
            tipo = self._tipo_por_nombre(entendido.vehiculo)
            if tipo is None:
                return None
            if self.paso == Paso.PRECIOS:
                return f"precios.{tipo.id}"
            # Si ya tiene un vehículo de ese tipo, ese; si no, uno nuevo de ese tipo.
            suyos = [v for v, t in self._vehiculos_conocidos() if t.id == tipo.id]
            return f"vehiculo.{suyos[0].id}" if len(suyos) == 1 else f"vehiculo.tipo.{tipo.id}"
        if self.paso == Paso.PLACA:
            return "placa.no" if entendido.si_o_no == NO else None
        if entendido.si_o_no is None:
            return None
        pregunta = self._pregunta()
        if entendido.si_o_no == NO and (
            self.paso == Paso.CANCELAR or self._es_de_si_o_no(pregunta)
        ):
            return pregunta.opciones[-1].id  # un no siempre vale
        if self._es_de_si_o_no(pregunta) and self.paso not in PASOS_DE_PERMISO:
            return pregunta.opciones[0].id
        return None

    def _tipo_por_nombre(self, nombre: str | None) -> TipoVehiculo | None:
        if not nombre:
            return None
        for tipo in self.sesion.scalars(select(TipoVehiculo)):
            if normalizar(tipo.nombre) == normalizar(nombre):
                return tipo
        return None

    def _precios_de(self, vehiculo: str | None) -> list[Mensaje]:
        tipo = self._tipo_por_nombre(vehiculo)
        if tipo is None and vehiculo is None:
            # No dijo de cuál: si todos sus vehículos son de un tipo, los precios de ese.
            suyos = {suyo.id: suyo for _, suyo in self._vehiculos_conocidos()}
            if len(suyos) == 1:
                [tipo] = suyos.values()
        if tipo is not None:
            return self._eligio_precios(str(tipo.id))
        if vehiculo == CARRO:
            self._volver_al_menu()
            self.paso = Paso.PRECIOS
            return self._preguntar_cual_carro()
        return self._opcion_del_menu("precios")

    def _vehiculo_que_no_lava(self, entendido: Interpretacion) -> list[Mensaje]:
        """Nombró un vehículo que el lavadero no tiene ("¿lavan tractomulas?"): se le dice
        cuáles sí, sin precios ni turno para ese."""
        self._volver_al_menu()
        dicho = self._de_gemini(entendido)
        if dicho is not None:
            return [Mensaje(dicho)]
        vehiculos = _con_y([nombre.lower() for nombre in self._catalogo().vehiculos])
        return [
            Mensaje(
                self._fijo("vehiculo_que_no_lava", vehiculos=vehiculos),
                botones=(Opcion("menu.precios", BOTONES["precios"]),),
            )
        ]

    def _preguntar_cual_carro(self) -> list[Mensaje]:
        """Dijo "carro" para ver precios: ¿automóvil o camioneta? (cuestan distinto)."""
        self.datos["solo_carros"] = True
        return [self._pregunta()]

    def _boton(self, id_del_boton: str) -> list[Mensaje]:
        prefijo, _, resto = id_del_boton.partition(".")
        if prefijo == "menu":
            return self._opcion_del_menu(resto)
        if prefijo == "turno":
            return self._llegada() if resto == "llegue" else self._en_camino()
        if PASO_DEL_BOTON.get(prefijo) != self.paso:
            return self._menu("boton_vencido")
        manejadores = {
            "datos": self._acepta_los_datos,
            "vehiculo": self._eligio_vehiculo,
            "placa": lambda _: self._preguntar_servicio(),  # "Ahora no": placa pendiente
            "servicio": self._eligio_servicio,
            "confirmar": self._confirmar,
            "segundo": self._segundo_turno,
            "precios": self._eligio_precios,
            "cancelar": self._cancelar,
            "responder": self._permiso_para_responder,
        }
        if prefijo == "placa":
            self.datos["placa"] = None
        return manejadores[prefijo](resto)

    def _lo_que_escribio(self, escrito: str) -> list[Mensaje]:
        if self.paso == Paso.NOMBRE:
            return self._escribio_nombre(escrito)
        if self.paso == Paso.PLACA:
            return self._escribio_placa(escrito)
        return self._escribio_numero_de_turno(escrito)

    # --- Menú --------------------------------------------------------------------------------

    def _mensaje_del_menu(self, nombre_del_texto: str, **datos: object) -> Mensaje:
        return _con_el_menu(self.texto(nombre_del_texto, **datos))

    def _menu(self, nombre_del_texto: str) -> list[Mensaje]:
        self._volver_al_menu()
        return [self._mensaje_del_menu(nombre_del_texto)]

    def _saludo(self, entendido: Interpretacion | None = None) -> list[Mensaje]:
        """Con Gemini, con sus palabras: no saluda siempre igual y contesta el "¿cómo
        estás?". Siempre con el menú, que es la puerta de entrada."""
        self._volver_al_menu()
        dicho = self._de_gemini(entendido)
        if dicho is not None:
            return [_con_el_menu(dicho)]
        if self.cliente is not None:
            nombre = _primer_nombre(self.cliente)
            return [_con_el_menu(self._fijo("saludo_cliente", nombre=nombre))]
        negocio = self.parametros.nombre_negocio
        return [_con_el_menu(self._fijo("saludo", negocio=negocio, horario=self._horario()))]

    def _despedida(self, entendido: Interpretacion | None = None) -> list[Mensaje]:
        """Se despide o agradece: sin botones, que no es momento de ofrecer nada."""
        self._volver_al_menu()
        return [Mensaje(self._de_gemini(entendido) or self._fijo("despedida"))]

    def _opcion_del_menu(self, opcion: str) -> list[Mensaje]:
        if opcion == "pedir":
            return self._empezar_turno()
        if opcion == "fila":
            return self._ver_la_fila()
        self._volver_al_menu()
        self.paso = Paso.PRECIOS
        return [self._pregunta()]

    def _pregunta(self) -> Mensaje:
        """La pregunta del paso en que va la conversación (para repetirla si hace falta)."""
        preguntas = {
            Paso.AVISO_DATOS: self._pregunta_aviso_de_datos,
            Paso.NOMBRE: lambda: Mensaje(self.texto("pedir_nombre")),
            Paso.VEHICULO: self._pregunta_vehiculo,
            Paso.PLACA: self._pregunta_placa,
            Paso.SERVICIO: self._pregunta_servicio,
            Paso.CONFIRMAR: self._resumen,
            Paso.SEGUNDO_TURNO: self._pregunta_segundo_turno,
            Paso.PRECIOS: self._pregunta_precios,
            Paso.CANCELAR: self._pregunta_cancelar,
            Paso.NUMERO_DE_TURNO: lambda: Mensaje(self.texto("pedir_numero_de_turno")),
            Paso.PERMISO_CONTACTO: self._pregunta_permiso,
        }
        pregunta = preguntas.get(self.paso)
        return pregunta() if pregunta is not None else self._mensaje_del_menu("menu")

    # --- Horario ---------------------------------------------------------------------------

    def _horario(self) -> str:
        p = self.parametros
        return horario(p.dias_atencion, p.hora_apertura, p.hora_cierre)

    def _en_horario(self) -> bool:
        p = self.parametros
        return reglas.esta_en_horario(
            tiempo.hora_local(self.ahora), p.hora_apertura, p.hora_cierre, p.dias_atencion
        )

    def _proxima_apertura(self) -> str:
        """ "hoy a las 5:30 a.m.", "mañana a las 5:30 a.m." o "el lunes a las 5:30 a.m." """
        p = self.parametros
        local = tiempo.hora_local(self.ahora)
        a_las = hora_con_articulo(p.hora_apertura)
        for dias in range(8):
            dia = local + timedelta(days=dias)
            if dia.isoweekday() not in p.dias_atencion:
                continue
            if dias == 0 and local.time() >= p.hora_apertura:
                continue
            cuando = ("hoy", "mañana")[dias] if dias < 2 else f"el {DIAS[dia.weekday()]}"
            return f"{cuando} a {a_las}"
        return "pronto"

    def _no_hay_turnos(self) -> list[Mensaje] | None:
        """Si ahora no se dan turnos por WhatsApp, el mensaje que lo explica."""
        if not self.parametros.bot_activo:
            nombre, datos = "bot_apagado", {"telefono": self._telefono_del_negocio()}
        elif not self._en_horario():
            nombre = "fuera_de_horario"
            datos = {"horario": self._horario(), "apertura": self._proxima_apertura()}
        else:
            return None
        self._volver_al_menu()
        return [
            Mensaje(
                self.texto(nombre, **datos),
                botones=(
                    Opcion("menu.fila", BOTONES["fila"]),
                    Opcion("menu.precios", BOTONES["precios"]),
                ),
            )
        ]

    def _telefono_del_negocio(self) -> str:
        return telefono_legible(self.parametros.telefono_contacto)

    # --- Pedir turno: aviso de datos y nombre -----------------------------------------------

    def _empezar_turno(self, pistas: dict[str, str] | None = None) -> list[Mensaje]:
        """`pistas`: lo que ya dijo con sus palabras (el vehículo, el servicio, la placa).
        Sin ellas, las de los precios que acaba de ver. Lo que ya dijo no se le pregunta."""
        no_hay = self._no_hay_turnos()
        if no_hay is not None:
            return no_hay
        pistas = pistas or self.datos.get("pistas")
        self._volver_al_menu()
        if pistas:
            self.datos["pistas"] = dict(pistas)
        if self.cliente is None:
            # Cliente nuevo: sin aceptar el aviso de datos no se guarda nada.
            self.paso = Paso.AVISO_DATOS
            return [self._pregunta()]
        return self._preguntar_vehiculo()

    def _pregunta_aviso_de_datos(self) -> Mensaje:
        return Mensaje(
            self.parametros.aviso_datos,
            botones=(
                Opcion("datos.si", BOTONES["acepto"]),
                Opcion("datos.no", BOTONES["no_acepto"]),
            ),
        )

    def _acepta_los_datos(self, respuesta: str) -> list[Mensaje]:
        if respuesta != "si":
            self._volver_al_menu()
            return [
                Mensaje(
                    self.texto("datos_no"),
                    botones=(
                        Opcion("menu.fila", BOTONES["fila"]),
                        Opcion("menu.precios", BOTONES["precios"]),
                    ),
                )
            ]
        # Se anota cuándo aceptó; el cliente se crea al confirmar el turno.
        self.datos["acepto_datos_en"] = self.ahora.isoformat()
        self.paso = Paso.NOMBRE
        return [self._pregunta()]

    def _escribio_nombre(self, escrito: str) -> list[Mensaje]:
        try:
            self.datos["nombre"] = nombre_bonito(escrito)
        except NombreInvalido:
            return [Mensaje(self.texto("nombre_invalido"))]
        return self._preguntar_vehiculo()

    # --- Pedir turno: vehículo y placa --------------------------------------------------------

    def _pista(self, cual: str, *, usar: bool = True) -> str | None:
        """Lo que el cliente ya dijo con sus palabras para este turno ("vehiculo",
        "servicio", "placa") o los precios que vio ("precios_de"). Cada pista se usa una
        sola vez: si después cambia algo en el resumen, ya lo elige él."""
        pistas = self.datos.get("pistas") or {}
        valor = pistas.get(cual)
        if usar and valor is not None:
            # Un diccionario nuevo, sin tocar el que se leyó de la base.
            resto = {clave: dato for clave, dato in pistas.items() if clave != cual}
            if resto:
                self.datos["pistas"] = resto
            else:
                self.datos.pop("pistas", None)
        return valor

    def _vehiculo_dicho(
        self, conocidos: list[tuple[Vehiculo, TipoVehiculo]]
    ) -> list[Mensaje] | None:
        """Si ya dijo cuál ("la moto", "la ABC12D", o vio los precios de moto), no se le
        pregunta. None: no lo dijo, o no alcanza para saber cuál (dos motos, o "carro" con
        dos carros): ahí se pregunta."""
        placa = self._pista("placa", usar=False)
        for vehiculo, tipo in conocidos:
            if placa and vehiculo.placa == placa:
                self._pista("placa")
                self._pista("vehiculo")
                return self._usar_vehiculo(vehiculo, tipo)
        dijo = self._pista("vehiculo")
        nombre = dijo or self._pista("precios_de")
        if not nombre:
            return None
        tipo = self._tipo_por_nombre(nombre)  # None si dijo "carro" sin decir cuál
        if tipo is None and nombre != CARRO:
            return None
        candidatos = [
            (vehiculo, suyo)
            for vehiculo, suyo in conocidos
            if (suyo.id == tipo.id if tipo is not None else suyo.nombre != "Moto")
        ]
        if len(candidatos) == 1:
            return self._usar_vehiculo(*candidatos[0])
        # Uno nuevo de ese tipo si lo dijo, o si vio esos precios y no tiene vehículos.
        if not candidatos and tipo is not None and (dijo or not conocidos):
            return self._eligio_vehiculo(f"tipo.{tipo.id}")
        return None

    def _placa_dicha(self, tipo: TipoVehiculo) -> str | None:
        """La placa que ya escribió ("quiero lavar la moto ABC12D"), si es de ese tipo de
        vehículo. Si no tiene el formato, se le pregunta como siempre, sin regañarlo."""
        placa = self._pista("placa")
        if not placa:
            return None
        try:
            return validar_placa(placa, tipo.patron_placa, tipo.nombre)
        except PlacaInvalida:
            return None

    def _vehiculos_conocidos(self) -> list[tuple[Vehiculo, TipoVehiculo]]:
        if self.cliente is None:
            return []
        consulta = (
            select(Vehiculo, TipoVehiculo)
            .join(TipoVehiculo, TipoVehiculo.id == Vehiculo.tipo_vehiculo_id)
            .where(Vehiculo.cliente_id == self.cliente.id)
            .order_by(Vehiculo.placa.is_(None), Vehiculo.id)
        )
        return [(vehiculo, tipo) for vehiculo, tipo in self.sesion.execute(consulta)]

    def _preguntar_vehiculo(self, *, aunque_lo_conozca: bool = False) -> list[Mensaje]:
        conocidos = self._vehiculos_conocidos()
        if aunque_lo_conozca:
            self.datos.pop("pistas", None)  # quiere cambiarlo: ahora lo elige él
        else:
            nombro_otro = self._pista("vehiculo", usar=False) is not None
            dicho = self._vehiculo_dicho(conocidos)
            if dicho is not None:
                return dicho
            if len(conocidos) == 1 and not nombro_otro:
                # Si ya se conoce el vehículo, no se pregunta. Se puede cambiar en el resumen.
                # Si nombró otro ("el carro", y el suyo es una moto), se le pregunta.
                return self._usar_vehiculo(*conocidos[0])
        self.paso = Paso.VEHICULO
        self.datos["otro_vehiculo"] = not conocidos
        return [self._pregunta()]

    def _pregunta_vehiculo(self) -> Mensaje:
        conocidos = self._vehiculos_conocidos()
        if conocidos and not self.datos.get("otro_vehiculo"):
            botones = [
                Opcion(f"vehiculo.{vehiculo.id}", _nombre_del_vehiculo(tipo.nombre, vehiculo.placa))
                for vehiculo, tipo in conocidos[:2]
            ]
            botones.append(Opcion("vehiculo.otro", BOTONES["otro_vehiculo"]))
            return Mensaje(self.texto("pedir_vehiculo_conocido"), botones=tuple(botones))
        tipos = list(self.sesion.scalars(select(TipoVehiculo).order_by(TipoVehiculo.id)))
        opciones = tuple(Opcion(f"vehiculo.tipo.{tipo.id}", tipo.nombre) for tipo in tipos)
        if len(opciones) <= 3:
            return Mensaje(self.texto("pedir_vehiculo"), botones=opciones)
        return Mensaje(self.texto("pedir_vehiculo"), lista=Lista("Ver vehículos", opciones[:10]))

    def _eligio_vehiculo(self, eleccion: str) -> list[Mensaje]:
        if eleccion == "otro":
            self.datos["otro_vehiculo"] = True
            return [self._pregunta()]
        if eleccion.startswith("tipo."):
            tipo = self.sesion.get(TipoVehiculo, _entero(eleccion.removeprefix("tipo.")))
            if tipo is None:
                return [_con_nota(self.texto("toca_una_opcion"), self._pregunta())]
            for clave in ("vehiculo_id", "placa"):
                self.datos.pop(clave, None)
            self.datos["tipo_vehiculo_id"] = tipo.id
            return self._seguir_con(tipo, placa_conocida=None)
        vehiculo = self.sesion.get(Vehiculo, _entero(eleccion))
        if vehiculo is None or self.cliente is None or vehiculo.cliente_id != self.cliente.id:
            return self._menu("boton_vencido")
        return self._usar_vehiculo(
            vehiculo, self.sesion.get(TipoVehiculo, vehiculo.tipo_vehiculo_id)
        )

    def _usar_vehiculo(self, vehiculo: Vehiculo, tipo: TipoVehiculo) -> list[Mensaje]:
        self.datos.pop("placa", None)
        self.datos["vehiculo_id"] = vehiculo.id
        self.datos["tipo_vehiculo_id"] = tipo.id
        return self._seguir_con(tipo, placa_conocida=vehiculo.placa)

    def _seguir_con(self, tipo: TipoVehiculo, *, placa_conocida: str | None) -> list[Mensaje]:
        """Ya se sabe el vehículo, y con él la fila: un turno por teléfono en cada fila."""
        activos = self._activos(fila_id=tipo.fila_id)
        if len(activos) >= self.parametros.turnos_activos_por_telefono:
            self.paso = Paso.SEGUNDO_TURNO
            self.datos = {"turno_id": activos[0].turno_id}
            return [self._pregunta()]
        if placa_conocida is None:
            self.paso = Paso.PLACA
            dicha = self._placa_dicha(tipo)
            if dicha is not None:
                return self._escribio_placa(dicha)  # como si la hubiera escrito ahora
            return [self._pregunta()]
        return self._preguntar_servicio()

    def _tipo(self) -> TipoVehiculo:
        return self.sesion.get(TipoVehiculo, self.datos["tipo_vehiculo_id"])

    def _pregunta_placa(self) -> Mensaje:
        return Mensaje(
            self.texto("pedir_placa", vehiculo=self._tipo().nombre.lower()),
            botones=(Opcion("placa.no", BOTONES["ahora_no"]),),
        )

    def _escribio_placa(self, escrito: str) -> list[Mensaje]:
        tipo = self._tipo()
        ahora_no = (Opcion("placa.no", BOTONES["ahora_no"]),)
        try:
            placa = validar_placa(escrito, tipo.patron_placa, tipo.nombre)
        except PlacaInvalida as error:
            ejemplo = "ABC12D" if tipo.nombre == "Moto" else "ABC123"
            return [
                Mensaje(
                    self.texto("placa_invalida", error=error, ejemplo=ejemplo), botones=ahora_no
                )
            ]
        duenio = self.sesion.scalars(select(Vehiculo).where(Vehiculo.placa == placa)).one_or_none()
        if duenio is not None:
            if self.cliente is None or duenio.cliente_id != self.cliente.id:
                return [Mensaje(self.texto("placa_de_otro", placa=placa), botones=ahora_no)]
            # Es un vehículo que el cliente ya tenía registrado: se usa ese.
            return self._usar_vehiculo(
                duenio, self.sesion.get(TipoVehiculo, duenio.tipo_vehiculo_id)
            )
        self.datos["placa"] = placa
        return self._preguntar_servicio()

    # --- Pedir turno: servicio, resumen y confirmación ------------------------------------------

    def _servicios(self, tipo: TipoVehiculo) -> list[tuple[Servicio, int]]:
        consulta = (
            select(Servicio, ServicioPrecio.precio)
            .join(ServicioPrecio, ServicioPrecio.servicio_id == Servicio.id)
            .where(
                Servicio.activo,
                Servicio.fila_id == tipo.fila_id,
                ServicioPrecio.tipo_vehiculo_id == tipo.id,
                ServicioPrecio.activo,
            )
            .order_by(Servicio.orden, Servicio.id)
        )
        return [(servicio, precio) for servicio, precio in self.sesion.execute(consulta)]

    def _preguntar_servicio(self) -> list[Mensaje]:
        self.paso = Paso.SERVICIO
        servicio = self._servicio_dicho()
        if servicio is not None:
            return self._eligio_servicio(str(servicio.id))  # como si lo hubiera tocado
        return [self._pregunta()]

    def _servicio_dicho(self) -> Servicio | None:
        """El servicio que ya nombró ("quiero un general para la camioneta"), si existe para
        su vehículo."""
        nombre = self._pista("servicio")
        if not nombre:
            return None
        for servicio, _ in self._servicios(self._tipo()):
            if normalizar(servicio.nombre) == normalizar(nombre):
                return servicio
        return None

    def _pregunta_servicio(self) -> Mensaje:
        tipo = self._tipo()
        opciones = [
            Opcion(
                f"servicio.{servicio.id}",
                recortar(servicio.nombre, 24),
                recortar(f"{pesos(precio)} · {servicio.descripcion}", 72),
            )
            for servicio, precio in self._servicios(tipo)[:9]
        ]
        opciones.append(
            Opcion("servicio.local", BOTONES["en_el_local"], BOTONES["en_el_local_descripcion"])
        )
        return Mensaje(
            self.texto("pedir_servicio", vehiculo=tipo.nombre.lower()),
            lista=Lista(BOTONES["ver_servicios"], tuple(opciones)),
        )

    def _eligio_servicio(self, eleccion: str) -> list[Mensaje]:
        if eleccion == "local":
            self.datos["servicio_id"] = None
        else:
            disponibles = {servicio.id for servicio, _ in self._servicios(self._tipo())}
            servicio_id = _entero(eleccion)
            if servicio_id not in disponibles:
                return [_con_nota(self.texto("servicio_no_disponible"), self._pregunta())]
            self.datos["servicio_id"] = servicio_id
        self.paso = Paso.CONFIRMAR
        return [self._pregunta()]

    def _resumen(self) -> Mensaje:
        tipo = self._tipo()
        placa = self.datos.get("placa")
        if placa is None and self.datos.get("vehiculo_id"):
            placa = self.sesion.get(Vehiculo, self.datos["vehiculo_id"]).placa
        servicio_id = self.datos.get("servicio_id")
        if servicio_id is None:
            servicio = "lo decides en el local"
        else:
            precio = dict((s.id, p) for s, p in self._servicios(tipo)).get(servicio_id)
            nombre = self.sesion.get(Servicio, servicio_id).nombre
            servicio = f"{nombre} · {pesos(precio)}" if precio is not None else nombre
        fila = self.sesion.get(Fila, tipo.fila_id)
        return Mensaje(
            self.texto(
                "resumen",
                nombre=self.cliente.nombre if self.cliente else self.datos.get("nombre", ""),
                vehiculo=_nombre_del_vehiculo(tipo.nombre, placa, pendiente=", placa pendiente"),
                servicio=servicio,
                fila=fila.nombre.lower(),
                como_va=self._como_va(
                    consultas.resumen_de_la_fila(self.sesion, fila.id, ahora=self.ahora)
                ),
            ),
            botones=(
                Opcion("confirmar.si", BOTONES["confirmar"]),
                Opcion("confirmar.cambiar", BOTONES["cambiar"]),
                Opcion("confirmar.no", BOTONES["mejor_no"]),
            ),
        )

    def _confirmar(self, respuesta: str) -> list[Mensaje]:
        if respuesta == "no":
            self._volver_al_menu()
            return [Mensaje(self.texto("mejor_no"))]
        if respuesta == "cambiar":
            for clave in ("vehiculo_id", "tipo_vehiculo_id", "placa", "servicio_id"):
                self.datos.pop(clave, None)
            return self._preguntar_vehiculo(aunque_lo_conozca=True)
        no_hay = self._no_hay_turnos()
        if no_hay is not None:
            return no_hay
        if self.cliente is None and not {"nombre", "acepto_datos_en"} <= self.datos.keys():
            return self._empezar_turno()  # le falta el aviso de datos o el nombre
        try:
            # Si algo falla, se deshace solo lo de aquí adentro (cliente, vehículo y turno):
            # la conversación igual queda anotada.
            with self.sesion.begin_nested():
                cliente = self.cliente or clientes.registrar_cliente(
                    self.sesion,
                    nombre=self.datos["nombre"],
                    telefono=self.telefono,
                    acepta_datos=True,
                    ahora=datetime.fromisoformat(self.datos["acepto_datos_en"]),
                )
                vehiculo = self._vehiculo_para_el_turno(cliente)
                nuevo = casos.pedir_turno(
                    self.sesion,
                    vehiculo_id=vehiculo.id,
                    origen=OrigenTurno.WHATSAPP,
                    servicio_id=self.datos.get("servicio_id"),
                    ahora=self.ahora,
                )
        except YaTieneTurnoActivo as error:
            self.paso = Paso.SEGUNDO_TURNO
            self.datos = {"turno_id": error.turno_id}
            return [self._pregunta()]
        except FueraDeHorario:
            return self._no_hay_turnos() or self._menu("menu")
        except ServicioNoDisponible:
            self.paso = Paso.SERVICIO
            return [_con_nota(self.texto("servicio_no_disponible"), self._pregunta())]
        except PlacaDeOtroCliente:
            self.paso = Paso.PLACA
            return [
                _con_nota(
                    self.texto("placa_de_otro", placa=self.datos.get("placa")), self._pregunta()
                )
            ]
        except ReglaDelNegocio as error:
            self._volver_al_menu()
            return [
                Mensaje(
                    self.texto("no_se_pudo", motivo=error, telefono=self._telefono_del_negocio())
                )
            ]
        self.cliente = cliente
        self._vincular(cliente)
        self._volver_al_menu()
        return [self._turno_confirmado(nuevo)]

    def _vehiculo_para_el_turno(self, cliente: Cliente) -> Vehiculo:
        placa = self.datos.get("placa")
        vehiculo_id = self.datos.get("vehiculo_id")
        if vehiculo_id is not None:
            vehiculo = self.sesion.get(Vehiculo, vehiculo_id)
            if placa and vehiculo.placa != placa:
                vehiculo = clientes.vehiculo_con_placa(self.sesion, vehiculo, placa)
            return vehiculo
        return clientes.registrar_vehiculo(
            self.sesion,
            cliente_id=cliente.id,
            tipo_vehiculo_id=self.datos["tipo_vehiculo_id"],
            placa=placa,
        )

    def _turno_confirmado(self, nuevo: casos.TurnoNuevo) -> Mensaje:
        botones: tuple[Opcion, ...] = ()
        if nuevo.llamado and nuevo.gracia_hasta is not None:
            situacion = self.texto("confirmado_llamado", hora=_hora_limite(nuevo.gracia_hasta))
            botones = textos.botones_del_llamado()
        elif nuevo.salir_ya:
            situacion = self.texto("confirmado_cerca", adelante=textos.adelante(nuevo.adelante))
        else:
            delante = textos.adelante(nuevo.adelante)
            situacion = self.texto(
                "confirmado_en_fila",
                adelante_mayuscula=delante[:1].upper() + delante[1:],
                espera=f"; entras en aprox. {duracion(nuevo.espera_min)}"
                if nuevo.espera_min
                else "",
                avisar=self.parametros.avisar_cuando_falten,
            )
        return Mensaje(
            self.texto(
                "turno_confirmado",
                nombre=_primer_nombre(self.cliente),
                numero=nuevo.numero,
                situacion=situacion,
                gracia=int(self.parametros.gracia.total_seconds() // 60),
            ),
            botones=botones,
        )

    # --- Segundo turno ---------------------------------------------------------------------

    def _pregunta_segundo_turno(self) -> Mensaje:
        posicion = consultas.posicion_del_turno(
            self.sesion, self.datos["turno_id"], ahora=self.ahora
        )
        return Mensaje(
            self.texto(
                "segundo_turno",
                numero=posicion.numero,
                fila=posicion.fila.lower(),
                estado=_estado_corto(posicion),
            ),
            botones=(
                Opcion("segundo.mantener", BOTONES["mantener"]),
                Opcion("segundo.cancelar", BOTONES["cancelarlo"]),
            ),
        )

    def _segundo_turno(self, eleccion: str) -> list[Mensaje]:
        turno_id = self.datos.get("turno_id")
        if eleccion == "cancelar" and turno_id is not None:
            return self._cancelar_turno(turno_id)
        self._volver_al_menu()
        return [Mensaje(self.texto("mantener"))]

    # --- La fila y los precios -------------------------------------------------------------

    def _registrar_consulta(self, tipo: TipoConsulta, tipo_vehiculo_id: int | None = None) -> None:
        clientes.registrar_consulta(
            self.sesion,
            tipo=tipo,
            cliente_id=self.cliente.id if self.cliente else None,
            tipo_vehiculo_id=tipo_vehiculo_id,
            ahora=self.ahora,
        )

    def _como_va(self, resumen: ResumenDeLaFila) -> str:
        """Solo cantidades: el bot nunca muestra nombres ni placas de otros clientes."""
        espera = f"en aprox. {duracion(resumen.espera_min)}" if resumen.espera_min else "pronto"
        if resumen.puestos_activos == 0:
            return self.texto("fila_cerrada")
        if resumen.puede_venir_ya:
            return self.texto("fila_libre")
        if resumen.en_espera == 0:
            return self.texto("fila_sin_espera", espera=espera)
        vehiculos = f"{resumen.en_espera} vehículo{'s' if resumen.en_espera != 1 else ''}"
        return self.texto("fila_con_espera", vehiculos=vehiculos, espera=espera)

    def _ver_la_fila(self) -> list[Mensaje]:
        self._registrar_consulta(TipoConsulta.FILA)
        self._volver_al_menu()
        lineas = [
            f"*{fila.nombre}:* "
            + self._como_va(consultas.resumen_de_la_fila(self.sesion, fila.id, ahora=self.ahora))
            for fila in self.sesion.scalars(select(Fila).order_by(Fila.id))
        ]
        return [
            Mensaje(
                self.texto("fila", filas="\n".join(lineas)),
                botones=(
                    Opcion("menu.pedir", BOTONES["pedir"]),
                    Opcion("menu.precios", BOTONES["precios"]),
                ),
            )
        ]

    def _pregunta_precios(self) -> Mensaje:
        tipos = list(self.sesion.scalars(select(TipoVehiculo).order_by(TipoVehiculo.id)))
        if self.datos.get("solo_carros"):
            carros = [tipo for tipo in tipos if tipo.nombre != "Moto"]
            if carros:
                tipos = carros
        opciones = tuple(Opcion(f"precios.{tipo.id}", tipo.nombre) for tipo in tipos)
        nombre = "precios_carro" if self.datos.get("solo_carros") else "precios_tipo"
        if len(opciones) <= 3:
            return Mensaje(self.texto(nombre), botones=opciones)
        return Mensaje(self.texto(nombre), lista=Lista("Ver vehículos", opciones[:10]))

    def _eligio_precios(self, eleccion: str) -> list[Mensaje]:
        tipo = self.sesion.get(TipoVehiculo, _entero(eleccion))
        if tipo is None:
            return [_con_nota(self.texto("toca_una_opcion"), self._pregunta())]
        self._registrar_consulta(TipoConsulta.PRECIOS, tipo.id)
        self._volver_al_menu()
        # Si después toca "Pedir turno", se piensa que es para este vehículo (_vehiculo_dicho).
        self.datos["pistas"] = {"precios_de": tipo.nombre}
        servicios = self._servicios(tipo)
        botones = (
            Opcion("menu.pedir", BOTONES["pedir"]),
            Opcion("menu.fila", BOTONES["fila"]),
        )
        # Con descripción si cabe; si el menú crece mucho, solo nombre y precio.
        for con_descripcion in (True, False):
            bloques = [
                f"*{servicio.nombre}* · {pesos(precio)}"
                + (f"\n{servicio.descripcion}" if con_descripcion else "")
                for servicio, precio in servicios
            ]
            separador = "\n\n" if con_descripcion else "\n"
            mensaje = self.texto(
                "precios", vehiculo=tipo.nombre.lower(), servicios=separador.join(bloques)
            )
            if len(mensaje) <= 1024:
                return [Mensaje(mensaje, botones=botones)]
        return [Mensaje(recortar(mensaje, 1024), botones=botones)]

    # --- Mi turno, cancelar, llegada y en camino -------------------------------------------

    def _activos(self, *, fila_id: int | None = None) -> list[PosicionDelTurno]:
        if self.cliente is None:
            return []
        posiciones = consultas.turnos_activos_del_cliente(
            self.sesion, self.cliente.id, ahora=self.ahora
        )
        if fila_id is None:
            return posiciones
        fila = self.sesion.get(Fila, fila_id).nombre
        return [posicion for posicion in posiciones if posicion.fila == fila]

    def _mi_turno(self) -> list[Mensaje]:
        self._volver_al_menu()
        posiciones = self._activos()
        if not posiciones:
            return [
                Mensaje(
                    self.texto("sin_turno"),
                    botones=(
                        Opcion("menu.pedir", BOTONES["pedir"]),
                        Opcion("menu.fila", BOTONES["fila"]),
                    ),
                )
            ]
        llamados = [p for p in posiciones if p.estado == E.LLAMADO]
        return [
            Mensaje(
                "\n\n".join(_como_va_el_turno(posicion, self.texto) for posicion in posiciones),
                botones=textos.botones_del_llamado() if len(llamados) == 1 else (),
            )
        ]

    def _preguntar_si_cancela(self) -> list[Mensaje]:
        """Cancelar no se puede deshacer: siempre se pregunta con botones antes."""
        self._volver_al_menu()
        posiciones = self._activos()
        if not posiciones:
            return [
                Mensaje(
                    self.texto("sin_turno_para_cancelar"),
                    botones=(Opcion("menu.pedir", BOTONES["pedir"]),),
                )
            ]
        cancelables = [p for p in posiciones if p.estado in (E.EN_FILA, E.LLAMADO)]
        if not cancelables:
            return [
                Mensaje(self.texto("cancelar_en_el_local", telefono=self._telefono_del_negocio()))
            ]
        self.paso = Paso.CANCELAR
        self.datos = {"cancelables": {str(p.turno_id): p.numero for p in cancelables[:2]}}
        return [self._pregunta()]

    def _pregunta_cancelar(self) -> Mensaje:
        cancelables: dict[str, str] = self.datos.get("cancelables", {})
        if len(cancelables) == 1:
            [(turno_id, numero)] = cancelables.items()
            return Mensaje(
                self.texto("cancelar_confirmar", numero=numero),
                botones=(
                    Opcion(f"cancelar.{turno_id}", BOTONES["si_cancelar"]),
                    Opcion("cancelar.no", BOTONES["no_cancelar"]),
                ),
            )
        botones = [
            Opcion(f"cancelar.{turno_id}", f"Cancelar {numero}")
            for turno_id, numero in cancelables.items()
        ]
        botones.append(Opcion("cancelar.no", "No cancelar"))
        return Mensaje(self.texto("cancelar_cual"), botones=tuple(botones))

    def _cancelar(self, eleccion: str) -> list[Mensaje]:
        if eleccion not in self.datos.get("cancelables", {}):
            self._volver_al_menu()
            return [Mensaje(self.texto("no_cancelado"))]
        return self._cancelar_turno(int(eleccion))

    def _cancelar_turno(self, turno_id: int) -> list[Mensaje]:
        self._volver_al_menu()
        numero = consultas.posicion_del_turno(self.sesion, turno_id, ahora=self.ahora).numero
        try:
            with self.sesion.begin_nested():
                casos.cancelar(self.sesion, turno_id, actor=Actor.CLIENTE, ahora=self.ahora)
        except CambioNoPermitido:
            return [Mensaje(self.texto("ya_no_se_puede_cancelar", numero=numero))]
        return [
            Mensaje(
                self.texto("cancelado", numero=numero),
                botones=(Opcion("menu.pedir", BOTONES["pedir"]),),
            )
        ]

    def _llegada(self) -> list[Mensaje]:
        """ "Ya estoy aquí": el turno pasa a Llegó y puede tomar el próximo puesto libre."""
        self._volver_al_menu()
        esperando = [p for p in self._activos() if p.estado in (E.EN_FILA, E.LLAMADO, E.LLEGO)]
        if not esperando:
            if self.cliente is None:
                # Quizás pidió el turno en el mostrador sin dejar el celular.
                self.paso = Paso.NUMERO_DE_TURNO
                return [self._pregunta()]
            return self._mi_turno()
        numeros = ", ".join(p.numero for p in esperando)
        por_marcar = [p for p in esperando if p.estado != E.LLEGO]
        if not por_marcar:
            return [Mensaje(self.texto("ya_llego", numeros=numeros))]
        for posicion in por_marcar:
            casos.marcar_llegada(
                self.sesion, posicion.turno_id, actor=Actor.CLIENTE, ahora=self.ahora
            )
        return [Mensaje(self.texto("llegada", numeros=numeros))]

    def _en_camino(self) -> list[Mensaje]:
        self._volver_al_menu()
        en_espera = [p for p in self._activos() if p.estado in (E.EN_FILA, E.LLAMADO)]
        if not en_espera:
            return self._mi_turno()
        for posicion in en_espera:
            casos.marcar_en_camino(self.sesion, posicion.turno_id)
        llamado = next((p for p in en_espera if p.gracia_hasta is not None), None)
        if llamado is not None:
            return [
                Mensaje(self.texto("en_camino_llamado", hora=_hora_limite(llamado.gracia_hasta)))
            ]
        return [Mensaje(self.texto("en_camino"))]

    # --- Quejas, felicitaciones y hablar con una persona -------------------------------------

    def _queja(self, escrito: str) -> list[Mensaje]:
        """Se queja de algo que pasó. La queja le llega de una vez al administrador
        (Comentarios, en el dashboard); el bot pide perdón y le deja contar más."""
        comentario = comentarios.registrar(
            self.sesion, TipoComentario.QUEJA, escrito, cliente=self.cliente, ahora=self.ahora
        )
        self._volver_al_menu()
        self.datos = {"comentario": comentario.id, "tipo": TipoComentario.QUEJA.value}
        if self.cliente is None:
            # Sin su permiso no se guarda su número: se le pregunta si quiere que le respondan.
            self.paso = Paso.PERMISO_CONTACTO
            return [self._con_botones_de_permiso(self.texto("queja"))]
        self.paso = Paso.DETALLE
        return [Mensaje(self.texto("queja_cliente", nombre=_primer_nombre(self.cliente)))]

    def _hablar_con_una_persona(self, escrito: str) -> list[Mensaje]:
        """Quiere que le responda alguien y no el bot: le llega al administrador."""
        self._volver_al_menu()
        if self.cliente is None:
            # Se guarda solo si acepta que guardemos su número: sin él no hay a quién responder.
            self.paso = Paso.PERMISO_CONTACTO
            self.datos = {"pide_persona": _sin_pasarse(escrito)}
            return [self._pregunta()]
        comentario = comentarios.registrar(
            self.sesion,
            TipoComentario.QUIERE_HABLAR,
            escrito,
            cliente=self.cliente,
            ahora=self.ahora,
        )
        self.paso = Paso.DETALLE
        self.datos = {"comentario": comentario.id, "tipo": TipoComentario.QUIERE_HABLAR.value}
        return [
            Mensaje(
                self.texto(
                    "persona",
                    nombre=_primer_nombre(self.cliente),
                    telefono=self._telefono_del_negocio(),
                )
            )
        ]

    def _con_botones_de_permiso(self, texto: str) -> Mensaje:
        return Mensaje(
            texto,
            botones=(
                Opcion("responder.si", BOTONES["responder_si"]),
                Opcion("responder.no", BOTONES["responder_no"]),
            ),
        )

    def _pregunta_permiso(self) -> Mensaje:
        nombre = "persona_permiso" if "pide_persona" in self.datos else "queja_permiso"
        return self._con_botones_de_permiso(self.texto(nombre))

    def _permiso_para_responder(self, respuesta: str) -> list[Mensaje]:
        pide_persona = self.datos.get("pide_persona")
        comentario_id = self.datos.get("comentario")
        tipo = _tipo_de_comentario(self.datos.get("tipo"))
        self._volver_al_menu()
        if respuesta != "si":
            telefono = self._telefono_del_negocio()
            if pide_persona is not None:
                return [Mensaje(self.texto("persona_sin_permiso", telefono=telefono))]
            if tipo == TipoComentario.PREGUNTA:
                return [Mensaje(self.texto("pregunta_anonima", telefono=telefono))]
            return [Mensaje(self.texto("queja_anonima"))]
        if pide_persona is not None:
            tipo = TipoComentario.QUIERE_HABLAR
            nuevo = comentarios.registrar(self.sesion, tipo, pide_persona, ahora=self.ahora)
            comentario_id = nuevo.id
        if comentario_id is not None:
            # Aceptó: desde ahora el comentario lleva su número, para responderle.
            comentarios.dar_permiso(self.sesion, comentario_id, self.telefono, ahora=self.ahora)
        self.paso = Paso.DETALLE
        self.datos = {"comentario": comentario_id, "tipo": tipo.value}
        return [Mensaje(self.texto("contacto_si"))]

    def _anotar(self, escrito: str) -> None:
        """Agrega lo que escribió al comentario que está contando (o a lo que pidió, si
        todavía no ha dado permiso para guardarlo). Si el comentario ya no existe (lo
        borraron en el dashboard mientras tanto), se guarda uno nuevo: sus palabras no se
        pierden."""
        if "pide_persona" in self.datos:
            self.datos["pide_persona"] = _sin_pasarse(f"{self.datos['pide_persona']}\n{escrito}")
            return
        comentario_id = self.datos.get("comentario")
        if comentario_id is not None and comentarios.agregar_detalle(
            self.sesion, comentario_id, escrito
        ):
            return
        tipo = _tipo_de_comentario(self.datos.get("tipo"))
        nuevo = comentarios.registrar(
            self.sesion, tipo, escrito, cliente=self.cliente, ahora=self.ahora
        )
        self.datos["comentario"] = nuevo.id

    def _detalle(self, escrito: str, normal: str) -> list[Mensaje]:
        """Lo que escribe después de quejarse o de pedir hablar con alguien: es para el
        administrador, con sus palabras, y se agrega al comentario. Si además pide algo
        que el bot sabe hacer (cancelar, ver su turno...), también se hace."""
        if self.entrada.otro or not escrito:
            return [Mensaje(self.texto("detalle_solo_texto"))]
        entendido = self._entender(escrito)
        if entendido is not None and entendido.intencion == Intencion.SALUDO:
            return self._saludo(entendido)  # "hola": empieza otra conversación
        if entendido is None and normal and set(normal.split()) <= SOLO_SALUDO:
            return self._saludo()
        if not _solo_para_cerrar(normal):
            self._anotar(escrito)
        self._volver_al_menu()
        dicho = self._de_gemini(entendido) or self._fijo("detalle_recibido")
        return [Mensaje(dicho), *self._ademas(normal, escrito, entendido)]

    def _lo_de_la_fila(self, normal: str) -> list[Mensaje] | None:
        """Lo que pide sobre su turno en medio de otra cosa (lo que cuenta de una queja, o
        la conversación con el administrador). Un mensaje corto es un pedido ("ya llegué");
        uno largo es un relato ("ya llegué a la casa y vi el rayón"), y en un relato no se
        marca nada en la fila. None: no es de la fila."""
        corto = len(normal.split()) <= 4
        if FRASE_DEL_QR in normal:
            return self._llego_por_el_qr()  # el código QR del local
        if "cancel" in normal:
            # Siempre pregunta antes, con botones; si no tiene turnos, no hay qué cancelar.
            return self._preguntar_si_cancela() if self._activos() else []
        if corto and "mi turno" in normal:
            return self._mi_turno()
        if corto and _avisa_que_llego(normal):
            return self._llegada()
        if corto and _avisa_que_va_en_camino(normal):
            return self._en_camino()
        return None

    def _ademas(self, normal: str, escrito: str, entendido: Interpretacion | None) -> list[Mensaje]:
        """En lo que contó, ¿pide también algo que el bot sabe hacer? Se hace después de
        anotarlo: primero lo de la fila (_lo_de_la_fila) y después lo demás."""
        if _solo_para_cerrar(normal):
            return []
        de_la_fila = self._lo_de_la_fila(normal)
        if de_la_fila is not None:
            return de_la_fila
        if entendido is None:
            return self._mis_lavadas() if _pregunta_sus_lavadas(normal) else []
        if entendido.intencion not in PIDE_ALGO_MAS:
            return []
        if entendido.intencion == Intencion.CANCELAR and not self._activos():
            return []
        return self._hacer(entendido, escrito)

    def _felicitacion(self, escrito: str, entendido: Interpretacion | None = None) -> list[Mensaje]:
        """Felicita el servicio: se le agradece y le llega al administrador."""
        comentarios.registrar(
            self.sesion,
            TipoComentario.FELICITACION,
            escrito,
            cliente=self.cliente,
            ahora=self.ahora,
        )
        nombre, datos = "felicitacion", {}
        if self.cliente is not None:
            nombre, datos = "felicitacion_cliente", {"nombre": _primer_nombre(self.cliente)}
        dicho = self._de_gemini(entendido)
        if self.paso == Paso.MENU:
            self._volver_al_menu()
            if dicho is not None:
                return [Mensaje(dicho)]  # como la charla: sin el menú pegado
            return [_con_el_menu(self._fijo(nombre, **datos))]
        # En medio de otra pregunta (pidiendo turno, por ejemplo): se agradece y se sigue.
        return [_con_nota(dicho or self._fijo(nombre, **datos), self._pregunta())]

    # --- Preguntas sobre el negocio, charla y mensajes graves ---------------------------------

    def _con_la_ficha(self, entendido: Interpretacion, escrito: str) -> list[Mensaje]:
        """Una pregunta sobre el negocio, charla, un saludo en medio de algo o un mensaje que
        no se entiende: contesta Gemini, solo con la ficha, y el bot lo revisa. En medio de
        otra pregunta (pidiendo turno, por ejemplo), contesta y sigue con lo que estaba.

        Lo que escribe Gemini va solo, sin el menú: es una conversación, no una venta, y los
        botones de antes se pueden seguir tocando. Si no se entendió, sí lleva el menú: los
        botones ayudan. El texto fijo también lo lleva: sin Gemini, los botones son el
        camino."""
        intencion = entendido.intencion
        dicho = self._de_gemini(entendido)
        if intencion == Intencion.PREGUNTA and (dicho is None or not entendido.sabe):
            # La ficha no lo dice: lo que sí sabe (si Gemini lo escribió) y al administrador.
            return self._pregunta_sin_respuesta(escrito, dicho if not entendido.sabe else None)
        if self.paso != Paso.MENU:
            nota = dicho or self._fijo(
                "toca_una_opcion" if intencion == Intencion.OTRO else "charla"
            )
            return [_con_nota(nota, self._pregunta())]
        if dicho is None:
            if intencion == Intencion.SALUDO:
                return self._saludo()
            return [
                _con_el_menu(self._fijo("no_entendi" if intencion == Intencion.OTRO else "charla"))
            ]
        if intencion == Intencion.OTRO:
            return [_con_el_menu(dicho)]
        return [Mensaje(dicho)]

    def _ficha(self) -> str:
        """Lo que Gemini sabe del negocio (se arma una sola vez por mensaje)."""
        if self._la_ficha is None:
            self._la_ficha = ficha_completa(
                self.parametros, self._catalogo(), servicios_de_la_ficha(self.sesion)
            )
        return self._la_ficha

    def _pregunta_sin_respuesta(self, escrito: str, dicho: str | None = None) -> list[Mensaje]:
        """La ficha no lo dice: la pregunta le llega al administrador, en Comentarios.
        `dicho`: lo que Gemini escribió para decir que eso no lo tiene confirmado (con lo
        que sí sabe que tenga que ver); sin él, sale el texto fijo."""
        if self.paso == Paso.PERMISO_CONTACTO:
            self._anotar(escrito)  # a la misma pregunta (o queja) por la que pide permiso
        else:
            comentario = comentarios.registrar(
                self.sesion,
                TipoComentario.PREGUNTA,
                escrito,
                cliente=self.cliente,
                ahora=self.ahora,
            )
        if self.paso != Paso.MENU:
            # Pidiendo un turno, por ejemplo: queda anotada y el bot sigue con lo que iba.
            return [_con_nota(dicho or self._fijo("pregunta_en_el_camino"), self._pregunta())]
        self._volver_al_menu()
        if self.cliente is None:
            # Sin su permiso no se guarda su número: se le pregunta si quiere que le respondan.
            self.paso = Paso.PERMISO_CONTACTO
            self.datos = {"comentario": comentario.id, "tipo": TipoComentario.PREGUNTA.value}
            if dicho is not None:
                texto = f"{dicho}\n\n{self.texto('queja_permiso')}"
            else:
                texto = self._fijo("pregunta_sin_respuesta")
            return [self._con_botones_de_permiso(texto)]
        nombre = _primer_nombre(self.cliente)
        if dicho is not None:
            return [Mensaje(f"{dicho} {self.texto('pregunta_pasada', nombre=nombre)}")]
        return [_con_el_menu(self._fijo("pregunta_al_administrador", nombre=nombre))]

    def _mientras_responde_una_persona(
        self, pausa_vigente: PausaDelBot, normal: str
    ) -> list[Mensaje]:
        """El administrador le está escribiendo desde Comentarios: el bot no se mete en la
        conversación. Lo que escribe le llega al administrador (el comentario vuelve a
        quedar por atender) y, después de la pausa, el bot empieza desde el menú. Lo de la
        fila sí lo atiende el bot ("ya llegué", "mi turno"...): la fila no espera."""
        de_la_fila = self._lo_de_la_fila(normal) if normal else None
        if de_la_fila:  # sin turnos que cancelar, "cancelar" también es para la persona
            return de_la_fila
        self._avisar_a_quien_responde(pausa_vigente)
        self._volver_al_menu()
        return []

    def _avisar_a_quien_responde(self, pausa_vigente: PausaDelBot) -> None:
        if pausa_vigente.comentario_id is not None:
            comentarios.reabrir(self.sesion, pausa_vigente.comentario_id)

    def _ayuda_urgente(self) -> list[Mensaje]:
        """Dice que quiere morir o hacerse daño. El bot no improvisa ni guarda nada: manda
        un texto fijo con las líneas de ayuda, sin botones (no es momento de ofrecer turnos)."""
        self._volver_al_menu()
        return [Mensaje(self.texto("ayuda_urgente"))]

    # --- Sus lavadas ------------------------------------------------------------------------

    def _mis_lavadas(self) -> list[Mensaje]:
        """Cuántas lavadas lleva y cuándo fue la última, con los datos del sistema."""
        self._volver_al_menu()
        pedir = (Opcion("menu.pedir", BOTONES["pedir"]),)
        if self.cliente is None:
            return [Mensaje(self.texto("mis_lavadas_sin_registro"), botones=pedir)]
        cuantas, ultima = clientes.lavadas(self.sesion, self.cliente.id)
        nombre = _primer_nombre(self.cliente)
        if cuantas == 0 or ultima is None:
            return [Mensaje(self.texto("sin_lavadas", nombre=nombre), botones=pedir)]
        return [
            Mensaje(
                self.texto(
                    "mis_lavadas",
                    nombre=nombre,
                    lavadas=f"{cuantas} lavada{'s' if cuantas != 1 else ''}",
                    ultima=cuando_fue(ultima, tiempo.hora_local(self.ahora).date()),
                ),
                botones=pedir,
            )
        ]

    # --- Código QR del mostrador -------------------------------------------------------------

    def _llego_por_el_qr(self) -> list[Mensaje]:
        """ "Hola, ya estoy en la fila": un cliente que pidió turno en el mostrador.

        Se busca su turno por el celular que anotó el administrador; si no aparece, se le
        pide el número de turno.
        """
        self._volver_al_menu()
        if self.cliente is None:
            self.paso = Paso.NUMERO_DE_TURNO
            return [self._pregunta()]
        posiciones = self._activos()
        if not posiciones:
            return self._mi_turno()
        return [Mensaje(self.texto("vinculado", numero=", ".join(p.numero for p in posiciones)))]

    def _escribio_numero_de_turno(self, escrito: str) -> list[Mensaje]:
        encontrado = _NUMERO_DE_TURNO.fullmatch(normalizar(escrito).replace(" ", ""))
        if encontrado is None:
            return [Mensaje(self.texto("numero_invalido"))]
        prefijo, consecutivo = encontrado.group(1).upper(), int(encontrado.group(2))
        numero = reglas.numero_de_turno(prefijo, consecutivo)
        turno = self.sesion.scalars(
            select(Turno)
            .join(Fila, Fila.id == Turno.fila_id)
            .where(
                Fila.prefijo == prefijo,
                Turno.consecutivo == consecutivo,
                Turno.fecha == tiempo.hora_local(self.ahora).date(),
                Turno.estado.in_(ESTADOS_ACTIVOS),
            )
        ).one_or_none()
        if turno is None:
            return [Mensaje(self.texto("numero_no_existe", numero=numero))]
        self._volver_al_menu()
        cliente = self.sesion.get(Cliente, turno.cliente_id)
        if cliente.telefono is not None and cliente.telefono != self.telefono:
            return [Mensaje(self.texto("numero_otro_celular"))]
        # El cliente lo registró el administrador sin celular: desde ahora es este. Ya
        # aceptó el aviso de datos en el mostrador. La respuesta no dice su nombre: quien
        # escribe solo dio un número de turno.
        cliente.telefono = self.telefono
        self.sesion.flush()
        self.cliente = cliente
        self._vincular(cliente)
        return [Mensaje(self.texto("vinculado", numero=numero))]


# --- Piezas pequeñas ----------------------------------------------------------------------------


def _con_nota(nota: str, pregunta: Mensaje) -> Mensaje:
    """La misma pregunta, con una línea antes."""
    return Mensaje(f"{nota}\n\n{pregunta.texto}", botones=pregunta.botones, lista=pregunta.lista)


def _entero(texto_escrito: str) -> int | None:
    return int(texto_escrito) if texto_escrito.isdigit() else None


def _con_el_menu(texto: str) -> Mensaje:
    return Mensaje(
        texto,
        botones=(
            Opcion("menu.pedir", BOTONES["pedir"]),
            Opcion("menu.fila", BOTONES["fila"]),
            Opcion("menu.precios", BOTONES["precios"]),
        ),
    )


def _tipo_de_comentario(valor: object) -> TipoComentario:
    """El tipo guardado en la conversación; si no se reconoce, es una queja."""
    try:
        return TipoComentario(valor)
    except ValueError:
        return TipoComentario.QUEJA


def _sin_pasarse(texto: str) -> str:
    """Lo que escribió el cliente, sin pasarse de lo que se guarda de un comentario."""
    return recortar(texto.strip(), LARGO_MAXIMO)


def _primer_nombre(cliente: Cliente | None) -> str:
    return cliente.nombre.split()[0] if cliente is not None and cliente.nombre else ""


def _nombre_del_vehiculo(tipo: str, placa: str | None, *, pendiente: str = " sin placa") -> str:
    """ "Automóvil ABC123", o "Automóvil sin placa" (cabe en un botón de WhatsApp)."""
    return f"{tipo} {placa}" if placa else f"{tipo}{pendiente}"


def _hora_limite(momento: datetime) -> str:
    return hora_con_articulo(tiempo.hora_local(momento))


def _estado_corto(posicion: PosicionDelTurno) -> str:
    if posicion.estado == E.EN_FILA and posicion.adelante is not None:
        return textos.adelante(posicion.adelante)
    if posicion.estado == E.LLAMADO and posicion.gracia_hasta is not None:
        return f"te estamos llamando, tienes hasta {_hora_limite(posicion.gracia_hasta)}"
    return NOMBRE_DEL_ESTADO[posicion.estado].lower()


def _como_va_el_turno(posicion: PosicionDelTurno, texto: Textos) -> str:
    datos = {"numero": posicion.numero, "fila": posicion.fila.lower()}
    if posicion.estado == E.LLAMADO and posicion.gracia_hasta is not None:
        return texto("mi_turno_llamado", hora=_hora_limite(posicion.gracia_hasta), **datos)
    if posicion.estado == E.EN_FILA:
        espera = (
            f"; entras en aprox. {duracion(posicion.espera_min)}" if posicion.espera_min else ""
        )
        return texto(
            "mi_turno_en_fila",
            adelante=textos.adelante(posicion.adelante or 0),
            espera=espera,
            **datos,
        )
    nombres = {
        E.LLEGO: "mi_turno_llego",
        E.EN_LAVADO: "mi_turno_en_lavado",
        E.LISTO: "mi_turno_listo",
    }
    return texto(nombres[posicion.estado], **datos)
