"""Lista para abrir: lo que falta configurar antes de trabajar en serio con los dos negocios.

Cada punto dice si ya está, por qué importa y dónde se arregla. Lo que el sistema no puede
saber por sí mismo (el plan de Render, las copias en GitHub, la impresora) va como recordatorio.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalogo.datos_iniciales import CONFIGURACION_INICIAL
from app.catalogo.parametros import leer_parametros
from app.catalogo.tablas import Configuracion
from app.config import obtener_entorno
from app.parqueadero.tablas import Mensualidad
from app.parqueadero.tarifas import PREFIJO as PREFIJO_PARQUEADERO
from app.parqueadero.tarifas import leer_ajustes as ajustes_del_parqueadero
from app.turnos.recibo import leer_ajustes as ajustes_del_recibo
from app.web.tablas import Administrador, Rol

TELEFONO_DE_EJEMPLO = dict((c, v) for c, v, _ in CONFIGURACION_INICIAL)["telefono_contacto"]


@dataclass(frozen=True)
class Punto:
    que: str
    listo: bool | None  # None: el sistema no lo puede saber; es un recordatorio
    por_que: str
    donde: str | None = None  # el enlace para arreglarlo
    obligatorio: bool = True


@dataclass(frozen=True)
class Grupo:
    nombre: str
    puntos: list[Punto]


def revisar(sesion: Session) -> list[Grupo]:
    entorno = obtener_entorno()
    parametros = leer_parametros(sesion)
    parqueadero = ajustes_del_parqueadero(sesion)
    recibo = ajustes_del_recibo(sesion)
    tarifas_guardadas = sesion.scalar(
        select(func.count())
        .select_from(Configuracion)
        .where(Configuracion.clave == f"{PREFIJO_PARQUEADERO}hora_carro")
    )
    mensualidades = sesion.scalar(select(func.count()).select_from(Mensualidad))
    cajeros = sesion.scalar(
        select(func.count())
        .select_from(Administrador)
        .where(Administrador.rol == Rol.CAJERO, Administrador.activo)
    )
    return [
        Grupo(
            "Parqueadero",
            [
                Punto(
                    "Tarifas guardadas",
                    bool(tarifas_guardadas),
                    "La hora de carro y de moto, la gracia y, si aplica, el cobro por días: "
                    "revisa que no pasen el tope de tu categoría.",
                    "/parqueadero/tarifas",
                ),
                Punto(
                    "NIT, dirección y teléfono en el ticket",
                    bool(parqueadero.nit and parqueadero.direccion and parqueadero.telefono),
                    "La Ley 1480 pide que el recibo diga quién presta el servicio.",
                    "/parqueadero/tarifas",
                ),
                Punto(
                    "Mensualidades que ya tenías",
                    bool(mensualidades),
                    "Créalas antes del primer día: así el sistema sabe quién está al día.",
                    "/parqueadero/mensualidades",
                    obligatorio=False,
                ),
            ],
        ),
        Grupo(
            "Lavadero",
            [
                Punto(
                    "Teléfono del negocio",
                    parametros.telefono_contacto != TELEFONO_DE_EJEMPLO,
                    "El que da el bot y sale en el recibo. Todavía es el de ejemplo.",
                    "/configuracion#negocio",
                ),
                Punto(
                    "Dirección del lavadero",
                    bool(recibo.direccion),
                    "El bot la dice cuando le preguntan dónde quedan y al dar un turno.",
                    "/configuracion#recibo",
                ),
                Punto(
                    "NIT y dirección en el recibo",
                    bool(recibo.nit and recibo.direccion),
                    "Salen en el recibo de cada lavado.",
                    "/configuracion#recibo",
                ),
                Punto(
                    "Menú, precios y puestos",
                    None,
                    "Revisa que los servicios, sus precios y los puestos sean los de verdad.",
                    "/configuracion#menu",
                ),
            ],
        ),
        Grupo(
            "Los dos negocios",
            [
                Punto(
                    "Correos de los jefes para el reporte del día",
                    bool(parqueadero.destinos_del_reporte and recibo.destinos_del_reporte),
                    "Al cerrar cada caja les llega el reporte del día.",
                    "/configuracion#recibo",
                ),
                Punto(
                    "Correo del sistema",
                    entorno.correo_configurado,
                    "Sin él, el reporte solo sale por WhatsApp (los datos CORREO_ van en Render).",
                ),
                Punto(
                    "Usuarios de los cajeros",
                    bool(cajeros),
                    "Cada persona con su usuario: así se sabe quién cobró y quién cerró.",
                    "/usuarios",
                    obligatorio=False,
                ),
            ],
        ),
        Grupo(
            "El servidor",
            [
                Punto(
                    "En modo producción",
                    entorno.es_produccion,
                    "ENTORNO=produccion en Render: exige una clave secreta segura.",
                ),
                Punto(
                    "WhatsApp conectado",
                    entorno.whatsapp_conectado,
                    "Los datos WHATSAPP_ de la cuenta nueva de Meta van en Render (README, "
                    "Conectar WhatsApp). Sin ellos, el lavadero funciona sin el bot.",
                    obligatorio=False,
                ),
                Punto(
                    "Gemini en plan de pago",
                    (not entorno.gemini_clave) or entorno.gemini_plan_de_pago,
                    "Con clientes reales, Gemini va en el plan de pago (GEMINI_PLAN_DE_PAGO=true).",
                ),
                Punto(
                    "Render en el plan de pago",
                    None,
                    "El plan gratis apaga el servidor y bloquea el correo: pásalo antes del 12 "
                    "de diciembre.",
                ),
                Punto(
                    "Copias de seguridad en verde",
                    None,
                    "En GitHub > Actions, la copia diaria debe salir en verde.",
                ),
                Punto(
                    "Impresora de tickets",
                    None,
                    "Predeterminada en Windows y Chrome con --kiosk-printing (README, Imprimir "
                    "los tickets).",
                ),
            ],
        ),
    ]


def pendientes(grupos: list[Grupo]) -> int:
    """Los puntos obligatorios que el sistema sabe que faltan."""
    return sum(1 for g in grupos for p in g.puntos if p.obligatorio and p.listo is False)
