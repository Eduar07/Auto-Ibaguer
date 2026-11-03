"""Cómo dice el bot cada aviso de la fila.

Los avisos los deja la fila en la bandeja de salida (app.turnos.casos_de_uso) solo con
sus datos: el número de turno, la hora límite, cuántos tiene adelante. El texto se arma
al momento de enviarlo, con los textos del bot.
"""

from datetime import datetime
from typing import Any

from app import tiempo
from app.bot import textos
from app.bot.mensajes import Mensaje, Opcion
from app.bot.textos import BOTONES, texto
from app.catalogo.parametros import Parametros
from app.formato import hora_con_articulo
from app.turnos.estados import TipoAviso


def mensaje_del_aviso(tipo: str, contenido: dict[str, Any], parametros: Parametros) -> Mensaje:
    turno = contenido.get("turno", "")
    adelante = int(contenido.get("adelante", 0))
    match TipoAviso(tipo):
        case TipoAviso.AVISO_ANTICIPADO:
            return Mensaje(
                texto("aviso_anticipado", turno=turno, adelante=textos.adelante(adelante))
            )
        case TipoAviso.LLAMADO:
            limite = tiempo.hora_local(datetime.fromisoformat(contenido["gracia_hasta"]))
            return Mensaje(
                texto("aviso_llamado", turno=turno, hora=hora_con_articulo(limite)),
                botones=textos.botones_del_llamado(),
            )
        case TipoAviso.PASA_AL_FINAL:
            # Si quedó cerca, este mismo aviso le pide salir ya (no se manda otro).
            cerca = adelante <= parametros.avisar_cuando_falten
            nombre = "aviso_pasa_al_final_cerca" if cerca else "aviso_pasa_al_final"
            return Mensaje(texto(nombre, turno=turno, adelante=textos.adelante(adelante)))
        case TipoAviso.NO_LLEGO:
            return Mensaje(
                texto("aviso_no_llego", turno=turno),
                botones=(Opcion("menu.pedir", BOTONES["pedir"]),),
            )
        case TipoAviso.VEHICULO_LISTO:
            return Mensaje(
                texto("aviso_vehiculo_listo", turno=turno, negocio=parametros.nombre_negocio)
            )
