"""Estados del turno y las demás listas cerradas de la fila.

Es la parte más pura del módulo: no sabe nada de la base de datos ni de WhatsApp.
Las tablas, las reglas y los casos de uso la comparten.
"""

from enum import StrEnum


class EstadoTurno(StrEnum):
    """Los estados posibles de un turno. La base de datos rechaza cualquier otro."""

    EN_FILA = "en_fila"
    LLAMADO = "llamado"
    LLEGO = "llego"
    EN_LAVADO = "en_lavado"
    LISTO = "listo"
    PAGADO = "pagado"
    CANCELADO = "cancelado"
    NO_LLEGO = "no_llego"


# Cómo se le muestra cada estado a la persona.
NOMBRE_DEL_ESTADO = {
    EstadoTurno.EN_FILA: "En fila",
    EstadoTurno.LLAMADO: "Llamado",
    EstadoTurno.LLEGO: "Llegó",
    EstadoTurno.EN_LAVADO: "En lavado",
    EstadoTurno.LISTO: "Listo",
    EstadoTurno.PAGADO: "Pagado",
    EstadoTurno.CANCELADO: "Cancelado",
    EstadoTurno.NO_LLEGO: "No llegó",
}

# Un turno en estos estados todavía está "vivo" en el lavadero.
ESTADOS_ACTIVOS = frozenset(
    {
        EstadoTurno.EN_FILA,
        EstadoTurno.LLAMADO,
        EstadoTurno.LLEGO,
        EstadoTurno.EN_LAVADO,
        EstadoTurno.LISTO,
    }
)

# Los que esperan puesto: todavía no entran a lavarse. Juntos forman la fila de espera.
ESTADOS_EN_ESPERA = frozenset({EstadoTurno.EN_FILA, EstadoTurno.LLAMADO, EstadoTurno.LLEGO})


class OrigenTurno(StrEnum):
    WHATSAPP = "whatsapp"
    DASHBOARD = "dashboard"


class Actor(StrEnum):
    """Quién hizo un cambio de estado."""

    CLIENTE = "cliente"
    ADMINISTRADOR = "administrador"
    SISTEMA = "sistema"


class MedioPago(StrEnum):
    EFECTIVO = "efectivo"
    NEQUI = "nequi"
    DAVIPLATA = "daviplata"
    TARJETA = "tarjeta"


class TipoAviso(StrEnum):
    """Los avisos que la fila le deja al cliente en la bandeja de salida de WhatsApp."""

    AVISO_ANTICIPADO = "aviso_anticipado"  # faltan 2 turnos: sal hacia el lavadero
    LLAMADO = "llamado"  # te toca: tienes hasta las 8:15 a.m.
    PASA_AL_FINAL = "pasa_al_final"  # no llegaste a tiempo: pasaste al final
    NO_LLEGO = "no_llego"  # segunda vez sin llegar: perdiste el turno
    VEHICULO_LISTO = "vehiculo_listo"  # ya puedes recogerlo y pagar en el local
