"""Cambiar la configuración del negocio desde el dashboard: parámetros, precios y servicios.

Todo se valida aquí antes de guardar, para que el bot y la fila nunca lean un valor
imposible (un horario que cierra antes de abrir, un tiempo de gracia de 0 minutos...).
Un precio nunca se edita encima: se apaga el viejo y se crea uno nuevo, así los cobros
pasados siguen mostrando lo que se cobró.
"""

import re
from collections.abc import Mapping
from datetime import datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.catalogo.datos_iniciales import CONFIGURACION_INICIAL
from app.catalogo.tablas import Configuracion, Servicio, ServicioPrecio, TipoVehiculo
from app.clientes.errores import TelefonoInvalido
from app.clientes.reglas import normalizar_telefono
from app.errores import ReglaDelNegocio

_DESCRIPCIONES = {clave: descripcion for clave, _, descripcion in CONFIGURACION_INICIAL}
_DIAS = {"1", "2", "3", "4", "5", "6", "7"}  # 1 = lunes ... 7 = domingo

# (mínimo, máximo) de cada número que el administrador puede cambiar.
LIMITES = {
    "gracia_minutos": (5, 60),
    "avisar_cuando_falten": (1, 5),
    "sesion_bot_minutos": (10, 120),
}
PRECIO_MAXIMO = 5_000_000
# El nombre de un modelo de Gemini: "gemini-3.5-flash-lite", "gemini-flash-latest"...
MODELO_GEMINI = re.compile(r"[a-z0-9][a-z0-9.\-]{2,59}")
DURACION_MINIMA, DURACION_MAXIMA = 5, 600


class ConfiguracionInvalida(ReglaDelNegocio):
    """Uno o más campos no se pueden guardar. `errores` dice cuál y por qué."""

    def __init__(self, errores: dict[str, str]) -> None:
        self.errores = errores
        super().__init__("No se guardó nada: revisa los campos marcados.")


def _hora(texto: str) -> time | None:
    try:
        return time.fromisoformat(texto.strip())
    except ValueError:
        return None


def _numero_entre(texto: str, minimo: int, maximo: int) -> int | None:
    texto = texto.strip()
    if not texto.isdigit() or not minimo <= int(texto) <= maximo:
        return None
    return int(texto)


def validar_parametros(formulario: Mapping[str, Any]) -> dict[str, Any]:
    """Convierte lo que llega del formulario a los valores que se guardan.

    `formulario` trae textos (como llegan del navegador), salvo `dias_atencion`, que es
    una lista, y `bot_activo`, que es verdadero o falso. Si algo no sirve, lanza
    ConfiguracionInvalida con todos los errores juntos, no solo el primero.
    """
    errores: dict[str, str] = {}
    limpios: dict[str, Any] = {}

    nombre = " ".join(str(formulario.get("nombre_negocio", "")).split())
    if not 2 <= len(nombre) <= 60:
        errores["nombre_negocio"] = "Escribe el nombre del lavadero (de 2 a 60 letras)."
    limpios["nombre_negocio"] = nombre

    try:
        limpios["telefono_contacto"] = normalizar_telefono(
            str(formulario.get("telefono_contacto", ""))
        )
    except TelefonoInvalido:
        errores["telefono_contacto"] = "Escribe un celular de 10 dígitos, por ejemplo 322 755 6850."

    apertura = _hora(str(formulario.get("hora_apertura", "")))
    cierre = _hora(str(formulario.get("hora_cierre", "")))
    if apertura is None:
        errores["hora_apertura"] = "Escribe la hora en que abre."
    if cierre is None:
        errores["hora_cierre"] = "Escribe la hora en que deja de dar turnos."
    if apertura and cierre and cierre <= apertura:
        errores["hora_cierre"] = "Debe ser después de la hora en que abre."
    if apertura and cierre:
        limpios["hora_apertura"] = apertura.strftime("%H:%M")
        limpios["hora_cierre"] = cierre.strftime("%H:%M")

    dias = sorted({int(dia) for dia in formulario.get("dias_atencion", []) if str(dia) in _DIAS})
    if not dias:
        errores["dias_atencion"] = "Elige al menos un día."
    limpios["dias_atencion"] = dias

    limpios["bot_activo"] = bool(formulario.get("bot_activo"))
    limpios["gemini_activo"] = bool(formulario.get("gemini_activo"))

    modelo = str(formulario.get("modelo_gemini", "")).strip().lower()
    if not MODELO_GEMINI.fullmatch(modelo):
        errores["modelo_gemini"] = (
            "Escribe el nombre del modelo como sale en Google AI Studio, por ejemplo "
            "gemini-3.5-flash-lite."
        )
    limpios["modelo_gemini"] = modelo

    for clave, (minimo, maximo) in LIMITES.items():
        valor = _numero_entre(str(formulario.get(clave, "")), minimo, maximo)
        if valor is None:
            errores[clave] = f"Escribe un número entre {minimo} y {maximo}."
        limpios[clave] = valor

    aviso = str(formulario.get("aviso_datos", "")).strip()
    aviso = re.sub(r"[ \t]+", " ", aviso)
    if not 20 <= len(aviso) <= 600:
        errores["aviso_datos"] = "El aviso debe tener entre 20 y 600 caracteres."
    limpios["aviso_datos"] = aviso

    if errores:
        raise ConfiguracionInvalida(errores)
    return limpios


