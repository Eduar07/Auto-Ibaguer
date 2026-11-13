"""Las tarifas del parqueadero y lo que sale en el ticket.

Se guardan en la misma tabla `configuracion` del lavadero, con claves que empiezan por
"parqueadero." Si una clave no está (por ejemplo, en una base recién creada), se usa el
valor inicial de aquí abajo en vez de fallar.
"""

import re
from dataclasses import dataclass, replace
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalogo.tablas import Configuracion
from app.parqueadero.errores import TarifaInvalida
from app.parqueadero.estados import NOMBRE_DEL_TIPO, TipoParqueo
from app.parqueadero.reglas import Tarifa, validar_tarifa

PREFIJO = "parqueadero."
ANCHOS_DE_TICKET = (58, 80)  # milímetros del papel de las impresoras térmicas comunes

# clave (sin el prefijo), valor inicial, para qué sirve
VALORES_INICIALES: tuple[tuple[str, Any, str], ...] = (
    ("hora_carro", 3000, "Valor de la hora de carro en el parqueadero"),
    ("hora_moto", 1500, "Valor de la hora de moto en el parqueadero"),
    (
        "fraccion_carro",
        None,
        "Valor de los primeros 15 minutos de carro; vacío = se cobra la hora completa",
    ),
    (
        "fraccion_moto",
        None,
        "Valor de los primeros 15 minutos de moto; vacío = se cobra la hora completa",
    ),
    ("gracia_minutos", 15, "Minutos de gracia después de cada hora antes de cobrar la siguiente"),
    ("dia_carro", None, "Lo máximo que paga un carro por día (24 horas); vacío = todo por horas"),
    ("dia_moto", None, "Lo máximo que paga una moto por día (24 horas); vacío = todo por horas"),
    (
        "dia_siguiente_carro",
        None,
        "Lo que vale cada día de carro después del primero; vacío = lo mismo que el primero",
    ),
    (
        "dia_siguiente_moto",
        None,
        "Lo que vale cada día de moto después del primero; vacío = lo mismo que el primero",
    ),
    ("nombre", "Parqueadero", "Nombre del parqueadero en el ticket"),
    ("nit", "", "NIT del parqueadero en el ticket (solo el número, con su dígito de verificación)"),
    ("direccion", "", "Dirección del parqueadero en el ticket"),
    ("telefono", "", "Teléfono del parqueadero en el ticket"),
    (
        "pie_ticket",
        "Conserve este ticket para retirar su vehículo.",
        "Texto al final del ticket (condiciones del servicio)",
    ),
    ("ancho_ticket", 80, "Ancho del papel de la impresora de tickets, en milímetros"),
    (
        "correos_reporte",
        "",
        "Correos que reciben el reporte diario del parqueadero al cerrar la caja",
    ),
)
_INICIALES = {clave: valor for clave, valor, _ in VALORES_INICIALES}
_DESCRIPCIONES = {clave: descripcion for clave, _, descripcion in VALORES_INICIALES}


@dataclass(frozen=True)
class AjustesParqueadero:
    hora_carro: int
    hora_moto: int
    fraccion_carro: int | None
    fraccion_moto: int | None
    gracia_minutos: int
    nombre: str
    nit: str
    direccion: str
    telefono: str
    pie_ticket: str
    ancho_ticket: int
    correos_reporte: str = ""  # "jefe@gmail.com, socio@gmail.com"
    dia_carro: int | None = None
    dia_moto: int | None = None
    dia_siguiente_carro: int | None = None
    dia_siguiente_moto: int | None = None

    @property
    def destinos_del_reporte(self) -> list[str]:
        return [correo for correo in self.correos_reporte.split(", ") if correo]

    def tarifa(self, tipo: TipoParqueo) -> Tarifa:
        if tipo is TipoParqueo.MOTO:
            return Tarifa(
                self.hora_moto,
                self.gracia_minutos,
                self.fraccion_moto,
                self.dia_moto,
                self.dia_siguiente_moto,
            )
        return Tarifa(
            self.hora_carro,
            self.gracia_minutos,
            self.fraccion_carro,
            self.dia_carro,
            self.dia_siguiente_carro,
        )


