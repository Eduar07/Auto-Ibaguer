"""Reglas de clientes y vehículos, sin base de datos: teléfonos, nombres y placas."""

import re
import secrets

from app.clientes.errores import NombreInvalido, PlacaInvalida, TelefonoInvalido

LARGO_MAXIMO_NOMBRE = 80
# Los celulares inventados para probar (Probar el bot, simular-turno) empiezan así: ningún
# celular colombiano empieza por 5799. A esos nunca se les escribe por WhatsApp.
PREFIJO_DE_PRUEBA = "5799"

# A quien tiene nombre de usuario en WhatsApp, Meta le puede mandar al bot sin su celular:
# solo un id de usuario de la empresa (BSUID): país, punto y hasta 128 letras y números,
# como "CO.13491208655302741918". Con él se le contesta igual. Donde el sistema guarda el
# "teléfono" de una conversación de WhatsApp, guarda el celular o, si no lo hay, ese id.
USUARIO_DE_WHATSAPP = re.compile(r"[A-Z]{2}\.[A-Za-z0-9]{1,128}")
LARGO_DEL_CONTACTO = 140
# La regla en la base de datos: el celular (solo dígitos) o el usuario de WhatsApp.
CONTACTO_VALIDO = r"telefono ~ '^([0-9]{8,15}|[A-Z]{2}\.[A-Za-z0-9]{1,128})$'"


def es_usuario_de_whatsapp(contacto: str | None) -> bool:
    """¿Es el id de usuario de WhatsApp (sin celular) en vez de un celular?"""
    return bool(contacto) and USUARIO_DE_WHATSAPP.fullmatch(contacto) is not None


def normalizar_telefono(texto: str) -> str:
    """ "+57 322 755 6850" o "3227556850" -> "573227556850".

    Se guarda como lo trae WhatsApp: formato internacional, solo dígitos. A un celular
    colombiano de 10 dígitos se le agrega el 57.
    """
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) == 10 and digitos.startswith("3"):
        digitos = "57" + digitos
    if not 8 <= len(digitos) <= 15:
        raise TelefonoInvalido(f"El teléfono {texto.strip()!r} no es válido.")
    return digitos


def normalizar_contacto(texto: str) -> str:
    """Quien escribe por WhatsApp: su celular normalizado o, si no muestra el celular, su
    id de usuario tal cual. Lo que alguien escribe a mano (el celular del negocio, el de
    un cliente en el parqueadero) usa normalizar_telefono: ahí solo vale un celular."""
    limpio = texto.strip()
    return limpio if es_usuario_de_whatsapp(limpio) else normalizar_telefono(texto)


def celular_inventado() -> str:
    """Un celular para probar: 5799 y ocho cifras al azar."""
    return f"{PREFIJO_DE_PRUEBA}{secrets.randbelow(10**8):08d}"


def es_de_prueba(telefono: str) -> bool:
    return telefono.startswith(PREFIJO_DE_PRUEBA)


def nombre_bonito(texto: str) -> str:
    """ "  eduar   GUERRERO " -> "Eduar Guerrero": mayúscula inicial en cada palabra."""
    palabras = texto.split()
    if not palabras:
        raise NombreInvalido("Falta el nombre.")
    nombre = " ".join(palabra[:1].upper() + palabra[1:].lower() for palabra in palabras)
    if len(nombre) > LARGO_MAXIMO_NOMBRE:
        raise NombreInvalido(f"El nombre puede tener máximo {LARGO_MAXIMO_NOMBRE} caracteres.")
    return nombre


def normalizar_placa(texto: str) -> str:
    """ " abc-123 " -> "ABC123": sin espacios, guiones ni puntos, y en mayúsculas."""
    return re.sub(r"[\s.\-]", "", texto).upper()


# Una placa dentro de un mensaje: 3 letras y 3 caracteres (ABC123, ABC12D), pegados o con
# un espacio o guion en medio.
_PLACA_EN_EL_TEXTO = re.compile(r"\b([A-Za-z]{3})([ -]?)(\d{2}[0-9A-Za-z])\b")


def placa_en_el_texto(texto: str) -> str | None:
    """La placa que trae un mensaje ("quiero lavar la moto abc12d" -> "ABC12D"), o None si
    no trae una o trae varias. Con un espacio en medio, solo si las letras van en
    mayúscula: "por 120 mil" no es una placa, "ABC 123" sí. El formato de cada tipo de
    vehículo lo revisa validar_placa."""
    encontradas = {
        (letras + resto).upper()
        for letras, separador, resto in _PLACA_EN_EL_TEXTO.findall(texto)
        if separador != " " or letras.isupper()
    }
    return encontradas.pop() if len(encontradas) == 1 else None


def validar_placa(texto: str, patron: str, tipo_vehiculo: str) -> str:
    """Devuelve la placa limpia si tiene el formato del tipo de vehículo.

    Carro: 3 letras y 3 números (ABC123). Moto: 3 letras, 2 números y 1 letra (ABC12D).
    """
    placa = normalizar_placa(texto)
    if re.fullmatch(patron, placa) is None:
        raise PlacaInvalida(
            f"La placa {placa or texto!r} no tiene el formato de placa de {tipo_vehiculo.lower()}."
        )
    return placa
