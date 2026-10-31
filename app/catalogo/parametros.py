"""Los parámetros del negocio, leídos de la tabla `configuracion` y con su tipo correcto.

En la base cada valor es JSON ("05:30", 15, [1, 2, 3]...). Aquí se convierten a lo que
usan las reglas (una hora, minutos, un conjunto de días). Si a alguien se le borra una
clave, se usa el valor inicial del documento en vez de fallar.
"""

from dataclasses import dataclass
from datetime import time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalogo.datos_iniciales import CONFIGURACION_INICIAL
from app.catalogo.tablas import Configuracion

_VALORES_INICIALES: dict[str, Any] = {clave: valor for clave, valor, _ in CONFIGURACION_INICIAL}


@dataclass(frozen=True)
class Parametros:
    nombre_negocio: str
    telefono_contacto: str
    hora_apertura: time
    hora_cierre: time
    dias_atencion: frozenset[int]  # 1 = lunes ... 7 = domingo
    gracia: timedelta
    avisar_cuando_falten: int
    sesion_bot: timedelta
    servicio_por_defecto: str
    turnos_activos_por_telefono: int
    bot_activo: bool


def leer_parametros(sesion: Session) -> Parametros:
    guardados = {fila.clave: fila.valor for fila in sesion.scalars(select(Configuracion))}

    def valor(clave: str) -> Any:
        return guardados.get(clave, _VALORES_INICIALES[clave])

    return Parametros(
        nombre_negocio=str(valor("nombre_negocio")),
        telefono_contacto=str(valor("telefono_contacto")),
        hora_apertura=time.fromisoformat(valor("hora_apertura")),
        hora_cierre=time.fromisoformat(valor("hora_cierre")),
        dias_atencion=frozenset(int(dia) for dia in valor("dias_atencion")),
        gracia=timedelta(minutes=int(valor("gracia_minutos"))),
        avisar_cuando_falten=int(valor("avisar_cuando_falten")),
        sesion_bot=timedelta(minutes=int(valor("sesion_bot_minutos"))),
        servicio_por_defecto=str(valor("servicio_por_defecto")),
        turnos_activos_por_telefono=int(valor("turnos_activos_por_telefono")),
        bot_activo=bool(valor("bot_activo")),
    )
