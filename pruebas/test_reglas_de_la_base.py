"""Reglas del negocio que la base de datos hace cumplir por sí sola.

Aunque el código tenga un error, PostgreSQL rechaza estos cambios. Cada prueba
comprueba además que el rechazo venga de la regla correcta.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.bot.tablas import SesionBot
from app.catalogo.tablas import ServicioPrecio
from app.clientes.tablas import Cliente
from app.integraciones.tablas import MensajeEntrante
from app.turnos.estados import EstadoTurno, MedioPago
from app.turnos.tablas import Pago
from pruebas.fabricas import (
    DIA_DE_PRUEBA,
    debe_rechazar,
    nuevo_cliente,
    nuevo_turno,
    nuevo_vehiculo,
    servicio,
    tipo_vehiculo,
)

# --- Clientes y vehículos -------------------------------------------------------------


def test_dos_clientes_no_pueden_tener_el_mismo_telefono(sesion: Session):
    nuevo_cliente(sesion, telefono="573001234567")
    with debe_rechazar(sesion, "uq_cliente_telefono"):
        sesion.add(Cliente(telefono="573001234567", nombre="Otra persona"))


def test_telefono_solo_con_digitos(sesion: Session):
    with debe_rechazar(sesion, "ck_cliente_telefono_solo_digitos"):
        sesion.add(Cliente(telefono="300-123-4567", nombre="Ana"))


def test_aceptar_el_aviso_de_datos_exige_la_fecha(sesion: Session):
    with debe_rechazar(sesion, "ck_cliente_aceptacion_con_fecha"):
        sesion.add(Cliente(telefono="573009998877", nombre="Ana", acepta_datos=True))


def test_dos_vehiculos_no_pueden_tener_la_misma_placa(sesion: Session):
    nuevo_vehiculo(sesion, placa="ABC123")
    with debe_rechazar(sesion, "uq_vehiculo_placa"):
        nuevo_vehiculo(sesion, placa="ABC123")


def test_varios_vehiculos_pueden_tener_la_placa_pendiente(sesion: Session):
    primero = nuevo_vehiculo(sesion, placa=None)
    segundo = nuevo_vehiculo(sesion, placa=None)
    assert primero.id != segundo.id


def test_placa_guardada_en_mayusculas_y_sin_guiones(sesion: Session):
    with debe_rechazar(sesion, "ck_vehiculo_placa_formato"):
        nuevo_vehiculo(sesion, placa="abc123")
    with debe_rechazar(sesion, "ck_vehiculo_placa_formato"):
        nuevo_vehiculo(sesion, placa="ABC-12")


# --- Turnos ----------------------------------------------------------------------------


def test_el_numero_de_turno_no_se_repite_en_la_misma_fila_y_dia(sesion: Session):
    nuevo_turno(sesion, consecutivo=14)
    with debe_rechazar(sesion, "uq_turno_fila_id_fecha_consecutivo"):
        nuevo_turno(sesion, consecutivo=14)


def test_el_mismo_numero_si_se_puede_en_otra_fila_o_en_otro_dia(sesion: Session):
    nuevo_turno(sesion, consecutivo=14)
    nuevo_turno(sesion, consecutivo=14, nombre_fila="Motos")
    nuevo_turno(sesion, consecutivo=14, fecha=DIA_DE_PRUEBA.replace(day=27))


def test_un_estado_inventado_es_rechazado(sesion: Session):
    turno = nuevo_turno(sesion)
    with debe_rechazar(sesion, "ck_turno_estado"):
        sesion.execute(text("UPDATE turno SET estado = 'perdido' WHERE id = :id"), {"id": turno.id})


def test_un_puesto_no_puede_lavar_dos_vehiculos_a_la_vez(sesion: Session):
    # El "choque": dos personas tocan "Pasar a puesto C1" al mismo tiempo.
    nuevo_turno(sesion, consecutivo=1, estado=EstadoTurno.EN_LAVADO, nombre_puesto="C1")
    with debe_rechazar(sesion, "uq_turno_puesto_en_lavado"):
        nuevo_turno(sesion, consecutivo=2, estado=EstadoTurno.EN_LAVADO, nombre_puesto="C1")


def test_el_puesto_se_libera_cuando_el_vehiculo_queda_listo(sesion: Session):
    primero = nuevo_turno(sesion, consecutivo=1, estado=EstadoTurno.EN_LAVADO, nombre_puesto="C1")
    primero.estado = EstadoTurno.LISTO
    sesion.flush()
    nuevo_turno(sesion, consecutivo=2, estado=EstadoTurno.EN_LAVADO, nombre_puesto="C1")


def test_en_lavado_exige_un_puesto(sesion: Session):
    with debe_rechazar(sesion, "ck_turno_en_lavado_con_puesto"):
        nuevo_turno(sesion, estado=EstadoTurno.EN_LAVADO, nombre_puesto=None)


def test_en_lavado_exige_la_hora_de_entrada(sesion: Session):
    turno = nuevo_turno(sesion, estado=EstadoTurno.EN_LAVADO, nombre_puesto="C1", guardar=False)
    turno.en_lavado_desde = None
    with debe_rechazar(sesion, "ck_turno_en_lavado_con_hora"):
        sesion.add(turno)


def test_un_turno_llamado_exige_hora_limite(sesion: Session):
    turno = nuevo_turno(sesion, estado=EstadoTurno.LLAMADO, guardar=False)
    turno.gracia_hasta = None
    with debe_rechazar(sesion, "ck_turno_llamado_con_hora_limite"):
        sesion.add(turno)


def test_maximo_dos_intentos_sin_llegar(sesion: Session):
    turno = nuevo_turno(sesion)
    with debe_rechazar(sesion, "ck_turno_intentos_entre_0_y_2"):
        turno.intentos_sin_llegar = 3


# --- Cobro -----------------------------------------------------------------------------


def nuevo_pago(turno_id: int, precio_cobrado: int, motivo: str | None = None) -> Pago:
    return Pago(
        turno_id=turno_id,
        precio_menu=40_000,
        precio_cobrado=precio_cobrado,
        motivo_ajuste=motivo,
        medio_pago=MedioPago.EFECTIVO,
    )


def test_cambiar_el_precio_al_cobrar_exige_motivo(sesion: Session):
    turno = nuevo_turno(sesion, estado=EstadoTurno.LISTO)
    with debe_rechazar(sesion, "ck_pago_motivo_si_cambia_el_precio"):
        sesion.add(nuevo_pago(turno.id, 35_000))
    with debe_rechazar(sesion, "ck_pago_motivo_si_cambia_el_precio"):
        sesion.add(nuevo_pago(turno.id, 35_000, motivo="   "))

    sesion.add(nuevo_pago(turno.id, 35_000, motivo="Cliente frecuente"))
    sesion.flush()


def test_un_turno_se_cobra_una_sola_vez(sesion: Session):
    turno = nuevo_turno(sesion, estado=EstadoTurno.LISTO)
    sesion.add(nuevo_pago(turno.id, 40_000))
    sesion.flush()
    with debe_rechazar(sesion, "uq_pago_turno_id"):
        sesion.add(nuevo_pago(turno.id, 40_000))


# --- Catálogo y mensajes ---------------------------------------------------------------


def test_un_solo_precio_vigente_por_servicio_y_vehiculo(sesion: Session):
    basico = servicio(sesion, "Básico")
    automovil = tipo_vehiculo(sesion, "Automóvil")

    def precio(valor: int, activo: bool) -> ServicioPrecio:
        return ServicioPrecio(
            servicio_id=basico.id,
            tipo_vehiculo_id=automovil.id,
            precio=valor,
            duracion_min=60,
            activo=activo,
        )

    with debe_rechazar(sesion, "uq_servicio_precio_vigente"):
        sesion.add(precio(45_000, activo=True))
    # Un precio viejo (inactivo) sí se puede guardar como historia.
    sesion.add(precio(35_000, activo=False))
    sesion.flush()


def nueva_sesion_bot(telefono: str, cliente_id: int | None = None) -> SesionBot:
    return SesionBot(
        telefono=telefono,
        cliente_id=cliente_id,
        paso_actual="menu",
        expira_en=datetime.now(UTC) + timedelta(minutes=30),
    )


def test_un_numero_tiene_una_sola_sesion_con_el_bot(sesion: Session):
    sesion.add(nueva_sesion_bot("573001112233"))
    sesion.flush()
    with debe_rechazar(sesion, "uq_sesion_bot_telefono"):
        sesion.add(nueva_sesion_bot("573001112233"))


def test_un_cliente_tiene_una_sola_sesion_con_el_bot(sesion: Session):
    cliente = nuevo_cliente(sesion)
    sesion.add(nueva_sesion_bot("573001112233", cliente.id))
    sesion.flush()
    with debe_rechazar(sesion, "uq_sesion_bot_cliente_id"):
        sesion.add(nueva_sesion_bot("573004445566", cliente.id))


def test_varias_sesiones_sin_cliente_mientras_no_aceptan_el_aviso(sesion: Session):
    sesion.add_all([nueva_sesion_bot("573001112233"), nueva_sesion_bot("573004445566")])
    sesion.flush()


def test_un_mensaje_de_meta_no_se_guarda_dos_veces(sesion: Session):
    def mensaje() -> MensajeEntrante:
        return MensajeEntrante(
            id_meta="wamid.ABC123",
            telefono="573001112233",
            tipo="text",
            contenido={"text": {"body": "hola"}},
            recibido_en=datetime.now(UTC),
        )

    sesion.add(mensaje())
    sesion.flush()
    with debe_rechazar(sesion, "uq_mensaje_entrante_id_meta"):
        sesion.add(mensaje())
