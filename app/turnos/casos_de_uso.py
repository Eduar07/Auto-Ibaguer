"""Casos de uso de la fila: lo que pasa cuando el cliente, el administrador o el reloj
hacen algo con un turno.

Cada función recibe una sesión abierta y deja los cambios listos, sin confirmarlos:
quien la llama (el bot, el dashboard o el reloj) hace el commit. Así el cambio de
estado, su historial y sus avisos quedan guardados juntos, o no queda ninguno.

Después de cada cambio, `_revisar_fila` aplica las reglas de llamados: llama a quien le
toca un puesto libre y le pide salir hacia el lavadero a quien le faltan 2 turnos.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import tiempo
from app.catalogo.parametros import Parametros, leer_parametros
from app.catalogo.tablas import Fila, Puesto, Servicio, TipoVehiculo
from app.clientes import servicio as clientes
from app.clientes.errores import NoExiste
from app.clientes.tablas import Cliente, Vehiculo
from app.formato import hora, hora_con_articulo, pesos
from app.integraciones import bandeja_salida
from app.turnos import consultas, datos, reglas
from app.turnos.errores import (
    CambioNoPermitido,
    FaltaPlaca,
    FaltaServicio,
    FueraDeHorario,
    PuestoNoDisponible,
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
    espera = datos.en_espera(sesion, fila.id)
    decision = reglas.decidir_llamados(
        [reglas.TurnoEnEspera(t.id, t.estado, t.aviso_anticipado_en is not None) for t in espera],
        datos.puestos_libres(sesion, fila.id),
        parametros.avisar_cuando_falten,
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
    _cambiar_estado(
        sesion,
        turno,
        EstadoTurno.LLEGO,
        actor=actor,
        ahora=ahora,
        administrador_id=administrador_id,
    )
    turno.en_camino = False
    sesion.flush()
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
    """Cobra un turno Listo.

    No se cobra sin placa ni con el servicio por definir. El precio sale del menú; si
    el administrador cobra otro, debe escribir el motivo. El pago guarda una copia del
    precio del menú: si mañana suben los precios, este cobro no cambia.
    """
    ahora = ahora or tiempo.ahora()
    turno = datos.bloquear_turno(sesion, turno_id)
    if not reglas.puede_cambiar(turno.estado, EstadoTurno.PAGADO, Actor.ADMINISTRADOR):
        raise CambioNoPermitido(_por_que_no(turno.estado, EstadoTurno.PAGADO, Actor.ADMINISTRADOR))
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
