"""Lo que se hace en el parqueadero: entradas, salidas, caja, mensualidades y la corrección de
un cobro equivocado.

Cada función trabaja dentro de la transacción de quien la llama (la pantalla confirma con
commit). Antes de cambiar algo que se puede tocar dos veces al mismo tiempo (un doble clic
en "Cobrar", dos personas abriendo caja), se pide un candado en PostgreSQL: el segundo
espera a que termine el primero y ve lo que hizo. Así no se cobra dos veces.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.clientes.errores import NombreInvalido, TelefonoInvalido
from app.clientes.reglas import nombre_bonito, normalizar_telefono
from app.parqueadero import reglas
from app.parqueadero.errores import (
    CajaYaAbierta,
    CobroNoCorregible,
    MensualidadInvalida,
    NoEstaAdentro,
    SinCajaAbierta,
    ValorInvalido,
    YaEstaAdentro,
)
from app.parqueadero.estados import EstadoParqueo, TipoCorreccion, TipoParqueo
from app.parqueadero.tablas import (
    CajaParqueadero,
    CorreccionCobro,
    Mensualidad,
    PagoMensualidad,
    Parqueo,
)
from app.parqueadero.tarifas import leer_ajustes
from app.turnos.estados import MedioPago

# --- Candados ---------------------------------------------------------------------------


def _candado(sesion: Session, nombre: str) -> None:
    """Un candado de PostgreSQL con nombre, que se suelta solo al terminar la transacción."""
    sesion.execute(select(func.pg_advisory_xact_lock(func.hashtext(nombre))))


def _bloquear_parqueo(sesion: Session, parqueo_id: int) -> Parqueo:
    parqueo = sesion.execute(
        select(Parqueo)
        .where(Parqueo.id == parqueo_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if parqueo is None:
        raise NoEstaAdentro(f"No existe el ticket {parqueo_id}.")
    return parqueo


def _hora(momento: datetime) -> str:
    return formato.hora(tiempo.hora_local(momento))


def _hora_con_articulo(momento: datetime | None) -> str:
    return formato.hora_con_articulo(tiempo.hora_local(momento)) if momento else "hora sin anotar"


def _hoy(ahora: datetime) -> date:
    return tiempo.hora_local(ahora).date()


# --- Caja -------------------------------------------------------------------------------


def caja_abierta(sesion: Session) -> CajaParqueadero | None:
    return sesion.scalars(select(CajaParqueadero).where(CajaParqueadero.abierta)).one_or_none()


def abrir_caja(
    sesion: Session,
    *,
    base: int,
    nota: str | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CajaParqueadero:
    """Abre la caja con la plata que queda en el cajón para dar vueltos (la base)."""
    ahora = ahora or tiempo.ahora()
    if base < 0:
        raise ValorInvalido("La base no puede ser negativa.")
    _candado(sesion, "parqueadero:caja")
    abierta = caja_abierta(sesion)
    if abierta is not None:
        raise CajaYaAbierta(f"Ya hay una caja abierta desde las {_hora(abierta.abierta_en)}.")
    caja = CajaParqueadero(
        base=base,
        abierta_en=ahora,
        abierta_por=administrador_id,
        nota_apertura=(nota or "").strip() or None,
    )
    sesion.add(caja)
    sesion.flush()
    return caja


def cuadre_de_caja(sesion: Session, caja: CajaParqueadero) -> reglas.Cuadre:
    """Lo que entró a la caja por cada medio: salidas cobradas y pagos de mensualidad."""
    parqueos = sesion.execute(
        select(Parqueo.medio_pago, func.sum(Parqueo.valor_cobrado))
        .where(
            Parqueo.caja_id == caja.id,
            Parqueo.estado == EstadoParqueo.SALIO,
            Parqueo.valor_cobrado > 0,
        )
        .group_by(Parqueo.medio_pago)
    ).all()
    mensualidades = sesion.execute(
        select(PagoMensualidad.medio_pago, func.sum(PagoMensualidad.valor))
        .where(PagoMensualidad.caja_id == caja.id)
        .group_by(PagoMensualidad.medio_pago)
    ).all()
    return reglas.Cuadre(
        base=caja.base,
        parqueos=reglas.sumar_por_medio((medio, int(total)) for medio, total in parqueos),
        mensualidades=reglas.sumar_por_medio((medio, int(total)) for medio, total in mensualidades),
    )


def cerrar_caja(
    sesion: Session,
    caja_id: int,
    *,
    efectivo_contado: int,
    nota: str | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CajaParqueadero:
    """Cierra la caja con lo que se contó en el cajón. Guarda lo que esperaba el sistema
    (base + efectivo) para que la diferencia quede escrita."""
    ahora = ahora or tiempo.ahora()
    if efectivo_contado < 0:
        raise ValorInvalido("El efectivo contado no puede ser negativo.")
    _candado(sesion, "parqueadero:caja")
    caja = sesion.execute(
        select(CajaParqueadero)
        .where(CajaParqueadero.id == caja_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if caja is None or not caja.abierta:
        raise SinCajaAbierta("Esa caja ya está cerrada.")
    caja.efectivo_esperado = cuadre_de_caja(sesion, caja).efectivo_esperado
    caja.efectivo_contado = efectivo_contado
    caja.nota_cierre = (nota or "").strip() or None
    caja.cerrada_en = ahora
    caja.cerrada_por = administrador_id
    caja.abierta = False
    sesion.flush()
    return caja


def _caja_abierta_bloqueada(sesion: Session) -> CajaParqueadero | None:
    """La caja abierta, con un candado compartido hasta el final de la transacción.

    Mientras alguien cobra o corrige en ella, nadie la puede cerrar: el cierre espera y cuenta
    también ese cobro. Y si el cierre llegó primero, quien cobra espera y ve la caja cerrada
    (sin el candado, el cobro podía quedar en una caja ya contada, por fuera de su cuadre).
    """
    return sesion.execute(
        select(CajaParqueadero)
        .where(CajaParqueadero.abierta)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _caja_para_cobrar(sesion: Session) -> CajaParqueadero:
    caja = _caja_abierta_bloqueada(sesion)
    if caja is None:
        raise SinCajaAbierta("Abre la caja para poder cobrar.")
    return caja


# --- Entradas y salidas -----------------------------------------------------------------


def registrar_entrada(
    sesion: Session,
    *,
    placa: str,
    tipo: TipoParqueo,
    observaciones: str | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Parqueo:
    """Un vehículo entra: queda con la tarifa de hoy y su número de ticket."""
    ahora = ahora or tiempo.ahora()
    placa = reglas.validar_placa(placa)
    tipo = TipoParqueo(tipo)
    _candado(sesion, f"parqueadero:placa:{placa}")
    adentro = sesion.scalars(
        select(Parqueo).where(Parqueo.placa == placa, Parqueo.estado == EstadoParqueo.ADENTRO)
    ).one_or_none()
    if adentro is not None:
        raise YaEstaAdentro(
            f"{placa} ya está adentro desde las {_hora(adentro.entrada_en)} (ticket {adentro.id})."
        )
    tarifa = leer_ajustes(sesion).tarifa(tipo)
    parqueo = Parqueo(
        placa=placa,
        tipo=tipo,
        entrada_en=ahora,
        valor_hora=tarifa.valor_hora,
        valor_fraccion=tarifa.valor_fraccion,
        gracia_minutos=tarifa.gracia_minutos,
        valor_dia=tarifa.valor_dia,
        valor_dia_siguiente=tarifa.valor_dia_siguiente,
        observaciones=(observaciones or "").strip() or None,
        entrada_por=administrador_id,
    )
    sesion.add(parqueo)
    sesion.flush()
    return parqueo


def anular_entrada(
    sesion: Session,
    parqueo_id: int,
    *,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Parqueo:
    """Una entrada que no debió existir (placa equivocada, repetida). No cuenta para nada."""
    ahora = ahora or tiempo.ahora()
    limpio = motivo.strip()
    if not limpio:
        raise ValorInvalido("Escribe por qué se anula la entrada.")
    parqueo = _bloquear_parqueo(sesion, parqueo_id)
    if parqueo.estado is not EstadoParqueo.ADENTRO:
        raise NoEstaAdentro(f"El ticket {parqueo.id} ya no está adentro.")
    parqueo.estado = EstadoParqueo.ANULADO
    parqueo.salida_en = ahora
    parqueo.motivo_anulacion = limpio
    parqueo.salida_por = administrador_id
    sesion.flush()
    return parqueo


@dataclass(frozen=True)
class Liquidacion:
    """Lo que paga un vehículo si sale ahora."""

    parqueo: Parqueo
    salida_en: datetime
    cobro: reglas.Cobro  # lo que da la tarifa por el tiempo
    mensualidad: Mensualidad | None  # la mensualidad activa de la placa, si tiene
    al_dia_hasta: date | None  # hasta cuándo está pagada, si cubre la salida

    @property
    def es_mensualista_al_dia(self) -> bool:
        return self.al_dia_hasta is not None

    @property
    def debe_la_mensualidad(self) -> bool:
        """Tiene mensualidad, pero no cubre hoy: paga por horas, o se le cobra el mes."""
        return self.mensualidad is not None and self.al_dia_hasta is None

    @property
    def valor(self) -> int:
        return 0 if self.es_mensualista_al_dia else self.cobro.valor


def liquidar(sesion: Session, parqueo: Parqueo, ahora: datetime | None = None) -> Liquidacion:
    ahora = ahora or tiempo.ahora()
    mensualidad = mensualidad_activa(sesion, parqueo.placa)
    pagos = periodos_pagados(sesion, mensualidad.id) if mensualidad is not None else []
    return liquidar_con(parqueo, ahora, mensualidad, pagos)


def liquidar_con(
    parqueo: Parqueo,
    ahora: datetime,
    mensualidad: Mensualidad | None,
    pagos: list[tuple[date, date]],
) -> Liquidacion:
    """La cuenta de `liquidar`, con la mensualidad y sus pagos ya leídos. La lista de los que
    están adentro liquida a todos de una vez: así son dos consultas, no dos por vehículo."""
    cobro = reglas.calcular_cobro(
        reglas.minutos_adentro(parqueo.entrada_en, ahora), tarifa_con_que_entro(parqueo)
    )
    al_dia_hasta = None
    if mensualidad is not None:
        al_dia_hasta = reglas.cubierta_hasta(pagos, _hoy(ahora))
    return Liquidacion(parqueo, ahora, cobro, mensualidad, al_dia_hasta)


def tarifa_con_que_entro(parqueo: Parqueo) -> reglas.Tarifa:
    """La tarifa copiada en la entrada: si después cambió el precio, este paga el de antes."""
    return reglas.Tarifa(
        parqueo.valor_hora,
        parqueo.gracia_minutos,
        parqueo.valor_fraccion,
        parqueo.valor_dia,
        parqueo.valor_dia_siguiente,
    )


def cobrar_salida(
    sesion: Session,
    parqueo_id: int,
    *,
    medio_pago: MedioPago | None,
    valor_cobrado: int | None = None,
    motivo: str | None = None,
    recibido: int | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Parqueo:
    """El vehículo sale. Se cobra con la tarifa con que entró; un mensualista al día sale
    sin pagar. Si se cobra plata, entra a la caja abierta.

    El parqueo se bloquea primero: si llega un segundo "Cobrar" (doble clic), espera, ve que
    ya salió y no cobra otra vez.
    """
    ahora = ahora or tiempo.ahora()
    parqueo = _bloquear_parqueo(sesion, parqueo_id)
    if parqueo.estado is not EstadoParqueo.ADENTRO:
        raise NoEstaAdentro(
            f"{parqueo.placa} ya salió"
            + (f" a las {_hora(parqueo.salida_en)}" if parqueo.salida_en else "")
            + f" (ticket {parqueo.id})."
        )
    liquidacion = liquidar(sesion, parqueo, ahora)
    calculado = liquidacion.valor
    cobrado = calculado if valor_cobrado is None else valor_cobrado
    motivo_limpio = reglas.motivo_del_ajuste(calculado, cobrado, motivo)
    caja = caja_abierta(sesion)
    medio = MedioPago(medio_pago) if medio_pago else None
    if cobrado > 0:
        caja = _caja_para_cobrar(sesion)
        if medio is None:
            raise ValorInvalido("Elige el medio de pago.")
    else:
        medio = None  # una salida en $0 no movió plata
    parqueo.estado = EstadoParqueo.SALIO
    parqueo.salida_en = ahora
    parqueo.minutos = liquidacion.cobro.minutos
    parqueo.horas_cobradas = liquidacion.cobro.horas
    parqueo.valor_calculado = calculado
    parqueo.valor_cobrado = cobrado
    parqueo.motivo_ajuste = motivo_limpio
    parqueo.medio_pago = medio
    parqueo.recibido = reglas.revisar_recibido(cobrado, medio, recibido) if medio else None
    parqueo.caja_id = caja.id if caja is not None else None
    parqueo.mensualidad_id = (
        liquidacion.mensualidad.id if liquidacion.es_mensualista_al_dia else None
    )
    parqueo.salida_por = administrador_id
    sesion.flush()
    return parqueo


def buscar_adentro(sesion: Session, texto: str) -> Parqueo | None:
    """El vehículo que está adentro, por placa ("abc123") o por número de ticket ("123")."""
    placa, ticket = reglas.placa_o_ticket(texto)
    consulta = select(Parqueo).where(Parqueo.estado == EstadoParqueo.ADENTRO)
    if ticket is not None:
        return sesion.scalars(consulta.where(Parqueo.id == ticket)).one_or_none()
    if placa:
        return sesion.scalars(consulta.where(Parqueo.placa == placa)).one_or_none()
    return None


# --- Mensualidades ----------------------------------------------------------------------


def mensualidad_activa(sesion: Session, placa: str) -> Mensualidad | None:
    return sesion.scalars(
        select(Mensualidad).where(Mensualidad.placa == placa, Mensualidad.activa)
    ).one_or_none()


def periodos_pagados(sesion: Session, mensualidad_id: int) -> list[tuple[date, date]]:
    """Los periodos (desde, hasta) que cubren los pagos de una mensualidad."""
    return [(desde, hasta) for _, desde, hasta in _pagos_de(sesion, [mensualidad_id])]


def periodos_por_mensualidad(
    sesion: Session, mensualidad_ids: list[int]
) -> dict[int, list[tuple[date, date]]]:
    """Los periodos pagados de varias mensualidades, en una sola consulta."""
    periodos: dict[int, list[tuple[date, date]]] = {id_: [] for id_ in mensualidad_ids}
    for mensualidad_id, desde, hasta in _pagos_de(sesion, mensualidad_ids):
        periodos[mensualidad_id].append((desde, hasta))
    return periodos


def _pagos_de(sesion: Session, mensualidad_ids: Sequence[int]) -> list[Any]:
    if not mensualidad_ids:
        return []
    return list(
        sesion.execute(
            select(
                PagoMensualidad.mensualidad_id, PagoMensualidad.desde, PagoMensualidad.hasta
            ).where(PagoMensualidad.mensualidad_id.in_(mensualidad_ids))
        )
    )


def cubierta_hasta(sesion: Session, mensualidad_id: int, dia: date) -> date | None:
    """Si algún pago cubre `dia`, hasta cuándo va lo pagado (contando pagos seguidos)."""
    return reglas.cubierta_hasta(periodos_pagados(sesion, mensualidad_id), dia)


def pagada_hasta(sesion: Session, mensualidad_id: int) -> date | None:
    return sesion.scalar(
        select(func.max(PagoMensualidad.hasta)).where(
            PagoMensualidad.mensualidad_id == mensualidad_id
        )
    )


def _datos_del_titular(titular: str, telefono: str) -> tuple[str, str | None]:
    try:
        nombre = nombre_bonito(titular)
        celular = normalizar_telefono(telefono) if telefono.strip() else None
    except (NombreInvalido, TelefonoInvalido) as error:
        raise MensualidadInvalida(str(error)) from error
    return nombre, celular


def _valor_mensual(valor: int | None) -> int:
    if valor is None or valor <= 0:
        raise MensualidadInvalida("Escribe el valor de la mensualidad.")
    return valor


def crear_mensualidad(
    sesion: Session,
    *,
    placa: str,
    tipo: TipoParqueo,
    titular: str,
    telefono: str = "",
    valor_mensual: int | None,
    notas: str | None = None,
    autorizo_datos: bool,
    pagada_hasta: date | None = None,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> Mensualidad:
    """Una mensualidad nueva. El valor lo decide el administrador en cada caso.

    `pagada_hasta`: si ya traía días pagados (por ejemplo, con el dueño anterior), hasta cuándo.
    Quedan como un pago sin caja, en 0, desde hoy: sale sin pagar hasta ese día, el primer
    cobro empieza el día siguiente y en la caja no entra plata que no se recibió.
    """
    ahora = ahora or tiempo.ahora()
    placa = reglas.validar_placa(placa)
    if not autorizo_datos:
        raise MensualidadInvalida(
            "Sin la autorización del titular no se guarda su nombre ni su celular (Ley 1581)."
        )
    nombre, celular = _datos_del_titular(titular, telefono)
    valor = _valor_mensual(valor_mensual)
    hoy = _hoy(ahora)
    if pagada_hasta is not None:
        reglas.revisar_dias_traidos(pagada_hasta, hoy)
    _candado(sesion, f"parqueadero:mensualidad:{placa}")
    if mensualidad_activa(sesion, placa) is not None:
        raise MensualidadInvalida(f"{placa} ya tiene una mensualidad activa.")
    mensualidad = Mensualidad(
        placa=placa,
        tipo=TipoParqueo(tipo),
        titular=nombre,
        telefono=celular,
        valor_mensual=valor,
        notas=(notas or "").strip() or None,
        autorizo_datos_en=ahora,
    )
    sesion.add(mensualidad)
    sesion.flush()
    if pagada_hasta is not None:
        sesion.add(
            PagoMensualidad(
                mensualidad_id=mensualidad.id,
                desde=hoy,
                hasta=pagada_hasta,
                valor=0,
                medio_pago=None,
                caja_id=None,
                administrador_id=administrador_id,
                fecha_hora=ahora,
            )
        )
        sesion.flush()
    return mensualidad


def _bloquear_mensualidad(sesion: Session, mensualidad_id: int) -> Mensualidad:
    mensualidad = sesion.execute(
        select(Mensualidad)
        .where(Mensualidad.id == mensualidad_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if mensualidad is None:
        raise MensualidadInvalida("Esa mensualidad no existe.")
    return mensualidad


def editar_mensualidad(
    sesion: Session,
    mensualidad_id: int,
    *,
    tipo: TipoParqueo,
    titular: str,
    telefono: str = "",
    valor_mensual: int | None,
    notas: str | None = None,
) -> Mensualidad:
    """Cambia los datos de una mensualidad. La placa no se cambia: para otra placa, se da de
    baja esta y se crea una nueva (así los pagos quedan con el vehículo que los hizo)."""
    mensualidad = _bloquear_mensualidad(sesion, mensualidad_id)
    nombre, celular = _datos_del_titular(titular, telefono)
    mensualidad.tipo = TipoParqueo(tipo)
    mensualidad.titular = nombre
    mensualidad.telefono = celular
    mensualidad.valor_mensual = _valor_mensual(valor_mensual)
    mensualidad.notas = (notas or "").strip() or None
    sesion.flush()
    return mensualidad


TITULAR_BORRADO = "Datos borrados"


def dar_de_baja(sesion: Session, mensualidad_id: int, *, borrar_datos: bool = False) -> Mensualidad:
    """Ya no es mensualista: desde ahora paga por horas. Sus pagos quedan en el historial.

    `borrar_datos`: si el titular lo pidió (Ley 1581), se borran su nombre, su celular y las
    notas. La placa y los pagos se quedan: son las cuentas del negocio.
    """
    mensualidad = _bloquear_mensualidad(sesion, mensualidad_id)
    if not mensualidad.activa:
        raise MensualidadInvalida("Esa mensualidad ya estaba dada de baja.")
    mensualidad.activa = False
    if borrar_datos:
        mensualidad.titular = TITULAR_BORRADO
        mensualidad.telefono = None
        mensualidad.notas = None
    sesion.flush()
    return mensualidad


def periodo_sugerido(sesion: Session, mensualidad: Mensualidad, hoy: date) -> tuple[date, date]:
    """El siguiente periodo por pagar (ver reglas.periodo_siguiente): sigue al último pagado,
    aunque pague unos días tarde. En el cobro se puede cambiar."""
    return reglas.periodo_siguiente(pagada_hasta(sesion, mensualidad.id), hoy)


def pagar_mensualidad(
    sesion: Session,
    mensualidad_id: int,
    *,
    desde: date,
    hasta: date,
    valor: int | None,
    medio_pago: MedioPago,
    adelantado: bool = False,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> PagoMensualidad:
    """Registra un pago de mensualidad en la caja abierta.

    La mensualidad se bloquea primero: un doble clic no registra el mismo mes dos veces,
    porque el segundo ve que esos días ya están pagados.

    El mes siguiente se cobra cuando toca: desde 3 días antes de que se acabe lo pagado. Si
    está al día por más tiempo, solo se cobra con `adelantado` (el cliente pidió adelantar),
    para que un clic de más no le cobre otro mes.
    """
    ahora = ahora or tiempo.ahora()
    reglas.revisar_periodo(desde, hasta)
    if valor is None or valor <= 0:
        raise ValorInvalido("Escribe el valor que pagó.")
    mensualidad = _bloquear_mensualidad(sesion, mensualidad_id)
    if not mensualidad.activa:
        raise MensualidadInvalida("Esa mensualidad está dada de baja: no recibe pagos.")
    al_dia = cubierta_hasta(sesion, mensualidad.id, _hoy(ahora))
    if not adelantado and not reglas.se_puede_cobrar(al_dia, _hoy(ahora)):
        raise MensualidadInvalida(
            f"Ya pagó hasta el {formato.fecha(al_dia)}: el mes siguiente se cobra desde el "
            f"{formato.fecha(reglas.cobrar_desde(al_dia))}. Si el cliente quiere pagarlo ya, "
            "marca que es por adelantado."
        )
    caja = _caja_para_cobrar(sesion)
    cruce = sesion.execute(
        select(PagoMensualidad.desde, PagoMensualidad.hasta)
        .where(
            PagoMensualidad.mensualidad_id == mensualidad.id,
            PagoMensualidad.desde <= hasta,
            PagoMensualidad.hasta >= desde,
        )
        .limit(1)
    ).first()
    if cruce is not None:
        raise MensualidadInvalida(
            f"Esos días ya están pagados (del {formato.fecha(cruce.desde)} al "
            f"{formato.fecha(cruce.hasta)})."
        )
    pago = PagoMensualidad(
        mensualidad_id=mensualidad.id,
        desde=desde,
        hasta=hasta,
        valor=valor,
        medio_pago=MedioPago(medio_pago),
        caja_id=caja.id,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    sesion.add(pago)
    sesion.flush()
    return pago


# --- Correcciones -----------------------------------------------------------------------
#
# Un cobro equivocado (otro medio de pago, otro valor, la salida del carro que no era, un mes
# cobrado dos veces) se corrige solo mientras su caja sigue abierta: es como corregir una venta
# antes de cerrar la registradora. La caja cerrada ya se contó y se firmó, y sus números no se
# mueven. Cada corrección deja una fila en `correccion_cobro` con cómo estaba el cobro, cómo
# quedó, el motivo y quién la hizo: la caja, el balance y el reporte a los jefes la muestran.


def _motivo_de_la_correccion(motivo: str | None) -> str:
    limpio = " ".join((motivo or "").split())
    if not limpio:
        raise ValorInvalido(
            "Escribe por qué se corrige: queda anotado en la caja y en el reporte a los jefes."
        )
    return limpio[:200]


def _caja_del_cobro(sesion: Session, caja_id: int | None) -> CajaParqueadero:
    """La caja del cobro, que tiene que ser la abierta. Queda bloqueada hasta terminar: nadie
    la cierra a mitad de la corrección."""
    caja = _caja_abierta_bloqueada(sesion)
    if caja_id is None:
        raise CobroNoCorregible("Ese cobro no entró a ninguna caja: no hay plata que corregir.")
    if caja is None or caja.id != caja_id:
        raise CobroNoCorregible(
            "Ese cobro es de una caja que ya se cerró: sus cuentas quedaron contadas y no se "
            "corrigen. Si hay que devolver plata, sácala de la caja de hoy y escríbelo en la nota "
            "del cierre."
        )
    return caja


def _salida_cobrada(sesion: Session, parqueo_id: int) -> Parqueo:
    parqueo = _bloquear_parqueo(sesion, parqueo_id)
    if parqueo.estado is not EstadoParqueo.SALIO:
        raise CobroNoCorregible(
            f"{parqueo.placa} (ticket {parqueo.id}) no tiene una salida que corregir: "
            + ("está adentro." if parqueo.estado is EstadoParqueo.ADENTRO else "se anuló.")
        )
    return parqueo


def _como_estaba(valor: int, medio: MedioPago | None) -> str:
    return f"{formato.pesos(valor)} {reglas.como_se_pago(medio)}"


def corregir_salida(
    sesion: Session,
    parqueo_id: int,
    *,
    valor_cobrado: int,
    medio_pago: MedioPago | str | None,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionCobro:
    """Cambia el valor o el medio de pago de una salida ya cobrada (era Nequi y no efectivo,
    se escribió $30.000 y eran $3.000). Si el valor queda distinto a la tarifa, el motivo de la
    corrección queda como el motivo del ajuste."""
    ahora = ahora or tiempo.ahora()
    limpio = _motivo_de_la_correccion(motivo)
    parqueo = _salida_cobrada(sesion, parqueo_id)
    caja = _caja_del_cobro(sesion, parqueo.caja_id)
    if valor_cobrado < 0:
        raise ValorInvalido("El valor no puede ser negativo.")
    medio = MedioPago(medio_pago) if medio_pago and valor_cobrado > 0 else None
    if valor_cobrado > 0 and medio is None:
        raise ValorInvalido("Elige el medio de pago.")
    antes = (parqueo.valor_cobrado or 0, parqueo.medio_pago)
    if (valor_cobrado, medio) == antes:
        raise ValorInvalido(f"No hay nada que cambiar: ya está en {_como_estaba(*antes)}.")
    correccion = CorreccionCobro(
        tipo=TipoCorreccion.CORREGIDO,
        caja_id=caja.id,
        parqueo_id=parqueo.id,
        placa=parqueo.placa,
        valor_antes=antes[0],
        valor_despues=valor_cobrado,
        medio_antes=antes[1],
        medio_despues=medio,
        detalle=f"Salida de {_hora_con_articulo(parqueo.salida_en)} · ticket {parqueo.id}",
        motivo=limpio,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    parqueo.valor_cobrado = valor_cobrado
    parqueo.medio_pago = medio
    parqueo.motivo_ajuste = None if valor_cobrado == parqueo.valor_calculado else limpio
    parqueo.recibido = None  # el vuelto ya se dio: con otro valor o medio, ya no aplica
    sesion.add(correccion)
    sesion.flush()
    return correccion


def deshacer_salida(
    sesion: Session,
    parqueo_id: int,
    *,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionCobro:
    """La salida no debió pasar (se cobró el carro que no era): el vehículo vuelve a quedar
    adentro con su ticket, su hora de entrada y su tarifa, y lo cobrado sale de la caja."""
    ahora = ahora or tiempo.ahora()
    limpio = _motivo_de_la_correccion(motivo)
    parqueo = _salida_cobrada(sesion, parqueo_id)
    caja = _caja_del_cobro(sesion, parqueo.caja_id)
    _candado(sesion, f"parqueadero:placa:{parqueo.placa}")
    otra = sesion.scalars(
        select(Parqueo).where(
            Parqueo.placa == parqueo.placa, Parqueo.estado == EstadoParqueo.ADENTRO
        )
    ).one_or_none()
    if otra is not None:
        raise YaEstaAdentro(
            f"{parqueo.placa} ya volvió a entrar (ticket {otra.id}): esa salida no se puede "
            "deshacer. Si lo cobrado estuvo mal, cambia el valor o el medio de pago."
        )
    correccion = CorreccionCobro(
        tipo=TipoCorreccion.SALIDA_DESHECHA,
        caja_id=caja.id,
        parqueo_id=parqueo.id,
        placa=parqueo.placa,
        valor_antes=parqueo.valor_cobrado or 0,
        valor_despues=0,
        medio_antes=parqueo.medio_pago,
        medio_despues=None,
        detalle=f"Salida de {_hora_con_articulo(parqueo.salida_en)} · ticket {parqueo.id}",
        motivo=limpio,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    parqueo.estado = EstadoParqueo.ADENTRO
    parqueo.salida_en = None
    parqueo.minutos = None
    parqueo.horas_cobradas = None
    parqueo.valor_calculado = None
    parqueo.valor_cobrado = None
    parqueo.motivo_ajuste = None
    parqueo.medio_pago = None
    parqueo.recibido = None
    parqueo.caja_id = None
    parqueo.mensualidad_id = None
    parqueo.salida_por = None
    sesion.add(correccion)
    sesion.flush()
    return correccion


def _pago_cobrado(sesion: Session, pago_id: int) -> tuple[PagoMensualidad, Mensualidad]:
    """El pago y su mensualidad, bloqueados en el mismo orden que al cobrar el mes (primero la
    mensualidad): un doble clic espera y ve lo que hizo el primero."""
    mensualidad_id = sesion.scalar(
        select(PagoMensualidad.mensualidad_id).where(PagoMensualidad.id == pago_id)
    )
    mensualidad = _bloquear_mensualidad(sesion, mensualidad_id) if mensualidad_id else None
    pago = sesion.execute(
        select(PagoMensualidad)
        .where(PagoMensualidad.id == pago_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if pago is None or mensualidad is None:
        raise CobroNoCorregible("Ese pago ya no existe: puede que ya se haya anulado.")
    return pago, mensualidad


def _detalle_del_pago(pago: PagoMensualidad) -> str:
    return f"Mensualidad del {formato.fecha(pago.desde)} al {formato.fecha(pago.hasta)}"


def corregir_pago(
    sesion: Session,
    pago_id: int,
    *,
    valor: int,
    medio_pago: MedioPago | str | None,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionCobro:
    """Cambia el valor o el medio de pago de un mes cobrado. Los días que cubre no cambian:
    si eran otros, se anula el pago y se cobra de nuevo."""
    ahora = ahora or tiempo.ahora()
    limpio = _motivo_de_la_correccion(motivo)
    pago, mensualidad = _pago_cobrado(sesion, pago_id)
    caja = _caja_del_cobro(sesion, pago.caja_id)
    if valor <= 0:
        raise ValorInvalido("Escribe lo que pagó. Para quitar el pago, anúlalo.")
    if not medio_pago:
        raise ValorInvalido("Elige el medio de pago.")
    medio = MedioPago(medio_pago)
    antes = (pago.valor, pago.medio_pago)
    if (valor, medio) == antes:
        raise ValorInvalido(f"No hay nada que cambiar: ya está en {_como_estaba(*antes)}.")
    correccion = CorreccionCobro(
        tipo=TipoCorreccion.CORREGIDO,
        caja_id=caja.id,
        mensualidad_id=mensualidad.id,
        pago_id=pago.id,
        placa=mensualidad.placa,
        valor_antes=antes[0],
        valor_despues=valor,
        medio_antes=antes[1],
        medio_despues=medio,
        detalle=_detalle_del_pago(pago),
        motivo=limpio,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    pago.valor = valor
    pago.medio_pago = medio
    sesion.add(correccion)
    sesion.flush()
    return correccion


def anular_pago(
    sesion: Session,
    pago_id: int,
    *,
    motivo: str,
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> CorreccionCobro:
    """Quita un mes cobrado por error (dos veces, a la mensualidad equivocada): esos días
    vuelven a deberse y la plata sale de la caja. Lo cobrado queda en la corrección."""
    ahora = ahora or tiempo.ahora()
    limpio = _motivo_de_la_correccion(motivo)
    pago, mensualidad = _pago_cobrado(sesion, pago_id)
    caja = _caja_del_cobro(sesion, pago.caja_id)
    correccion = CorreccionCobro(
        tipo=TipoCorreccion.PAGO_ANULADO,
        caja_id=caja.id,
        mensualidad_id=mensualidad.id,
        pago_id=pago.id,
        placa=mensualidad.placa,
        valor_antes=pago.valor,
        valor_despues=0,
        medio_antes=pago.medio_pago,
        medio_despues=None,
        detalle=_detalle_del_pago(pago),
        motivo=limpio,
        administrador_id=administrador_id,
        fecha_hora=ahora,
    )
    sesion.add(correccion)
    sesion.delete(pago)
    sesion.flush()
    return correccion
