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

# Decisión del negocio: por ahora todo dura 1 hora. Se ajusta con los tiempos reales.
DURACION_ESTIMADA_MIN = 60

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

# (fila, servicio, qué incluye, precio en pesos por tipo de vehículo)
SERVICIOS = (
    (
        "Carros",
        "Básico",
        "Lavado exterior a presión, champú, secado y brillo de llantas",
        {"Automóvil": 40_000, "Camioneta": 50_000},
    ),
    (
        "Carros",
        "Encerado",
        "Básico + cera protectora para la pintura",
        {"Automóvil": 60_000, "Camioneta": 70_000},
    ),
    (
        "Carros",
        "General",
        "Encerado + aspirado completo, tablero, puertas y vidrios por dentro",
        {"Automóvil": 120_000, "Camioneta": 140_000},
    ),
    (
        "Carros",
        "Premium",
        "General + limpieza de cojinería, lavado de motor e hidratación de plásticos",
        {"Automóvil": 160_000, "Camioneta": 220_000},
    ),
    (
        "Carros",
        "Empelotado",
        "Desmonte de sillas y alfombras, cojinería, techo, chasis, motor, encerado y pulido",
        {"Automóvil": 300_000, "Camioneta": 400_000},
    ),
    ("Motos", "Básico", "Lavado a presión, champú y secado", {"Moto": 18_000}),
    (
        "Motos",
        "Intermedio",
        "Básico + desengrase de motor y cadena, brillo de llantas",
        {"Moto": 25_000},
    ),
    (
        "Motos",
        "Premium",
        "Intermedio + lubricación de cadena, brillo de plásticos y cromados, protector",
        {"Moto": 35_000},
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
    ("hora_apertura", "05:30", "Desde esta hora el bot da turnos"),
    (
        "hora_cierre",
        "19:30",
        "Desde esta hora el bot deja de dar turnos; la fila que ya existe se atiende igual",
    ),
    ("dias_atencion", [1, 2, 3, 4, 5, 6, 7], "Días en que abre: 1 = lunes ... 7 = domingo"),
    ("gracia_minutos", 15, "Minutos que tiene un cliente llamado para llegar"),
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


def cargar_datos_iniciales(sesion: Session) -> dict[str, int]:
    """Carga los datos de Auto Ibaguer y devuelve cuántos registros nuevos creó por tipo."""
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

    for orden, (nombre_fila, nombre, descripcion, precios) in enumerate(SERVICIOS, start=1):
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
                {"precio": precio, "duracion_min": DURACION_ESTIMADA_MIN},
            )
            creados["precios"] += nuevo

    for clave, valor, descripcion in CONFIGURACION_INICIAL:
        _, nuevo = _obtener_o_crear(
            sesion, Configuracion, {"clave": clave}, {"valor": valor, "descripcion": descripcion}
        )
        creados["parametros"] += nuevo

    return creados
