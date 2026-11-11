"""Lo que se hace en el parqueadero: entradas, salidas, caja y mensualidades.

Cada función trabaja dentro de la transacción de quien la llama (la pantalla confirma con
commit). Antes de cambiar algo que se puede tocar dos veces al mismo tiempo (un doble clic
en "Cobrar", dos personas abriendo caja), se pide un candado en PostgreSQL: el segundo
espera a que termine el primero y ve lo que hizo. Así no se cobra dos veces.
"""

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import formato, tiempo
from app.clientes.errores import NombreInvalido, TelefonoInvalido
from app.clientes.reglas import nombre_bonito, normalizar_telefono
from app.parqueadero import reglas
from app.parqueadero.errores import (
    CajaYaAbierta,
    MensualidadInvalida,
    NoEstaAdentro,
    SinCajaAbierta,
    ValorInvalido,
    YaEstaAdentro,
)
from app.parqueadero.estados import EstadoParqueo, TipoParqueo
from app.parqueadero.tablas import CajaParqueadero, Mensualidad, PagoMensualidad, Parqueo
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


def _caja_para_cobrar(sesion: Session) -> CajaParqueadero:
    caja = caja_abierta(sesion)
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
    tarifa = reglas.Tarifa(parqueo.valor_hora, parqueo.gracia_minutos, parqueo.valor_fraccion)
    cobro = reglas.calcular_cobro(reglas.minutos_adentro(parqueo.entrada_en, ahora), tarifa)
    mensualidad = mensualidad_activa(sesion, parqueo.placa)
    al_dia_hasta = None
    if mensualidad is not None:
        al_dia_hasta = cubierta_hasta(sesion, mensualidad.id, _hoy(ahora))
    return Liquidacion(parqueo, ahora, cobro, mensualidad, al_dia_hasta)


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


def cubierta_hasta(sesion: Session, mensualidad_id: int, dia: date) -> date | None:
    """Si algún pago cubre `dia`, hasta cuándo va lo pagado (contando pagos seguidos)."""
    pagos = sesion.execute(
        select(PagoMensualidad.desde, PagoMensualidad.hasta).where(
            PagoMensualidad.mensualidad_id == mensualidad_id
        )
    ).all()
    return reglas.cubierta_hasta([(desde, hasta) for desde, hasta in pagos], dia)


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
    administrador_id: int | None = None,
    ahora: datetime | None = None,
) -> PagoMensualidad:
    """Registra un pago de mensualidad en la caja abierta.

    La mensualidad se bloquea primero: un doble clic no registra el mismo mes dos veces,
    porque el segundo ve que esos días ya están pagados.
    """
    ahora = ahora or tiempo.ahora()
    reglas.revisar_periodo(desde, hasta)
    if valor is None or valor <= 0:
        raise ValorInvalido("Escribe el valor que pagó.")
    mensualidad = _bloquear_mensualidad(sesion, mensualidad_id)
    if not mensualidad.activa:
        raise MensualidadInvalida("Esa mensualidad está dada de baja: no recibe pagos.")
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
