"""Datos iniciales de Auto Ibaguer: filas, puestos, menú, precios y parámetros.

Se pueden cargar varias veces sin problema: solo agregan lo que falta y nunca
sobrescriben lo que el administrador ya cambió desde el dashboard.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalogo.tablas import (
    Configuracion,
    Fila,
    Puesto,
    Servicio,
    ServicioPrecio,
    TipoVehiculo,
)
from app.db.base import Base

# Decisión del negocio: cada servicio dura unos 45 minutos. Se ajusta en Configuración.
DURACION_ESTIMADA_MIN = 45

PLACA_CARRO = r"^[A-Z]{3}[0-9]{3}$"  # ABC123
PLACA_MOTO = r"^[A-Z]{3}[0-9]{2}[A-Z]$"  # ABC12D

# (fila, prefijo del turno, puestos)
FILAS = (
    ("Carros", "C", ("C1", "C2")),
    ("Motos", "M", ("M1", "M2", "M3", "M4")),
)

# (tipo de vehículo, fila a la que va, formato de placa)
TIPOS_DE_VEHICULO = (
    ("Automóvil", "Carros", PLACA_CARRO),
    ("Camioneta", "Carros", PLACA_CARRO),
    ("Moto", "Motos", PLACA_MOTO),
)

# (fila, servicio, qué incluye, precio en pesos por tipo de vehículo). Por ahora el
# lavadero ofrece dos servicios; los demás se agregan en Configuración cuando crezca.
SERVICIOS = (
    (
        "Carros",
        "Básico",
        "Agua, champú Simoniz, aspirado por dentro, tapetes y llantina",
        {"Automóvil": 30_000, "Camioneta": 40_000},
    ),
    (
        "Carros",
        "Encerado",
        "Básico + encerado con polichadora: cera 3M o Meguiar's, pasta para pulir 3M y "
        "Morita de B-Max, que humecta, restaura y da brillo a las partes negras",
        {"Automóvil": 60_000, "Camioneta": 70_000},
    ),
    ("Motos", "Básico", "Agua, champú Simoniz y llantina", {"Moto": 15_000}),
    (
        "Motos",
        "Encerado",
        "Básico + encerado a mano con cera 3M o Meguiar's (sin polichadora)",
        {"Moto": 20_000},
    ),
)

# (clave, valor inicial, para qué sirve)
CONFIGURACION_INICIAL: tuple[tuple[str, Any, str], ...] = (
    ("nombre_negocio", "Auto Ibaguer", "Nombre con el que saluda el bot"),
    (
        "telefono_contacto",
        "573227556850",
        "Teléfono que da el bot cuando no puede resolver algo",
    ),
    ("hora_apertura", "06:30", "Desde esta hora el bot da turnos"),
    (
        "hora_cierre",
        "00:00",
        "Desde esta hora el bot deja de dar turnos (00:00 es a las 12 de la noche); la fila "
        "que ya existe se atiende igual",
    ),
    ("dias_atencion", [1, 2, 3, 4, 5, 6, 7], "Días en que abre: 1 = lunes ... 7 = domingo"),
    ("gracia_minutos", 15, "Minutos que tiene un cliente llamado para llegar"),
    (
        "minutos_entre_horarios",
        60,
        "Cada cuántos minutos hay un horario para reservar, desde la hora en que abre",
    ),
    ("dias_para_reservar", 7, "Con cuántos días de anticipación se puede reservar"),
    (
        "recordar_reserva_minutos",
        60,
        "Cuántos minutos antes se le recuerda la reserva (si WhatsApp lo deja: escribió en "
        "las últimas 24 horas)",
    ),
    (
        "avisar_cuando_falten",
        2,
        "Turnos que faltan cuando el bot le avisa al cliente que salga hacia el lavadero",
    ),
    ("sesion_bot_minutos", 30, "Minutos sin respuesta para que el bot vuelva al menú"),
    (
        "servicio_por_defecto",
        "Básico",
        "Servicio que se asume para calcular la espera cuando está por definir",
    ),
    (
        "turnos_activos_por_telefono",
        1,
        "Turnos activos que puede pedir un mismo teléfono por WhatsApp en cada fila",
    ),
    ("bot_activo", True, "Si está apagado, el bot avisa que hoy no hay turnos"),
    (
        "gemini_activo",
        True,
        "Si está apagado, el bot entiende solo los botones y unas pocas palabras",
    ),
    (
        "bot_ve_fotos",
        False,
        "Si está encendido, Gemini mira las fotos que mandan los clientes; si no, el bot "
        "les pide que lo escriban (los audios los oye siempre que Gemini esté)",
    ),
    (
        "modelo_gemini",
        "gemini-3.5-flash-lite",
        "Modelo de Gemini que entiende lo que escriben los clientes",
    ),
    (
        "ficha_negocio",
        "",
        "Lo que el bot puede contarles a los clientes del negocio (dirección, pagos...): "
        "Gemini contesta las preguntas solo con esto y con el horario del sistema",
    ),
    (
        "aviso_datos",
        "Para darte turno guardamos tu nombre, tu celular y la placa de tu vehículo. Los "
        "usamos solo para atenderte y avisarte. Puedes pedir que los borremos cuando "
        "quieras. ¿Aceptas?",
        "Aviso de tratamiento de datos (Ley 1581) que el bot le muestra a un cliente nuevo",
    ),
)


def _obtener_o_crear[M: Base](
    sesion: Session, modelo: type[M], buscar: dict[str, Any], crear: dict[str, Any]
) -> tuple[M, bool]:
    """Busca un registro; si no existe, lo crea. Devuelve (registro, ¿se creó?)."""
    existente = sesion.scalars(select(modelo).filter_by(**buscar)).one_or_none()
    if existente is not None:
        return existente, False
    nuevo = modelo(**buscar, **crear)
    sesion.add(nuevo)
    sesion.flush()  # para que tenga id y los siguientes registros lo puedan usar
    return nuevo, True


def cargar_datos_iniciales(
    sesion: Session,
    *,
    servicios: tuple[tuple[str, str, str, dict[str, int]], ...] = SERVICIOS,
    duracion: int = DURACION_ESTIMADA_MIN,
    configuracion: tuple[tuple[str, Any, str], ...] | None = None,
) -> dict[str, int]:
    """Carga los datos de Auto Ibaguer y devuelve cuántos registros nuevos creó por tipo.
    Las pruebas pasan su propio menú (`servicios`, `duracion`) y su horario
    (`configuracion`)."""
    creados = dict.fromkeys(
        ("filas", "puestos", "tipos_de_vehiculo", "servicios", "precios", "parametros"), 0
    )

    filas: dict[str, Fila] = {}
    for nombre, prefijo, puestos in FILAS:
        fila, nueva = _obtener_o_crear(sesion, Fila, {"nombre": nombre}, {"prefijo": prefijo})
        filas[nombre] = fila
        creados["filas"] += nueva
        for nombre_puesto in puestos:
            _, nuevo = _obtener_o_crear(
                sesion, Puesto, {"nombre": nombre_puesto}, {"fila_id": fila.id}
            )
            creados["puestos"] += nuevo

    tipos: dict[str, TipoVehiculo] = {}
    for nombre, nombre_fila, patron in TIPOS_DE_VEHICULO:
        tipo, nuevo = _obtener_o_crear(
            sesion,
            TipoVehiculo,
            {"nombre": nombre},
            {"fila_id": filas[nombre_fila].id, "patron_placa": patron},
        )
        tipos[nombre] = tipo
        creados["tipos_de_vehiculo"] += nuevo

    for orden, (nombre_fila, nombre, descripcion, precios) in enumerate(servicios, start=1):
        servicio, nuevo = _obtener_o_crear(
            sesion,
            Servicio,
            {"fila_id": filas[nombre_fila].id, "nombre": nombre},
            {"descripcion": descripcion, "orden": orden},
        )
        creados["servicios"] += nuevo
        for nombre_tipo, precio in precios.items():
            _, nuevo = _obtener_o_crear(
                sesion,
                ServicioPrecio,
                {
                    "servicio_id": servicio.id,
                    "tipo_vehiculo_id": tipos[nombre_tipo].id,
                    "activo": True,
                },
                {"precio": precio, "duracion_min": duracion},
            )
            creados["precios"] += nuevo

    for clave, valor, descripcion in configuracion or CONFIGURACION_INICIAL:
        _, nuevo = _obtener_o_crear(
            sesion, Configuracion, {"clave": clave}, {"valor": valor, "descripcion": descripcion}
        )
        creados["parametros"] += nuevo

    return creados