def guardar_parametros(
    sesion: Session, formulario: Mapping[str, Any], *, ahora: datetime | None = None
) -> list[str]:
    """Valida y guarda los parámetros. Devuelve las claves que cambiaron."""
    ahora = ahora or tiempo.ahora()
    limpios = validar_parametros(formulario)
    guardados = {fila.clave: fila for fila in sesion.scalars(select(Configuracion))}
    cambiaron = []
    for clave, valor in limpios.items():
        fila = guardados.get(clave)
        if fila is None:
            sesion.add(
                Configuracion(
                    clave=clave,
                    valor=valor,
                    descripcion=_DESCRIPCIONES[clave],
                    actualizado_en=ahora,
                )
            )
            cambiaron.append(clave)
        elif fila.valor != valor:
            fila.valor = valor
            fila.actualizado_en = ahora
            cambiaron.append(clave)
    sesion.flush()
    return cambiaron


# --- Precios y servicios ----------------------------------------------------------------


def _precio_vigente(
    sesion: Session, servicio_id: int, tipo_vehiculo_id: int
) -> ServicioPrecio | None:
    return sesion.scalars(
        select(ServicioPrecio).where(
            ServicioPrecio.servicio_id == servicio_id,
            ServicioPrecio.tipo_vehiculo_id == tipo_vehiculo_id,
            ServicioPrecio.activo,
        )
    ).one_or_none()


def _validar_precio(precio: int | None, duracion_min: int | None) -> None:
    if precio is None or not 0 <= precio <= PRECIO_MAXIMO:
        raise ConfiguracionInvalida({"precio": "Escribe un precio en pesos, por ejemplo 40.000."})
    if duracion_min is None or not DURACION_MINIMA <= duracion_min <= DURACION_MAXIMA:
        raise ConfiguracionInvalida(
            {"duracion": f"La duración va de {DURACION_MINIMA} a {DURACION_MAXIMA} minutos."}
        )


def cambiar_precio(
    sesion: Session,
    servicio_id: int,
    tipo_vehiculo_id: int,
    *,
    precio: int | None,
    duracion_min: int | None,
) -> bool:
    """Cambia el precio o la duración de un servicio para un tipo de vehículo.

    El precio viejo queda inactivo y se crea uno nuevo: aplica a los cobros de ahora en
    adelante. Devuelve si hubo cambio.
    """
    _validar_precio(precio, duracion_min)
    actual = _precio_vigente(sesion, servicio_id, tipo_vehiculo_id)
    if actual is not None:
        if actual.precio == precio and actual.duracion_min == duracion_min:
            return False
        actual.activo = False
        sesion.flush()  # primero se apaga el viejo: solo puede haber un precio vigente
    sesion.add(
        ServicioPrecio(
            servicio_id=servicio_id,
            tipo_vehiculo_id=tipo_vehiculo_id,
            precio=precio,
            duracion_min=duracion_min,
        )
    )
    sesion.flush()
    return True


def agregar_servicio(
    sesion: Session,
    *,
    fila_id: int,
    nombre: str,
    descripcion: str,
    precios: Mapping[int, int | None],
    duracion_min: int | None,
) -> Servicio:
    """Un servicio nuevo en el menú de una fila, con su precio para cada tipo de vehículo."""
    nombre = " ".join(nombre.split())
    nombre = nombre[:1].upper() + nombre[1:]
    if not 2 <= len(nombre) <= 40:
        raise ConfiguracionInvalida(
            {"nombre": "Escribe el nombre del servicio (de 2 a 40 letras)."}
        )
    repetido = sesion.scalar(
        select(Servicio.id).where(
            Servicio.fila_id == fila_id, func.lower(Servicio.nombre) == nombre.lower()
        )
    )
    if repetido is not None:
        raise ConfiguracionInvalida({"nombre": f"Ya hay un servicio que se llama {nombre}."})
    tipos = list(sesion.scalars(select(TipoVehiculo).where(TipoVehiculo.fila_id == fila_id)))
    for tipo in tipos:
        _validar_precio(precios.get(tipo.id), duracion_min)
    orden = sesion.scalar(select(func.max(Servicio.orden)).where(Servicio.fila_id == fila_id))
    servicio = Servicio(
        fila_id=fila_id,
        nombre=nombre,
        descripcion=" ".join(descripcion.split()),
        orden=(orden or 0) + 1,
    )
    sesion.add(servicio)
    sesion.flush()
    for tipo in tipos:
        sesion.add(
            ServicioPrecio(
                servicio_id=servicio.id,
                tipo_vehiculo_id=tipo.id,
                precio=precios[tipo.id],
                duracion_min=duracion_min,
            )
        )
    sesion.flush()
    return servicio