def leer_ajustes(sesion: Session) -> AjustesParqueadero:
    filas = sesion.scalars(
        select(Configuracion).where(Configuracion.clave.startswith(PREFIJO))
    ).all()
    guardados = {fila.clave.removeprefix(PREFIJO): fila.valor for fila in filas}
    valores = {clave: guardados.get(clave, inicial) for clave, inicial in _INICIALES.items()}
    return AjustesParqueadero(
        hora_carro=int(valores["hora_carro"]),
        hora_moto=int(valores["hora_moto"]),
        fraccion_carro=_entero_o_nada(valores["fraccion_carro"]),
        fraccion_moto=_entero_o_nada(valores["fraccion_moto"]),
        gracia_minutos=int(valores["gracia_minutos"]),
        nombre=str(valores["nombre"]),
        nit=str(valores["nit"]),
        direccion=str(valores["direccion"]),
        telefono=str(valores["telefono"]),
        pie_ticket=str(valores["pie_ticket"]),
        ancho_ticket=int(valores["ancho_ticket"]),
        correos_reporte=str(valores["correos_reporte"]),
        dia_carro=_entero_o_nada(valores["dia_carro"]),
        dia_moto=_entero_o_nada(valores["dia_moto"]),
        dia_siguiente_carro=_entero_o_nada(valores["dia_siguiente_carro"]),
        dia_siguiente_moto=_entero_o_nada(valores["dia_siguiente_moto"]),
    )


def _entero_o_nada(valor: Any) -> int | None:
    return None if valor is None else int(valor)


def limpiar_nit(texto: str) -> str:
    """El NIT tal como sale en el ticket después de la palabra "NIT": "900.123.456-7".

    Si escriben "NIT: 900..." se quita la palabra (si no, saldría "NIT NIT 900..."). Solo
    números, puntos, guiones y espacios: un nombre aquí es un error de quien lo llenó.
    """
    nit = re.sub(r"^\s*n\.?\s*i\.?\s*t\.?\s*[:.]?\s*", "", texto, flags=re.IGNORECASE).strip()
    if nit and (not re.fullmatch(r"[\d.\- ]+", nit) or len(re.sub(r"\D", "", nit)) < 6):
        raise TarifaInvalida("El NIT es solo el número, por ejemplo 900.123.456-7.")
    return nit


MAXIMO_DE_CORREOS = 5


def limpiar_correos(texto: str) -> str:
    """Los correos de los jefes, revisados y separados por coma: "a@gmail.com, b@gmail.com".

    Se pueden escribir separados por coma, punto y coma o espacio. Uno mal escrito no se
    guarda: mejor saberlo hoy que descubrir en un mes que el reporte nunca llegó.
    """
    correos = []
    for escrito in re.split(r"[,;\s]+", texto.strip()):
        if not escrito:
            continue
        try:
            correo = validate_email(escrito, check_deliverability=False).normalized
        except EmailNotValidError as error:
            raise TarifaInvalida(f"Revisa el correo «{escrito}»: no parece un correo.") from error
        if correo.lower() not in (c.lower() for c in correos):
            correos.append(correo)
    if len(correos) > MAXIMO_DE_CORREOS:
        raise TarifaInvalida(f"El reporte se manda a máximo {MAXIMO_DE_CORREOS} correos.")
    return ", ".join(correos)


def guardar_ajustes(sesion: Session, ajustes: AjustesParqueadero) -> None:
    """Revisa y guarda. Las entradas que ya están adentro conservan la tarifa con que entraron."""
    for tipo in TipoParqueo:
        try:
            validar_tarifa(ajustes.tarifa(tipo))
        except TarifaInvalida as error:
            raise TarifaInvalida(f"{NOMBRE_DEL_TIPO[tipo]}: {error}") from error
    if ajustes.ancho_ticket not in ANCHOS_DE_TICKET:
        raise TarifaInvalida("El ancho del ticket es de 58 o de 80 milímetros.")
    if not ajustes.nombre.strip():
        raise TarifaInvalida("Escribe el nombre del parqueadero: sale arriba en el ticket.")
    limpios = replace(
        ajustes,
        nombre=ajustes.nombre.strip(),
        nit=limpiar_nit(ajustes.nit),
        direccion=ajustes.direccion.strip(),
        telefono=ajustes.telefono.strip(),
        pie_ticket=ajustes.pie_ticket.strip(),
        correos_reporte=limpiar_correos(ajustes.correos_reporte),
    )
    existentes = {
        fila.clave: fila
        for fila in sesion.scalars(
            select(Configuracion).where(Configuracion.clave.startswith(PREFIJO))
        )
    }
    for clave in _INICIALES:
        valor = getattr(limpios, clave)
        fila = existentes.get(PREFIJO + clave)
        if fila is None:
            sesion.add(
                Configuracion(clave=PREFIJO + clave, valor=valor, descripcion=_DESCRIPCIONES[clave])
            )
        elif fila.valor != valor:
            fila.valor = valor
    sesion.flush()
