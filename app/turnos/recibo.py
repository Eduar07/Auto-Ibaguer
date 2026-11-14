"""Lo que sale en el recibo del lavadero y a quién le llega su reporte del día.

El nombre y el teléfono del negocio son los de Configuración (los mismos que dice el bot);
aquí van el NIT, la dirección, el texto del final del recibo, el ancho del papel de la
impresora, si el recibo se imprime solo al cobrar y los correos de los jefes. Se guardan en
la tabla `configuracion` con la clave "lavadero." adelante, como los del parqueadero.
"""

from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import formato
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import Configuracion
from app.errores import ReglaDelNegocio
from app.parqueadero.tarifas import ANCHOS_DE_TICKET, limpiar_correos, limpiar_nit

PREFIJO = "lavadero."

# clave (sin el prefijo), valor inicial, para qué sirve
VALORES_INICIALES: tuple[tuple[str, Any, str], ...] = (
    ("nit", "", "NIT del lavadero en el recibo (solo el número, con su dígito de verificación)"),
    ("direccion", "", "Dirección del lavadero en el recibo"),
    ("pie_recibo", "Gracias por preferirnos.", "Texto al final del recibo del lavadero"),
    ("ancho_ticket", 80, "Ancho del papel de la impresora del lavadero, en milímetros"),
    ("imprimir_al_cobrar", False, "Si el recibo del lavadero se imprime solo al cobrar un turno"),
    ("correos_reporte", "", "Correos que reciben el reporte diario del lavadero al cerrar su caja"),
)
_INICIALES = {clave: valor for clave, valor, _ in VALORES_INICIALES}

# Lo más largo que cabe bien en el recibo (en el de 58 mm, unas tres o cuatro líneas).
LARGO_MAXIMO = {"direccion": 80, "pie_recibo": 240}
_DESCRIPCIONES = {clave: descripcion for clave, _, descripcion in VALORES_INICIALES}


class AjustesInvalidos(ReglaDelNegocio):
    pass


@dataclass(frozen=True)
class AjustesDelRecibo:
    nit: str
    direccion: str
    pie_recibo: str
    ancho_ticket: int
    imprimir_al_cobrar: bool
    correos_reporte: str = ""  # "jefe@gmail.com, socio@gmail.com"

    @property
    def destinos_del_reporte(self) -> list[str]:
        return [correo for correo in self.correos_reporte.split(", ") if correo]


def leer_ajustes(sesion: Session) -> AjustesDelRecibo:
    filas = sesion.scalars(select(Configuracion).where(Configuracion.clave.startswith(PREFIJO)))
    guardados = {fila.clave.removeprefix(PREFIJO): fila.valor for fila in filas}
    valores = {clave: guardados.get(clave, inicial) for clave, inicial in _INICIALES.items()}
    return AjustesDelRecibo(
        nit=str(valores["nit"]),
        direccion=str(valores["direccion"]),
        pie_recibo=str(valores["pie_recibo"]),
        ancho_ticket=int(valores["ancho_ticket"]),
        imprimir_al_cobrar=bool(valores["imprimir_al_cobrar"]),
        correos_reporte=str(valores["correos_reporte"]),
    )


def guardar_ajustes(sesion: Session, ajustes: AjustesDelRecibo) -> None:
    """Revisa y guarda. Un NIT o un correo mal escrito no se guarda: se dice cuál."""
    if ajustes.ancho_ticket not in ANCHOS_DE_TICKET:
        raise AjustesInvalidos("El ancho del papel es de 58 o de 80 milímetros.")
    for clave, muy_largo in (
        ("direccion", "La dirección es muy larga"),
        ("pie_recibo", "El texto del final es muy largo"),
    ):
        if len(getattr(ajustes, clave).strip()) > LARGO_MAXIMO[clave]:
            raise AjustesInvalidos(f"{muy_largo}: máximo {LARGO_MAXIMO[clave]} letras.")
    try:
        limpios = replace(
            ajustes,
            nit=limpiar_nit(ajustes.nit),
            direccion=ajustes.direccion.strip(),
            pie_recibo=ajustes.pie_recibo.strip(),
            correos_reporte=limpiar_correos(ajustes.correos_reporte),
        )
    except ReglaDelNegocio as error:
        raise AjustesInvalidos(str(error)) from error
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


@dataclass(frozen=True)
class DatosDelTicket:
    """Lo que sale arriba y abajo en un ticket del lavadero (el recibo, el cierre de caja, el
    reporte del día): lo de Configuración y lo del recibo, con los nombres que usan los
    tickets del parqueadero (tickets/_base.html)."""

    nombre: str
    nit: str
    direccion: str
    telefono: str
    pie_ticket: str
    ancho_ticket: int


def datos_del_ticket(sesion: Session) -> DatosDelTicket:
    parametros = leer_parametros(sesion)
    ajustes = leer_ajustes(sesion)
    return DatosDelTicket(
        nombre=parametros.nombre_negocio,
        nit=ajustes.nit,
        direccion=ajustes.direccion,
        telefono=formato.telefono(parametros.telefono_contacto),  # "322 755 6850"
        pie_ticket=ajustes.pie_recibo,
        ancho_ticket=ajustes.ancho_ticket,
    )
