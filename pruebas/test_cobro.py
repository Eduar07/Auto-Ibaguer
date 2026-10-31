"""Cobrar: placa obligatoria, precio del menú, motivo si cambia y medio de pago."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.catalogo.tablas import ServicioPrecio
from app.clientes.tablas import Vehiculo
from app.turnos import casos_de_uso as casos
from app.turnos import datos
from app.turnos.errores import CambioNoPermitido, FaltaPlaca, FaltaServicio, PrecioInvalido
from app.turnos.estados import EstadoTurno, MedioPago
from app.turnos.tablas import Pago, Turno
from pruebas.fabricas import LUNES_8AM, en_el_local, historial, minutos, servicio, turno_listo

E = EstadoTurno
A_LAS_9 = LUNES_8AM + minutos(60)


def test_cobra_el_precio_del_menu(sesion: Session):
    turno = turno_listo(sesion)
    pago = casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=A_LAS_9)
    assert (pago.precio_menu, pago.precio_cobrado, pago.motivo_ajuste) == (40_000, 40_000, None)
    assert pago.medio_pago == MedioPago.EFECTIVO
    assert pago.fecha_hora == A_LAS_9
    assert sesion.get(Turno, turno).estado == E.PAGADO
    assert historial(sesion, turno)[-1].detalle == "$40.000 en efectivo"


def test_el_precio_depende_del_vehiculo(sesion: Session):
    turno = turno_listo(sesion, tipo="Camioneta", nombre_servicio="Premium")
    pago = casos.cobrar(sesion, turno, medio_pago=MedioPago.NEQUI, ahora=A_LAS_9)
    assert pago.precio_menu == 220_000


def test_cobrar_otro_precio_exige_motivo(sesion: Session):
    turno = turno_listo(sesion)
    with pytest.raises(PrecioInvalido, match="escribe el motivo"):
        casos.cobrar(
            sesion, turno, medio_pago=MedioPago.EFECTIVO, precio_cobrado=35_000, ahora=A_LAS_9
        )
    pago = casos.cobrar(
        sesion,
        turno,
        medio_pago=MedioPago.EFECTIVO,
        precio_cobrado=35_000,
        motivo="Cliente frecuente",
        ahora=A_LAS_9,
    )
    assert (pago.precio_menu, pago.precio_cobrado) == (40_000, 35_000)
    assert pago.motivo_ajuste == "Cliente frecuente"


def test_no_se_cobra_sin_placa(sesion: Session):
    turno = turno_listo(sesion, placa=None)
    with pytest.raises(FaltaPlaca):
        casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=A_LAS_9)
    # La placa se puede escribir al momento de cobrar.
    casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, placa="abc-123", ahora=A_LAS_9)
    vehiculo = sesion.get(Vehiculo, sesion.get(Turno, turno).vehiculo_id)
    assert vehiculo.placa == "ABC123"


def test_no_se_cobra_con_el_servicio_por_definir(sesion: Session):
    turno = turno_listo(sesion, nombre_servicio=None)
    with pytest.raises(FaltaServicio):
        casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=A_LAS_9)
    casos.corregir_turno(sesion, turno, servicio_id=servicio(sesion, "General").id, ahora=A_LAS_9)
    pago = casos.cobrar(sesion, turno, medio_pago=MedioPago.TARJETA, ahora=A_LAS_9)
    assert pago.precio_menu == 120_000


def test_solo_se_cobra_un_turno_listo(sesion: Session):
    nuevo = en_el_local(sesion, placa="ABC123", servicio_id=servicio(sesion).id)
    with pytest.raises(CambioNoPermitido, match="«Llegó» no puede pasar a «Pagado»"):
        casos.cobrar(sesion, nuevo.turno.id, medio_pago=MedioPago.EFECTIVO, ahora=A_LAS_9)


def test_un_turno_se_cobra_una_sola_vez(sesion: Session):
    turno = turno_listo(sesion)
    casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=A_LAS_9)
    with pytest.raises(CambioNoPermitido):
        casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=A_LAS_9)
    pagos = sesion.scalar(select(func.count()).select_from(Pago).where(Pago.turno_id == turno))
    assert pagos == 1


def test_si_mañana_suben_los_precios_el_cobro_de_hoy_no_cambia(sesion: Session):
    turno = turno_listo(sesion)
    pago = casos.cobrar(sesion, turno, medio_pago=MedioPago.EFECTIVO, ahora=A_LAS_9)

    vehiculo = sesion.get(Vehiculo, sesion.get(Turno, turno).vehiculo_id)
    viejo = datos.precio_vigente(sesion, servicio(sesion).id, vehiculo.tipo_vehiculo_id)
    viejo.activo = False
    sesion.flush()
    sesion.add(
        ServicioPrecio(
            servicio_id=viejo.servicio_id,
            tipo_vehiculo_id=viejo.tipo_vehiculo_id,
            precio=45_000,
            duracion_min=60,
        )
    )
    sesion.flush()

    sesion.refresh(pago)
    assert pago.precio_menu == 40_000
