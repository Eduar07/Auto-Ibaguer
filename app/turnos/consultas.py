"""Lo que el bot y el dashboard preguntan sobre la fila. Solo lee: no cambia nada.

El bot nunca muestra nombres ni placas de otros clientes: por eso el resumen de la
fila solo trae cantidades.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app import tiempo
from app.catalogo.parametros import Parametros, leer_parametros
from app.catalogo.tablas import Fila
from app.clientes.tablas import Vehiculo
from app.turnos import datos, reglas
from app.turnos.errores import TurnoNoExiste
from app.turnos.estados import EstadoTurno
from app.turnos.tablas import Turno


@dataclass(frozen=True)
class FotoDeLaFila:
    """La fila en un momento: quién espera, cuántos puestos hay y cuándo le toca a cada uno."""

    fila: Fila
    espera: list[Turno]
    puestos_activos: int
    puestos_libres: int
    estimacion: reglas.Estimacion

    def adelante_de(self, turno_id: int) -> int | None:
        """Cuántos turnos tiene adelante. None si ya no está esperando puesto."""
        for adelante, turno in enumerate(self.espera):
            if turno.id == turno_id:
                return adelante
        return None

    def inicio_de(self, turno_id: int) -> datetime | None:
        """Cuándo entraría a un puesto, según la estimación."""
        adelante = self.adelante_de(turno_id)
        if adelante is None or adelante >= len(self.estimacion.inicios):
            return None
        return self.estimacion.inicios[adelante]


def foto_de_la_fila(
    sesion: Session, fila_id: int, ahora: datetime, parametros: Parametros
) -> FotoDeLaFila:
    fila = sesion.get(Fila, fila_id)
    espera = datos.en_espera(sesion, fila_id)
    puestos = datos.puestos_activos(sesion, fila_id)
    lavando = {turno.puesto_id: turno for turno in datos.en_lavado(sesion, fila_id)}
    duraciones = datos.Duraciones(sesion, fila_id, parametros.servicio_por_defecto)
    tipos = datos.tipos_de_vehiculo(
        sesion, [turno.vehiculo_id for turno in [*espera, *lavando.values()]]
    )

    def duracion(turno: Turno):
        return duraciones.de(turno.servicio_id, tipos[turno.vehiculo_id])

    libres_desde = []
    for puesto in puestos:
        turno = lavando.get(puesto.id)
        if turno is None:
            libres_desde.append(ahora)
        else:
            libres_desde.append((turno.en_lavado_desde or ahora) + duracion(turno))

    return FotoDeLaFila(
        fila=fila,
        espera=espera,
        puestos_activos=len(puestos),
        puestos_libres=sum(1 for puesto in puestos if puesto.id not in lavando),
        estimacion=reglas.estimar_fila(libres_desde, [duracion(t) for t in espera], ahora),
    )


@dataclass(frozen=True)
class ResumenDeLaFila:
    """Para "Ver cómo está la fila"."""

    fila: str
    puestos_activos: int
    puestos_libres: int
    en_espera: int
    espera_min: int | None  # cuánto esperaría un turno nuevo

    @property
    def puede_venir_ya(self) -> bool:
        """Hay un puesto libre que nadie de la fila va a ocupar."""
        return self.puestos_libres > self.en_espera


def resumen_de_la_fila(
    sesion: Session, fila_id: int, *, ahora: datetime | None = None
) -> ResumenDeLaFila:
    ahora = ahora or tiempo.ahora()
    foto = foto_de_la_fila(sesion, fila_id, ahora, leer_parametros(sesion))
    return ResumenDeLaFila(
        fila=foto.fila.nombre,
        puestos_activos=foto.puestos_activos,
        puestos_libres=foto.puestos_libres,
        en_espera=len(foto.espera),
        espera_min=reglas.minutos_hasta(foto.estimacion.siguiente, ahora),
    )


@dataclass(frozen=True)
class PosicionDelTurno:
    """Para "mi turno" en el bot y para la fila del dashboard."""

    turno_id: int
    numero: str
    fila: str
    estado: EstadoTurno
    adelante: int | None  # None si ya no está esperando puesto
    espera_min: int | None
    gracia_hasta: datetime | None  # hora límite si está Llamado
    en_camino: bool
    placa_pendiente: bool
    servicio_por_definir: bool


def _posicion(sesion: Session, turno: Turno, foto: FotoDeLaFila, ahora: datetime):
    vehiculo = sesion.get(Vehiculo, turno.vehiculo_id)
    return PosicionDelTurno(
        turno_id=turno.id,
        numero=reglas.numero_de_turno(foto.fila.prefijo, turno.consecutivo),
        fila=foto.fila.nombre,
        estado=turno.estado,
        adelante=foto.adelante_de(turno.id),
        espera_min=reglas.minutos_hasta(foto.inicio_de(turno.id), ahora),
        gracia_hasta=turno.gracia_hasta if turno.estado == EstadoTurno.LLAMADO else None,
        en_camino=turno.en_camino,
        placa_pendiente=vehiculo.placa is None,
        servicio_por_definir=turno.servicio_id is None,
    )


def posicion_del_turno(
    sesion: Session, turno_id: int, *, ahora: datetime | None = None
) -> PosicionDelTurno:
    ahora = ahora or tiempo.ahora()
    turno = sesion.get(Turno, turno_id)
    if turno is None:
        raise TurnoNoExiste(turno_id)
    foto = foto_de_la_fila(sesion, turno.fila_id, ahora, leer_parametros(sesion))
    return _posicion(sesion, turno, foto, ahora)


def turnos_activos_del_cliente(
    sesion: Session, cliente_id: int, *, ahora: datetime | None = None
) -> list[PosicionDelTurno]:
    """Para "mi turno": puede tener uno de carros y uno de motos."""
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    fotos: dict[int, FotoDeLaFila] = {}
    posiciones = []
    for turno in datos.activos_del_cliente(sesion, cliente_id):
        if turno.fila_id not in fotos:
            fotos[turno.fila_id] = foto_de_la_fila(sesion, turno.fila_id, ahora, parametros)
        posiciones.append(_posicion(sesion, turno, fotos[turno.fila_id], ahora))
    return posiciones


def siguiente_para_puesto(sesion: Session, fila_id: int) -> int | None:
    """El turno que debería entrar al próximo puesto libre (el puesto no espera)."""
    espera = datos.en_espera(sesion, fila_id)
    return reglas.elegir_siguiente_para_puesto(
        [reglas.TurnoEnEspera(t.id, t.estado, t.aviso_anticipado_en is not None) for t in espera]
    )
