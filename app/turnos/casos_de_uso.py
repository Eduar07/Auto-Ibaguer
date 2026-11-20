"""Casos de uso de la fila: lo que pasa cuando el cliente, el administrador o el reloj
hacen algo con un turno.

Cada función recibe una sesión abierta y deja los cambios listos, sin confirmarlos:
quien la llama (el bot, el dashboard o el reloj) hace el commit. Así el cambio de
estado, su historial y sus avisos quedan guardados juntos, o no queda ninguno.

Después de cada cambio, `_revisar_fila` aplica las reglas de llamados: llama a quien le
toca un puesto libre y le pide salir hacia el lavadero a quien le faltan 2 turnos.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.caja import casos_de_uso as cajas
from app.caja.errores import CobroNoCorregible, ValorInvalido
from app.caja.estados import TipoCorreccion
from app.caja.reglas import VALOR_MAXIMO_DE_UN_COBRO, como_se_pago, sin_ceros_de_mas
from app.caja.tablas import CorreccionCobro
from app.catalogo.parametros import Parametros, leer_parametros
from app.catalogo.tablas import Fila, Puesto, Servicio, TipoVehiculo
from app.clientes import servicio as clientes
from app.clientes.errores import NoExiste
from app.clientes.tablas import Cliente, Vehiculo
from app.formato import cuando_sera, hora, hora_con_articulo, pesos
from app.integraciones import bandeja_salida
from app.integraciones.tablas import MensajeEntrante
from app.turnos import caja as caja_del_lavadero
from app.turnos import consultas, datos, reglas
from app.turnos.errores import (
    CambioNoPermitido,
    FaltaPlaca,
    FaltaServicio,
    FueraDeHorario,
    HorarioNoDisponible,
    PuestoNoDisponible,
    PuestoOcupado,
    ServicioNoDisponible,
    VehiculoDeOtraFila,
    YaTieneTurnoActivo,
)
from app.turnos.estados import (
    ESTADOS_ACTIVOS,
    NOMBRE_DEL_ESTADO,
    Actor,
    EstadoTurno,
    MedioPago,
    OrigenTurno,
    TipoAviso,
)
from app.turnos.tablas import Pago, Turno, TurnoHistorial

# --- Piezas internas -------------------------------------------------------------------


def _numero(sesion: Session, turno: Turno) -> str:
    fila = sesion.get(Fila, turno.fila_id)
    return reglas.numero_de_turno(fila.prefijo, turno.consecutivo)


def _registrar(
    sesion: Session,
    turno: Turno,
    anterior: EstadoTurno | None,
    nuevo: EstadoTurno,
    *,
    actor: Actor,
    ahora: datetime,
    administrador_id: int | None = None,
    detalle: str | None = None,
) -> None:
    """Anota el cambio en el historial, que nunca se borra."""
    sesion.add(
        TurnoHistorial(
            turno_id=turno.id,
            estado_anterior=anterior,
            estado_nuevo=nuevo,
            actor=actor,
            administrador_id=administrador_id,
            detalle=detalle,
            fecha_hora=ahora,
        )
    )


def _por_que_no(actual: EstadoTurno, nuevo: EstadoTurno, actor: Actor) -> str:
    de, a = NOMBRE_DEL_ESTADO[actual], NOMBRE_DEL_ESTADO[nuevo]
    if (actual, nuevo) in reglas.TRANSICIONES and actor == Actor.CLIENTE:
        return (
            f"Por WhatsApp no se puede pasar un turno de «{de}» a «{a}»; "
            "eso se resuelve en el local."
        )
    return f"Un turno en «{de}» no puede pasar a «{a}»."


def _cambiar_estado(
    sesion: Session,
    turno: Turno,
    nuevo: EstadoTurno,
    *,
    actor: Actor,
    ahora: datetime,
    administrador_id: int | None = None,
    detalle: str | None = None,
) -> None:
    if not reglas.puede_cambiar(turno.estado, nuevo, actor):
        raise CambioNoPermitido(_por_que_no(turno.estado, nuevo, actor))
    _registrar(
        sesion,
        turno,
        turno.estado,
        nuevo,
        actor=actor,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=detalle,
    )
    turno.estado = nuevo


def _avisar(sesion: Session, turno: Turno, tipo: TipoAviso, ahora: datetime, **detalles) -> None:
    """Deja el aviso en la bandeja de salida. Sin celular no hay a quién avisarle."""
    cliente = sesion.get(Cliente, turno.cliente_id)
    if cliente is None or cliente.telefono is None:
        return
    bandeja_salida.encolar(
        sesion,
        telefono=cliente.telefono,
        tipo=tipo.value,
        contenido={"turno": _numero(sesion, turno), **detalles},
        ahora=ahora,
        cliente_id=cliente.id,
        turno_id=turno.id,
    )


def _revisar_fila(
    sesion: Session,
    fila: Fila,
    ahora: datetime,
    parametros: Parametros,
    *,
    sin_mensaje_para: int | None = None,
) -> None:
    """Las reglas de llamados, después de cualquier cambio en la fila.

    Llama a quien le toca un puesto libre (empieza su tiempo de gracia) y le pide salir
    hacia el lavadero a quien le faltan pocos turnos. `sin_mensaje_para` es el turno que
    se acaba de pedir por WhatsApp: la confirmación del bot ya le dice todo, así que no
    se le manda un aviso aparte.
    """
    foto = consultas.foto_de_la_fila(sesion, fila.id, ahora, parametros)
    espera = foto.espera
    decision = reglas.decidir_llamados(
        [
            reglas.TurnoEnEspera(t.id, t.estado, t.aviso_anticipado_en is not None, t.prioridad)
            for t in espera
        ],
        foto.puestos_libres,
        parametros.avisar_cuando_falten,
        guardados=foto.guardados,
    )
    por_id = {turno.id: turno for turno in espera}
    for turno_id in decision.llamar:
        turno = por_id[turno_id]
        turno.llamado_en = ahora
        turno.gracia_hasta = ahora + parametros.gracia
        _cambiar_estado(
            sesion,
            turno,
            EstadoTurno.LLAMADO,
            actor=Actor.SISTEMA,
            ahora=ahora,
            detalle=f"Tiene hasta {hora_con_articulo(tiempo.hora_local(turno.gracia_hasta))}",
        )
        if turno_id != sin_mensaje_para:
            _avisar(
                sesion,
                turno,
                TipoAviso.LLAMADO,
                ahora,
                gracia_hasta=turno.gracia_hasta.isoformat(),
            )
    for turno_id, adelante in decision.avisar:
        turno = por_id[turno_id]
        turno.aviso_anticipado_en = ahora
        if turno_id != sin_mensaje_para:
            _avisar(sesion, turno, TipoAviso.AVISO_ANTICIPADO, ahora, adelante=adelante)
    sesion.flush()


def _servicio_valido(
    sesion: Session, servicio_id: int, fila_id: int, tipo_vehiculo_id: int
) -> Servicio:
    servicio = sesion.get(Servicio, servicio_id)
    if (
        servicio is None
        or not servicio.activo
        or servicio.fila_id != fila_id
        or datos.precio_vigente(sesion, servicio_id, tipo_vehiculo_id) is None
    ):
        raise ServicioNoDisponible("Ese servicio no está disponible para este vehículo.")
    return servicio


def _poner_placa(sesion: Session, turno: Turno, vehiculo: Vehiculo, placa: str) -> Vehiculo:
    destino = clientes.vehiculo_con_placa(sesion, vehiculo, placa)
    if destino.id != turno.vehiculo_id:
        tipo = sesion.get(TipoVehiculo, destino.tipo_vehiculo_id)
        if tipo.fila_id != turno.fila_id:
            raise VehiculoDeOtraFila(
                f"La placa {destino.placa} es de un vehículo {tipo.nombre.lower()} del cliente: "
                "va en la otra fila."
            )
        turno.vehiculo_id = destino.id
    return destino


# --- Pedir turno -----------------------------------------------------------------------


@dataclass(frozen=True)
class TurnoNuevo:
    """Lo que el bot necesita para confirmar el turno."""

    turno: Turno
    numero: str
    adelante: int
    espera_min: int | None
    # Había un puesto libre: se llamó de una vez y tiene hasta `gracia_hasta` para llegar.
    llamado: bool
    gracia_hasta: datetime | None
    # Está cerca: la confirmación le pide que salga ya hacia el lavadero.
    salir_ya: bool


def pedir_turno(
    sesion: Session,
    *,
    vehiculo_id: int,
    origen: OrigenTurno,
    servicio_id: int | None = None,
    administrador_id: int | None = None,
    en_el_local: bool | None = None,
    ahora: datetime | None = None,
) -> TurnoNuevo:
    """Da un turno nuevo, al final de la fila que le corresponde al vehículo.

    Por WhatsApp: solo dentro del horario y máximo un turno activo por teléfono en cada
    fila. Desde el dashboard, el administrador no tiene esos límites.

    El turno nace En fila. Si el vehículo ya está en el local (lo normal al crearlo desde
    el dashboard), nace como Llegó. Si hay un puesto libre para él, se llama de una vez.
    `servicio_id` vacío = servicio por definir.
    """
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    vehiculo = sesion.get(Vehiculo, vehiculo_id)
    if vehiculo is None:
        raise NoExiste(f"No existe el vehículo {vehiculo_id}.")
    tipo = sesion.get(TipoVehiculo, vehiculo.tipo_vehiculo_id)
    fila = datos.bloquear_fila(sesion, tipo.fila_id)
    por_whatsapp = origen == OrigenTurno.WHATSAPP

    if por_whatsapp:
        abierto = reglas.esta_en_horario(
            tiempo.hora_local(ahora),
            parametros.hora_apertura,
            parametros.hora_cierre,
            parametros.dias_atencion,
        )
        if not abierto:
            raise FueraDeHorario(
                f"Los turnos por WhatsApp se dan de {hora(parametros.hora_apertura)} "
                f"a {hora(parametros.hora_cierre)}."
            )
        activos = datos.activos_del_cliente(sesion, vehiculo.cliente_id, fila.id)
        if len(activos) >= parametros.turnos_activos_por_telefono:
            primero = activos[0]
            raise YaTieneTurnoActivo(
                primero.id, reglas.numero_de_turno(fila.prefijo, primero.consecutivo)
            )
    if servicio_id is not None:
        _servicio_valido(sesion, servicio_id, fila.id, tipo.id)

    fecha = tiempo.hora_local(ahora).date()
    ultimo = sesion.scalar(
        select(func.max(Turno.consecutivo)).where(Turno.fila_id == fila.id, Turno.fecha == fecha)
    )
    consecutivo = (ultimo or 0) + 1
    llego = (not por_whatsapp) if en_el_local is None else en_el_local
    turno = Turno(
        fila_id=fila.id,
        fecha=fecha,
        consecutivo=consecutivo,
        cliente_id=vehiculo.cliente_id,
        vehiculo_id=vehiculo.id,
        servicio_id=servicio_id,
        estado=EstadoTurno.LLEGO if llego else EstadoTurno.EN_FILA,
        origen=origen,
        en_fila_desde=ahora,
    )
    sesion.add(turno)
    sesion.flush()
    numero = reglas.numero_de_turno(fila.prefijo, consecutivo)
    _registrar(
        sesion,
        turno,
        None,
        turno.estado,
        actor=Actor.CLIENTE if por_whatsapp else Actor.ADMINISTRADOR,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=f"Turno {numero}",
    )

    adelante = [t.id for t in datos.en_espera(sesion, fila.id)].index(turno.id)
    salir_ya = por_whatsapp and adelante <= parametros.avisar_cuando_falten
    if salir_ya:
        turno.aviso_anticipado_en = ahora
    _revisar_fila(
        sesion, fila, ahora, parametros, sin_mensaje_para=turno.id if por_whatsapp else None
    )

    foto = consultas.foto_de_la_fila(sesion, fila.id, ahora, parametros)
    llamado = turno.estado == EstadoTurno.LLAMADO
    return TurnoNuevo(
        turno=turno,
        numero=numero,
        adelante=adelante,
        espera_min=reglas.minutos_hasta(foto.inicio_de(turno.id), ahora),
        llamado=llamado,
        gracia_hasta=turno.gracia_hasta if llamado else None,
        salir_ya=salir_ya and not llamado,
    )


# --- Mientras espera -------------------------------------------------------------------


def marcar_en_camino(sesion: Session, turno_id: int) -> Turno:
    """El cliente avisó "En camino".

    Es una marca informativa sobre un turno En fila o Llamado: no es un estado y no
    mueve la fila.
    """
    turno = datos.bloquear_turno(sesion, turno_id)
    if turno.estado not in (EstadoTurno.EN_FILA, EstadoTurno.LLAMADO):
        raise CambioNoPermitido("Solo un turno En fila o Llamado puede ir en camino.")
    turno.en_camino = True
    sesion.flush()
    return turno


def marcar_llegada(
    sesion: Session,
    turno_id: int,
    *,
    actor: Actor,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Turno:
    """ "Ya estoy aquí": el vehículo está en el local. Puede llegar antes de que lo llamen.

    Si lo escribe dos veces, la segunda no hace nada.
    """
    ahora = ahora or tiempo.ahora()
    turno = datos.bloquear_turno(sesion, turno_id)
    if turno.estado == EstadoTurno.LLEGO:
        return turno
    reservado = turno.estado == EstadoTurno.RESERVADO
    _cambiar_estado(
        sesion,
        turno,
        EstadoTurno.LLEGO,
        actor=actor,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle="Llegó antes de la hora de su reserva" if reservado else None,
    )
    turno.en_camino = False
    if reservado:
        # Entra a la fila desde que llegó; a la hora de su reserva pasa de primero.
        turno.en_fila_desde = ahora
    sesion.flush()
    if reservado:
        _revisar_fila(sesion, sesion.get(Fila, turno.fila_id), ahora, leer_parametros(sesion))
    return turno


def cancelar(
    sesion: Session,
    turno_id: int,
    *,
    actor: Actor,
    administrador_id: int | None = None,
    detalle: str | None = None,
    ahora: datetime | None = None,
) -> Turno:
    """Cancela un turno, sin penalidad. La fila avanza y se llama al siguiente de inmediato.

    Por WhatsApp solo se cancela En fila o Llamado; el administrador también puede
    cancelar a quien ya llegó y se fue sin lavar.
    """
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    turno = datos.bloquear_turno(sesion, turno_id)
    _cambiar_estado(
        sesion,
        turno,
        EstadoTurno.CANCELADO,
        actor=actor,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=detalle,
    )
    sesion.flush()
    _revisar_fila(sesion, sesion.get(Fila, turno.fila_id), ahora, parametros)
    return turno


@dataclass(frozen=True)
class GraciaVencida:
    turno_id: int
    numero: str
    estado: EstadoTurno  # En fila (pasó al final) o No llegó


def vencer_gracias(sesion: Session, *, ahora: datetime | None = None) -> list[GraciaVencida]:
    """Lo que hará el reloj cada 30 segundos con los llamados que no llegaron a tiempo.

    Primera vez: vuelve a la fila, al final, y se le avisa cuántos tiene adelante (si
    quedó cerca, ese mismo aviso le pide salir ya). Segunda vez: queda en No llegó.
    Después la fila avanza y se llama al siguiente.
    """
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    filas_con_vencidos = select(Turno.fila_id).where(
        Turno.estado == EstadoTurno.LLAMADO, Turno.gracia_hasta <= ahora
    )
    resultados = []
    for fila_id in sorted(set(sesion.scalars(filas_con_vencidos))):
        fila = datos.bloquear_fila(sesion, fila_id)
        vencidos = (
            select(Turno)
            .where(
                Turno.fila_id == fila_id,
                Turno.estado == EstadoTurno.LLAMADO,
                Turno.gracia_hasta <= ahora,
            )
            .order_by(Turno.gracia_hasta, Turno.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        al_final = []
        for turno in sesion.scalars(vencidos).all():
            nuevo = reglas.al_vencer_la_gracia(turno.intentos_sin_llegar)
            if nuevo == EstadoTurno.EN_FILA:
                turno.intentos_sin_llegar = 1
                turno.en_fila_desde = ahora
                turno.llamado_en = None
                turno.gracia_hasta = None
                turno.en_camino = False
                turno.aviso_anticipado_en = None
                detalle = "No llegó a tiempo: pasa al final de la fila"
                if turno.hora_reservada is not None:
                    # Pierde la reserva: queda en la fila como cualquiera.
                    detalle = "No llegó a la hora de su reserva: la pierde y pasa al final"
                    turno.hora_reservada = None
                    turno.prioridad = False
                al_final.append(turno)
            else:
                turno.intentos_sin_llegar = 2
                detalle = "No llegó a tiempo por segunda vez: pierde el turno"
            _cambiar_estado(sesion, turno, nuevo, actor=Actor.SISTEMA, ahora=ahora, detalle=detalle)
            if nuevo == EstadoTurno.NO_LLEGO:
                _avisar(sesion, turno, TipoAviso.NO_LLEGO, ahora)
            resultados.append(GraciaVencida(turno.id, _numero(sesion, turno), nuevo))
        sesion.flush()

        orden = [t.id for t in datos.en_espera(sesion, fila_id)]
        for turno in al_final:
            adelante = orden.index(turno.id)
            if adelante <= parametros.avisar_cuando_falten:
                turno.aviso_anticipado_en = ahora  # este aviso ya le pide salir
            _avisar(sesion, turno, TipoAviso.PASA_AL_FINAL, ahora, adelante=adelante)
        _revisar_fila(sesion, fila, ahora, parametros)
    return resultados


# --- En el local -----------------------------------------------------------------------


def corregir_turno(
    sesion: Session,
    turno_id: int,
    *,
    administrador_id: int | None = None,
    tipo_vehiculo_id: int | None = None,
    servicio_id: int | None = None,
    placa: str | None = None,
    ahora: datetime | None = None,
) -> Turno:
    """El administrador completa o corrige el turno al recibir el vehículo.

    Completa las marcas pendientes (placa pendiente, servicio por definir) y corrige el
    tipo de vehículo si el cliente dijo otro. El precio se ajusta solo, porque al cobrar
    sale del menú, y la espera de la fila se recalcula sola cada vez que se consulta.
    """
    ahora = ahora or tiempo.ahora()
    turno = datos.bloquear_turno(sesion, turno_id)
    if turno.estado not in ESTADOS_ACTIVOS:
        raise CambioNoPermitido(
            f"El turno ya terminó ({NOMBRE_DEL_ESTADO[turno.estado]}): no se puede corregir."
        )
    vehiculo = sesion.get(Vehiculo, turno.vehiculo_id)
    cambios = []

    if tipo_vehiculo_id is not None and tipo_vehiculo_id != vehiculo.tipo_vehiculo_id:
        nuevo_tipo = sesion.get(TipoVehiculo, tipo_vehiculo_id)
        if nuevo_tipo is None:
            raise NoExiste(f"No existe el tipo de vehículo {tipo_vehiculo_id}.")
        if nuevo_tipo.fila_id != turno.fila_id:
            raise VehiculoDeOtraFila(
                f"Un vehículo {nuevo_tipo.nombre.lower()} va en la otra fila: "
                "cancela este turno y crea uno allá."
            )
        anterior = sesion.get(TipoVehiculo, vehiculo.tipo_vehiculo_id).nombre
        vehiculo.tipo_vehiculo_id = nuevo_tipo.id
        cambios.append(f"Vehículo: {anterior} → {nuevo_tipo.nombre}")

    if placa is not None:
        vehiculo = _poner_placa(sesion, turno, vehiculo, placa)
        cambios.append(f"Placa: {vehiculo.placa}")

    if servicio_id is not None and servicio_id != turno.servicio_id:
        servicio = _servicio_valido(sesion, servicio_id, turno.fila_id, vehiculo.tipo_vehiculo_id)
        anterior = (
            sesion.get(Servicio, turno.servicio_id).nombre if turno.servicio_id else "por definir"
        )
        turno.servicio_id = servicio.id
        cambios.append(f"Servicio: {anterior} → {servicio.nombre}")
    elif turno.servicio_id is not None:
        # El servicio que ya tenía debe seguir teniendo precio para el vehículo corregido.
        _servicio_valido(sesion, turno.servicio_id, turno.fila_id, vehiculo.tipo_vehiculo_id)

    if cambios:
        _registrar(
            sesion,
            turno,
            turno.estado,
            turno.estado,
            actor=Actor.ADMINISTRADOR,
            ahora=ahora,
            administrador_id=administrador_id,
            detalle="; ".join(cambios),
        )
    sesion.flush()
    return turno


def pasar_a_puesto(
    sesion: Session,
    turno_id: int,
    puesto_id: int,
    *,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Turno:
    """El vehículo entra a lavarse en un puesto libre de su fila.

    Lo normal es pasar al primero de la fila que ya llegó (el puesto no espera:
    `consultas.siguiente_para_puesto`), pero el administrador puede tener una razón para
    escoger otro. Después la fila avanza y salen los avisos a quien le toca y a quien le
    faltan 2 turnos.
    """
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    turno = datos.bloquear_turno(sesion, turno_id)
    if not reglas.puede_cambiar(turno.estado, EstadoTurno.EN_LAVADO, Actor.ADMINISTRADOR):
        raise CambioNoPermitido(
            _por_que_no(turno.estado, EstadoTurno.EN_LAVADO, Actor.ADMINISTRADOR)
        )
    puesto = sesion.get(Puesto, puesto_id)
    if puesto is None or puesto.fila_id != turno.fila_id:
        raise PuestoNoDisponible("Ese puesto no es de la fila de este turno.")
    if not puesto.activo:
        raise PuestoNoDisponible(f"El puesto {puesto.nombre} está apagado.")
    ocupante = datos.turno_en_el_puesto(sesion, puesto.id)
    if ocupante is not None:
        raise PuestoNoDisponible(
            f"El puesto {puesto.nombre} está ocupado con el turno {_numero(sesion, ocupante)}."
        )
    turno.puesto_id = puesto.id
    turno.en_lavado_desde = ahora
    _cambiar_estado(
        sesion,
        turno,
        EstadoTurno.EN_LAVADO,
        actor=Actor.ADMINISTRADOR,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=f"Puesto {puesto.nombre}",
    )
    sesion.flush()
    _revisar_fila(sesion, sesion.get(Fila, turno.fila_id), ahora, parametros)
    return turno


def marcar_listo(
    sesion: Session,
    turno_id: int,
    *,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Turno:
    """El vehículo quedó listo: se le avisa al cliente y el puesto queda libre."""
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    turno = datos.bloquear_turno(sesion, turno_id)
    detalle = None
    if turno.en_lavado_desde is not None:
        minutos = round((ahora - turno.en_lavado_desde).total_seconds() / 60)
        detalle = f"Lavado en {minutos} min"
    _cambiar_estado(
        sesion,
        turno,
        EstadoTurno.LISTO,
        actor=Actor.ADMINISTRADOR,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=detalle,
    )
    _avisar(sesion, turno, TipoAviso.VEHICULO_LISTO, ahora)
    sesion.flush()
    _revisar_fila(sesion, sesion.get(Fila, turno.fila_id), ahora, parametros)
    return turno


def cobrar(
    sesion: Session,
    turno_id: int,
    *,
    medio_pago: MedioPago,
    administrador_id: int | None = None,
    precio_cobrado: int | None = None,
    motivo: str | None = None,
    placa: str | None = None,
    ahora: datetime | None = None,
) -> Pago:
    """Cobra un turno Listo. El cobro entra a la caja abierta del lavadero: sin caja abierta
    no se cobra.

    No se cobra sin placa ni con el servicio por definir. El precio sale del menú; si
    el administrador cobra otro, debe escribir el motivo. El pago guarda una copia del
    precio del menú: si mañana suben los precios, este cobro no cambia.
    """
    ahora = ahora or tiempo.ahora()
    turno = datos.bloquear_turno(sesion, turno_id)
    if not reglas.puede_cambiar(turno.estado, EstadoTurno.PAGADO, Actor.ADMINISTRADOR):
        raise CambioNoPermitido(_por_que_no(turno.estado, EstadoTurno.PAGADO, Actor.ADMINISTRADOR))
    caja = caja_del_lavadero.caja_para_cobrar(sesion)
    if turno.servicio_id is None:
        raise FaltaServicio("Define el servicio antes de cobrar.")
    vehiculo = sesion.get(Vehiculo, turno.vehiculo_id)
    if placa:
        vehiculo = _poner_placa(sesion, turno, vehiculo, placa)
    if vehiculo.placa is None:
        raise FaltaPlaca(
            "Escribe la placa antes de cobrar: sin ella el vehículo no acumula en la promoción."
        )
    precio = datos.precio_vigente(sesion, turno.servicio_id, vehiculo.tipo_vehiculo_id)
    if precio is None:
        raise ServicioNoDisponible("Ese servicio no tiene precio para este vehículo.")
    cobrado = precio.precio if precio_cobrado is None else precio_cobrado
    motivo_limpio = reglas.motivo_del_cobro(precio.precio, cobrado, motivo)
    medio = MedioPago(medio_pago)
    pago = Pago(
        turno_id=turno.id,
        precio_menu=precio.precio,
        precio_cobrado=cobrado,
        motivo_ajuste=motivo_limpio,
        medio_pago=medio,
        caja_id=caja.id,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    sesion.add(pago)
    _cambiar_estado(
        sesion,
        turno,
        EstadoTurno.PAGADO,
        actor=Actor.ADMINISTRADOR,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=f"{pesos(cobrado)} en {medio.value}",
    )
    sesion.flush()
    return pago


# --- Corregir un cobro --------------------------------------------------------------------
#
# Como en el parqueadero: un cobro equivocado (otro medio de pago, otro valor, el turno que no
# era) se corrige en su caja, abierta o cerrada hace hasta 30 días, con el motivo. No se borra
# nada sin dejar rastro: la corrección queda en `correccion_cobro` (con cómo estaba, cómo quedó
# y quién) y en el historial del turno. En una caja ya cerrada, lo que debía haber en el cajón
# se pone al día y la diferencia del cierre cambia con la corrección.


def _cobro_del_turno(sesion: Session, turno_id: int) -> tuple[Turno, Pago, str, str]:
    """El turno cobrado, su pago (los dos bloqueados), su placa y en palabras qué cobro era."""
    turno = datos.bloquear_turno(sesion, turno_id)
    fila = sesion.get(Fila, turno.fila_id)
    numero = reglas.numero_de_turno(fila.prefijo, turno.consecutivo)
    pago = sesion.scalars(
        select(Pago)
        .where(Pago.turno_id == turno.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if turno.estado != EstadoTurno.PAGADO or pago is None:
        raise CobroNoCorregible(f"El turno {numero} no tiene un cobro que corregir.")
    placa = sesion.scalar(select(Vehiculo.placa).where(Vehiculo.id == turno.vehiculo_id))
    if not placa:
        raise CobroNoCorregible(f"El vehículo del turno {numero} no tiene placa: escríbela antes.")
    servicio = sesion.get(Servicio, turno.servicio_id) if turno.servicio_id else None
    detalle = f"Turno {numero}" + (f" · {servicio.nombre}" if servicio else "")
    return turno, pago, placa, detalle


def corregir_cobro(
    sesion: Session,
    turno_id: int,
    *,
    precio_cobrado: int,
    medio_pago: MedioPago | str | None,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionCobro:
    """Cambia el valor o el medio de pago de un turno ya cobrado (era Nequi y no efectivo, se
    escribió $400.000 y eran $40.000). Si el valor queda distinto al del menú, el motivo de la
    corrección queda como el motivo del ajuste."""
    ahora = ahora or tiempo.ahora()
    limpio = cajas.motivo_de_la_correccion(motivo)
    turno, pago, placa, detalle = _cobro_del_turno(sesion, turno_id)
    caja = caja_del_lavadero.caja_del_cobro(sesion, pago.caja_id, ahora)
    if precio_cobrado < 0:
        raise ValorInvalido("El valor no puede ser negativo.")
    sin_ceros_de_mas(precio_cobrado, VALOR_MAXIMO_DE_UN_COBRO)
    if not medio_pago:
        raise ValorInvalido("Elige el medio de pago.")
    medio = MedioPago(medio_pago)
    antes = (pago.precio_cobrado, pago.medio_pago)
    if (precio_cobrado, medio) == antes:
        raise ValorInvalido(
            f"No hay nada que cambiar: ya está en {pesos(antes[0])} {como_se_pago(antes[1])}."
        )
    correccion = CorreccionCobro(
        tipo=TipoCorreccion.CORREGIDO,
        caja_id=caja.id,
        turno_id=turno.id,
        pago_id=pago.id,
        placa=placa,
        valor_antes=antes[0],
        valor_despues=precio_cobrado,
        medio_antes=antes[1],
        medio_despues=medio,
        detalle=detalle,
        motivo=limpio,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    pago.precio_cobrado = precio_cobrado
    pago.medio_pago = medio
    pago.motivo_ajuste = None if precio_cobrado == pago.precio_menu else limpio
    cajas.anotar_en_la_caja(correccion, caja)
    _registrar(
        sesion,
        turno,
        EstadoTurno.PAGADO,
        EstadoTurno.PAGADO,
        actor=Actor.ADMINISTRADOR,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=(
            f"Cobro corregido: {pesos(antes[0])} {como_se_pago(antes[1])} → "
            f"{pesos(precio_cobrado)} {como_se_pago(medio)}. Motivo: {limpio}"
        ),
    )
    sesion.add(correccion)
    sesion.flush()
    return correccion


def deshacer_cobro(
    sesion: Session,
    turno_id: int,
    *,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionCobro:
    """Quita el cobro de un turno (se cobró el que no era, o dos veces): el pago se borra, el
    turno vuelve a quedar Listo, por cobrar, y la corrección queda con lo que se había cobrado."""
    ahora = ahora or tiempo.ahora()
    limpio = cajas.motivo_de_la_correccion(motivo)
    turno, pago, placa, detalle = _cobro_del_turno(sesion, turno_id)
    caja = caja_del_lavadero.caja_del_cobro(sesion, pago.caja_id, ahora)
    correccion = CorreccionCobro(
        tipo=TipoCorreccion.COBRO_DESHECHO,
        caja_id=caja.id,
        turno_id=turno.id,
        pago_id=pago.id,
        placa=placa,
        valor_antes=pago.precio_cobrado,
        valor_despues=0,
        medio_antes=pago.medio_pago,
        medio_despues=None,
        detalle=detalle,
        motivo=limpio,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    cajas.anotar_en_la_caja(correccion, caja)
    _registrar(
        sesion,
        turno,
        EstadoTurno.PAGADO,
        EstadoTurno.LISTO,
        actor=Actor.ADMINISTRADOR,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=(
            f"Cobro deshecho ({pesos(pago.precio_cobrado)} {como_se_pago(pago.medio_pago)}). "
            f"Motivo: {limpio}"
        ),
    )
    turno.estado = EstadoTurno.LISTO
    sesion.delete(pago)
    sesion.add(correccion)
    sesion.flush()
    return correccion


# --- Puestos -----------------------------------------------------------------------------


def cambiar_puesto(
    sesion: Session, puesto_id: int, *, activo: bool, ahora: datetime | None = None
) -> bool:
    """Prende o apaga un puesto (por ejemplo, si se daña la hidrolavadora). Devuelve si cambió.

    Un puesto apagado sale de la fila sin borrarse. No se apaga con un vehículo adentro.
    Al prenderlo queda un puesto libre más, así que se llama de una vez al siguiente.
    """
    ahora = ahora or tiempo.ahora()
    puesto = sesion.get(Puesto, puesto_id)
    if puesto is None:
        raise NoExiste(f"No existe el puesto {puesto_id}.")
    fila = datos.bloquear_fila(sesion, puesto.fila_id)
    if puesto.activo == activo:
        return False
    if not activo:
        ocupante = datos.turno_en_el_puesto(sesion, puesto.id)
        if ocupante is not None:
            numero = reglas.numero_de_turno(fila.prefijo, ocupante.consecutivo)
            raise PuestoOcupado(
                f"En el puesto {puesto.nombre} se está lavando {numero}: "
                "márcalo listo antes de apagarlo."
            )
    puesto.activo = activo
    sesion.flush()
    if activo:
        _revisar_fila(sesion, fila, ahora, leer_parametros(sesion))
    return True


def agregar_puesto(sesion: Session, fila_id: int, *, ahora: datetime | None = None) -> Puesto:
    """Un puesto nuevo en la fila (C3, M5...), ya prendido: se llama de una vez al siguiente."""
    ahora = ahora or tiempo.ahora()
    fila = datos.bloquear_fila(sesion, fila_id)
    nombres = sesion.scalars(select(Puesto.nombre).where(Puesto.fila_id == fila.id))
    numeros = [int(nombre[len(fila.prefijo) :]) for nombre in nombres if nombre[1:].isdigit()]
    puesto = Puesto(fila_id=fila.id, nombre=f"{fila.prefijo}{max(numeros, default=0) + 1}")
    sesion.add(puesto)
    sesion.flush()
    _revisar_fila(sesion, fila, ahora, leer_parametros(sesion))
    return puesto


# --- Reservas ----------------------------------------------------------------------------

# Una reserva para hoy queda, mínimo, media hora adelante (app.turnos.reglas).
ANTICIPACION_MINIMA = reglas.ANTICIPACION_MINIMA


@dataclass(frozen=True)
class DiaParaReservar:
    fecha: date
    horarios: int  # cuántos horarios libres le quedan


@dataclass(frozen=True)
class Reserva:
    """Lo que el bot necesita para confirmar una reserva o un cambio de hora."""

    turno: Turno
    numero: str
    hora: datetime
    # Si cambió de día, el turno cambia de número: este era el de antes.
    numero_anterior: str | None = None


def duracion_del_turno(
    sesion: Session, fila_id: int, servicio_id: int | None, tipo_vehiculo_id: int
) -> timedelta:
    parametros = leer_parametros(sesion)
    return datos.Duraciones(sesion, fila_id, parametros.servicio_por_defecto).de(
        servicio_id, tipo_vehiculo_id
    )


def horarios_libres(
    sesion: Session,
    *,
    fila_id: int,
    fecha: date,
    duracion: timedelta,
    ahora: datetime | None = None,
    sin_turno_id: int | None = None,
) -> list[datetime]:
    """Las horas de ese día que todavía se pueden reservar en esa fila para un servicio que
    dura `duracion`: dentro del horario, con media hora de anticipación como mínimo, hasta
    `dias_para_reservar` días adelante y mientras quede un puesto (activo) libre en todo el
    rato que dura. `sin_turno_id`: el turno que cambia de hora no cuenta como ocupado."""
    ahora = ahora or tiempo.ahora()
    p = leer_parametros(sesion)
    zona = tiempo.zona_horaria()
    hoy = tiempo.hora_local(ahora).date()
    if not hoy <= fecha <= hoy + timedelta(days=p.dias_para_reservar):
        return []
    horas = [
        momento
        for momento in reglas.horarios_del_dia(
            fecha,
            apertura=p.hora_apertura,
            cierre=p.hora_cierre,
            dias=p.dias_atencion,
            cada=p.entre_horarios,
            duracion=duracion,
            zona=zona,
        )
        if momento >= ahora + ANTICIPACION_MINIMA
    ]
    if not horas:
        return []
    capacidad = len(datos.puestos_activos(sesion, fila_id))
    duraciones = datos.Duraciones(sesion, fila_id, p.servicio_por_defecto)
    del_dia = datos.reservas(
        sesion,
        fila_id,
        desde=datetime.combine(fecha, time(0), zona) - timedelta(hours=6),
        hasta=datetime.combine(fecha + timedelta(days=1), time(0), zona) + timedelta(hours=6),
        sin_turno_id=sin_turno_id,
    )
    tipos = datos.tipos_de_vehiculo(sesion, [t.vehiculo_id for t in del_dia])
    ocupadas = [
        (t.hora_reservada, duraciones.de(t.servicio_id, tipos[t.vehiculo_id])) for t in del_dia
    ]
    return [
        momento
        for momento in horas
        if reglas.cabe_otra_reserva(momento, duracion, ocupadas, capacidad)
    ]


def dias_para_reservar(
    sesion: Session,
    *,
    fila_id: int,
    duracion: timedelta,
    ahora: datetime | None = None,
    sin_turno_id: int | None = None,
) -> list[DiaParaReservar]:
    """Los días que todavía tienen horarios libres, desde hoy hasta `dias_para_reservar`."""
    ahora = ahora or tiempo.ahora()
    hoy = tiempo.hora_local(ahora).date()
    dias = []
    for adelante in range(leer_parametros(sesion).dias_para_reservar + 1):
        fecha = hoy + timedelta(days=adelante)
        libres = horarios_libres(
            sesion,
            fila_id=fila_id,
            fecha=fecha,
            duracion=duracion,
            ahora=ahora,
            sin_turno_id=sin_turno_id,
        )
        if libres:
            dias.append(DiaParaReservar(fecha, len(libres)))
    return dias


def _revisar_horario(
    sesion: Session,
    *,
    fila_id: int,
    hora_elegida: datetime,
    duracion: timedelta,
    ahora: datetime,
    sin_turno_id: int | None = None,
) -> None:
    fecha = tiempo.hora_local(hora_elegida).date()
    libres = horarios_libres(
        sesion,
        fila_id=fila_id,
        fecha=fecha,
        duracion=duracion,
        ahora=ahora,
        sin_turno_id=sin_turno_id,
    )
    if hora_elegida not in libres:
        raise HorarioNoDisponible(
            f"Para {cuando_sera(tiempo.hora_local(hora_elegida), tiempo.hora_local(ahora).date())} "
            "ya no hay cupo. Elige otra hora."
        )


def _siguiente_consecutivo(sesion: Session, fila_id: int, fecha: date) -> int:
    ultimo = sesion.scalar(
        select(func.max(Turno.consecutivo)).where(Turno.fila_id == fila_id, Turno.fecha == fecha)
    )
    return (ultimo or 0) + 1


def _dicho_de_la_reserva(momento: datetime, ahora: datetime) -> str:
    """ "hoy a las 10:30 a.m.", "mañana a las 7:30 a.m.", "el 25 de noviembre a las..." """
    hoy = tiempo.hora_local(ahora).date()
    local = tiempo.hora_local(momento)
    texto = cuando_sera(local, hoy)
    return f"hoy a {texto}" if local.date() == hoy else texto


def reservar(
    sesion: Session,
    *,
    vehiculo_id: int,
    hora_elegida: datetime,
    origen: OrigenTurno,
    servicio_id: int | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Reserva:
    """Reserva un día y una hora. El turno queda Reservado: a esa hora entra a la fila de
    primero (`activar_reservas`). Cada horario tiene tantos cupos como puestos activos.

    Por WhatsApp, máximo un turno activo por teléfono en cada fila (también cuenta una
    reserva). Se puede reservar aunque el local esté cerrado ahora.
    """
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    vehiculo = sesion.get(Vehiculo, vehiculo_id)
    if vehiculo is None:
        raise NoExiste(f"No existe el vehículo {vehiculo_id}.")
    tipo = sesion.get(TipoVehiculo, vehiculo.tipo_vehiculo_id)
    fila = datos.bloquear_fila(sesion, tipo.fila_id)
    por_whatsapp = origen == OrigenTurno.WHATSAPP
    if por_whatsapp:
        activos = datos.activos_del_cliente(sesion, vehiculo.cliente_id, fila.id)
        if len(activos) >= parametros.turnos_activos_por_telefono:
            primero = activos[0]
            raise YaTieneTurnoActivo(
                primero.id, reglas.numero_de_turno(fila.prefijo, primero.consecutivo)
            )
    if servicio_id is not None:
        _servicio_valido(sesion, servicio_id, fila.id, tipo.id)
    duracion = duracion_del_turno(sesion, fila.id, servicio_id, tipo.id)
    _revisar_horario(
        sesion, fila_id=fila.id, hora_elegida=hora_elegida, duracion=duracion, ahora=ahora
    )

    fecha = tiempo.hora_local(hora_elegida).date()
    turno = Turno(
        fila_id=fila.id,
        fecha=fecha,
        consecutivo=_siguiente_consecutivo(sesion, fila.id, fecha),
        cliente_id=vehiculo.cliente_id,
        vehiculo_id=vehiculo.id,
        servicio_id=servicio_id,
        estado=EstadoTurno.RESERVADO,
        origen=origen,
        en_fila_desde=hora_elegida,
        hora_reservada=hora_elegida,
    )
    sesion.add(turno)
    sesion.flush()
    numero = reglas.numero_de_turno(fila.prefijo, turno.consecutivo)
    _registrar(
        sesion,
        turno,
        None,
        EstadoTurno.RESERVADO,
        actor=Actor.CLIENTE if por_whatsapp else Actor.ADMINISTRADOR,
        ahora=ahora,
        administrador_id=administrador_id,
        detalle=f"Turno {numero}, reservado para {_dicho_de_la_reserva(hora_elegida, ahora)}",
    )
    # Si la reserva es pronto, desde ya se le guarda el puesto.
    _revisar_fila(sesion, fila, ahora, parametros)
    return Reserva(turno=turno, numero=numero, hora=hora_elegida)


# Desde estos estados se puede cambiar la hora: todavía no llega al local.
SE_PUEDE_REAGENDAR = frozenset({EstadoTurno.RESERVADO, EstadoTurno.EN_FILA, EstadoTurno.LLAMADO})


def reagendar(
    sesion: Session,
    turno_id: int,
    *,
    hora_elegida: datetime,
    actor: Actor,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Reserva:
    """Cambia el día o la hora: el turno queda Reservado para `hora_elegida`. Sirve para una
    reserva y también para quien estaba en la fila y ya no alcanza a venir hoy. Deja libre
    el cupo de antes; si estaba en la fila, la fila avanza. Si cambia de día, el turno
    toma un número de ese día."""
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    turno = datos.bloquear_turno(sesion, turno_id)
    if turno.estado not in SE_PUEDE_REAGENDAR:
        raise CambioNoPermitido(
            f"El turno está en «{NOMBRE_DEL_ESTADO[turno.estado]}»: ya no se le puede "
            "cambiar la hora."
        )
    if turno.estado != EstadoTurno.RESERVADO and not reglas.puede_cambiar(
        turno.estado, EstadoTurno.RESERVADO, actor
    ):
        raise CambioNoPermitido(_por_que_no(turno.estado, EstadoTurno.RESERVADO, actor))
    vehiculo = sesion.get(Vehiculo, turno.vehiculo_id)
    duracion = duracion_del_turno(
        sesion, turno.fila_id, turno.servicio_id, vehiculo.tipo_vehiculo_id
    )
    _revisar_horario(
        sesion,
        fila_id=turno.fila_id,
        hora_elegida=hora_elegida,
        duracion=duracion,
        ahora=ahora,
        sin_turno_id=turno.id,
    )
    fila = sesion.get(Fila, turno.fila_id)
    numero_antes = reglas.numero_de_turno(fila.prefijo, turno.consecutivo)
    fecha = tiempo.hora_local(hora_elegida).date()
    if fecha != turno.fecha:
        turno.consecutivo = _siguiente_consecutivo(sesion, fila.id, fecha)
        turno.fecha = fecha
    numero = reglas.numero_de_turno(fila.prefijo, turno.consecutivo)
    detalle = f"Cambió la hora: {_dicho_de_la_reserva(hora_elegida, ahora)}"
    if numero != numero_antes:
        detalle += f" (antes {numero_antes})"
    if turno.estado == EstadoTurno.RESERVADO:
        _registrar(
            sesion,
            turno,
            turno.estado,
            turno.estado,
            actor=actor,
            ahora=ahora,
            administrador_id=administrador_id,
            detalle=detalle,
        )
    else:
        _cambiar_estado(
            sesion,
            turno,
            EstadoTurno.RESERVADO,
            actor=actor,
            ahora=ahora,
            administrador_id=administrador_id,
            detalle=detalle,
        )
    turno.hora_reservada = hora_elegida
    turno.en_fila_desde = hora_elegida
    turno.prioridad = False
    turno.recordado_en = None
    turno.llamado_en = None
    turno.gracia_hasta = None
    turno.aviso_anticipado_en = None
    turno.en_camino = False
    # Una reserva nueva empieza de cero: si no llega a su hora, pasa al final (como dice el
    # mensaje de la reserva), aunque antes ya lo hubieran llamado una vez sin llegar.
    turno.intentos_sin_llegar = 0
    sesion.flush()
    _revisar_fila(sesion, fila, ahora, parametros)
    return Reserva(
        turno=turno,
        numero=numero,
        hora=hora_elegida,
        numero_anterior=numero_antes if numero != numero_antes else None,
    )


@dataclass(frozen=True)
class ReservaQueEntra:
    turno_id: int
    numero: str


def activar_reservas(sesion: Session, *, ahora: datetime | None = None) -> list[ReservaQueEntra]:
    """Lo que hace el reloj en cada vuelta: a las reservas que les llegó la hora, entran a la
    fila de primero (o, si ya están en el local, pasan de primeros). Si hay un puesto libre,
    se les llama de una vez.

    Si el servidor estuvo apagado (de noche, en el plan gratis de Render) y el día de una
    reserva ya pasó, no se le llama hoy por la reserva de ayer: queda en No llegó, sin
    mensaje."""
    ahora = ahora or tiempo.ahora()
    hoy = tiempo.hora_local(ahora).date()
    parametros = leer_parametros(sesion)
    pendientes = select(Turno.fila_id).where(
        Turno.hora_reservada <= ahora,
        Turno.prioridad.is_(False),
        Turno.estado.in_((EstadoTurno.RESERVADO, EstadoTurno.LLEGO)),
    )
    resultados = []
    for fila_id in sorted(set(sesion.scalars(pendientes))):
        fila = datos.bloquear_fila(sesion, fila_id)
        consulta = (
            select(Turno)
            .where(
                Turno.fila_id == fila_id,
                Turno.hora_reservada <= ahora,
                Turno.prioridad.is_(False),
                Turno.estado.in_((EstadoTurno.RESERVADO, EstadoTurno.LLEGO)),
            )
            .order_by(Turno.hora_reservada, Turno.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for turno in sesion.scalars(consulta).all():
            if (
                turno.estado == EstadoTurno.RESERVADO
                and tiempo.hora_local(turno.hora_reservada).date() < hoy
            ):
                _cambiar_estado(
                    sesion,
                    turno,
                    EstadoTurno.NO_LLEGO,
                    actor=Actor.SISTEMA,
                    ahora=ahora,
                    detalle="Su reserva era de un día que ya pasó",
                )
                continue
            turno.prioridad = True
            if turno.estado == EstadoTurno.RESERVADO:
                turno.en_fila_desde = turno.hora_reservada
                _cambiar_estado(
                    sesion,
                    turno,
                    EstadoTurno.EN_FILA,
                    actor=Actor.SISTEMA,
                    ahora=ahora,
                    detalle="Llegó la hora de su reserva: entra a la fila de primero",
                )
            else:
                _registrar(
                    sesion,
                    turno,
                    turno.estado,
                    turno.estado,
                    actor=Actor.SISTEMA,
                    ahora=ahora,
                    detalle="Llegó la hora de su reserva: pasa de primero",
                )
            resultados.append(ReservaQueEntra(turno.id, _numero(sesion, turno)))
        sesion.flush()
        _revisar_fila(sesion, fila, ahora, parametros)
    return resultados


# WhatsApp deja escribirle sin plantilla hasta 24 horas después de su último mensaje. Con
# este margen, el recordatorio no queda justo en el límite.
VENTANA_PARA_ESCRIBIR = timedelta(hours=24) - timedelta(minutes=5)


def _le_escribio_hace_poco(sesion: Session, telefono: str, ahora: datetime) -> bool:
    ultimo = sesion.scalar(
        select(func.max(MensajeEntrante.recibido_en)).where(MensajeEntrante.telefono == telefono)
    )
    return ultimo is not None and ultimo >= ahora - VENTANA_PARA_ESCRIBIR


def recordar_reservas(sesion: Session, *, ahora: datetime | None = None) -> int:
    """Lo que hace el reloj en cada vuelta: a quien le falta `recordar_reserva` (1 hora) para
    su reserva, se le recuerda una vez. Solo si WhatsApp lo deja (escribió en las últimas
    24 horas): si no, no se manda nada, porque Meta lo rechazaría. Devuelve cuántos
    recordatorios dejó en la bandeja de salida."""
    ahora = ahora or tiempo.ahora()
    parametros = leer_parametros(sesion)
    if parametros.recordar_reserva <= timedelta(0):
        return 0
    consulta = (
        select(Turno)
        .where(
            Turno.estado == EstadoTurno.RESERVADO,
            Turno.recordado_en.is_(None),
            Turno.hora_reservada > ahora,
            Turno.hora_reservada <= ahora + parametros.recordar_reserva,
        )
        .order_by(Turno.hora_reservada, Turno.id)
        .with_for_update(skip_locked=True)
    )
    enviados = 0
    for turno in sesion.scalars(consulta).all():
        turno.recordado_en = ahora
        cliente = sesion.get(Cliente, turno.cliente_id)
        if cliente is None or cliente.telefono is None:
            continue
        if not _le_escribio_hace_poco(sesion, cliente.telefono, ahora):
            continue
        _avisar(
            sesion,
            turno,
            TipoAviso.RECORDATORIO,
            ahora,
            hora_reservada=turno.hora_reservada.isoformat(),
        )
        enviados += 1
    sesion.flush()
    return enviados
