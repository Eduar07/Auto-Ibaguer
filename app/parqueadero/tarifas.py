"""Las tarifas del parqueadero y lo que sale en el ticket.

Se guardan en la misma tabla `configuracion` del lavadero, con claves que empiezan por
"parqueadero." Si una clave no está (por ejemplo, en una base recién creada), se usa el
valor inicial de aquí abajo en vez de fallar.
"""

from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalogo.tablas import Configuracion
from app.parqueadero.errores import TarifaInvalida
from app.parqueadero.estados import TipoParqueo
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
    ("nombre", "Parqueadero", "Nombre del parqueadero en el ticket"),
    ("nit", "", "NIT o razón social en el ticket"),
    ("direccion", "", "Dirección del parqueadero en el ticket"),
    ("telefono", "", "Teléfono del parqueadero en el ticket"),
    (
        "pie_ticket",
        "Conserve este ticket para retirar su vehículo.",
        "Texto al final del ticket (condiciones del servicio)",
    ),
    ("ancho_ticket", 80, "Ancho del papel de la impresora de tickets, en milímetros"),
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

    def tarifa(self, tipo: TipoParqueo) -> Tarifa:
        if tipo is TipoParqueo.MOTO:
            return Tarifa(self.hora_moto, self.gracia_minutos, self.fraccion_moto)
        return Tarifa(self.hora_carro, self.gracia_minutos, self.fraccion_carro)


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
    )


def _entero_o_nada(valor: Any) -> int | None:
    return None if valor is None else int(valor)


def guardar_ajustes(sesion: Session, ajustes: AjustesParqueadero) -> None:
    """Revisa y guarda. Las entradas que ya están adentro conservan la tarifa con que entraron."""
    for tipo in TipoParqueo:
        validar_tarifa(ajustes.tarifa(tipo))
    if ajustes.ancho_ticket not in ANCHOS_DE_TICKET:
        raise TarifaInvalida("El ancho del ticket es de 58 o de 80 milímetros.")
    if not ajustes.nombre.strip():
        raise TarifaInvalida("Escribe el nombre del parqueadero: sale arriba en el ticket.")
    limpios = replace(
        ajustes,
        nombre=ajustes.nombre.strip(),
        nit=ajustes.nit.strip(),
        direccion=ajustes.direccion.strip(),
        telefono=ajustes.telefono.strip(),
        pie_ticket=ajustes.pie_ticket.strip(),
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
