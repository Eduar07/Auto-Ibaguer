"""Las reservas en el dashboard y en Configuración.

En la fila en vivo el administrador ve las reservas de hoy (debajo de la espera de su
fila), las de los próximos días (a un lado), el puesto que se guarda para la reserva que
viene y, a su hora, quién va de primero. Desde ahí marca que llegó o la cancela. En
Configuración cambia el horario (hasta las 12 de la noche) y cómo se reserva.
"""

from datetime import datetime, time, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.catalogo.parametros import leer_parametros
from app.turnos import casos_de_uso as casos
from app.turnos.estados import EstadoTurno, OrigenTurno
from pruebas.fabricas import BOGOTA, LUNES_8AM, cliente_con_vehiculo, estado, ocupar_puestos
from pruebas.navegador import HTMX, aviso, error
from pruebas.test_pantallas_clientes_y_configuracion import formulario_de_configuracion

HOY = LUNES_8AM.date()
MARTES = HOY + timedelta(days=1)


def el(dia, hora: int, minuto: int = 0) -> datetime:
    return datetime.combine(dia, time(hora, minuto), BOGOTA)


def reserva(sesion: Session, momento: datetime, nombre: str = "Ana Ruiz") -> casos.Reserva:
    vehiculo = cliente_con_vehiculo(sesion, nombre=nombre)
    return casos.reservar(
        sesion,
        vehiculo_id=vehiculo.id,
        hora_elegida=momento,
        origen=OrigenTurno.WHATSAPP,
        ahora=LUNES_8AM,
    )


# --- La fila en vivo ---------------------------------------------------------------------------


def test_la_fila_muestra_las_reservas_de_hoy_y_las_proximas(adentro: TestClient, sesion: Session):
    de_hoy = reserva(sesion, el(HOY, 10, 30), nombre="Ana Ruiz")
    de_mañana = reserva(sesion, el(MARTES, 9, 30), nombre="Pedro Díaz")

    pagina = adentro.get("/fila").text

    assert "Reservas de hoy" in pagina and "Reservado · 10:30 a.m." in pagina
    assert "Próximas reservas" in pagina and "Mañana · 9:30 a.m." in pagina
    assert "No hay reservas para los próximos días." not in pagina
    assert "Nadie en espera." in pagina  # mientras no llega su hora, no están en la fila
    # La actividad dice quién reservó y para cuándo.
    assert f"{de_hoy.numero} reservó por WhatsApp para hoy a las 10:30 a.m." in pagina
    assert f"{de_mañana.numero} reservó por WhatsApp para mañana a las 9:30 a.m." in pagina


def test_el_puesto_libre_se_guarda_para_la_reserva_que_viene(adentro: TestClient, sesion: Session):
    ocupar_puestos(sesion, "C1")  # se desocupa a las 9:00
    de_las_8_30 = reserva(sesion, el(HOY, 8, 30))

    pagina = adentro.get("/fila").text

    assert f"Guardado para {de_las_8_30.numero}, que reservó a las 8:30 a.m." in pagina


def test_a_su_hora_la_reserva_va_de_primero(adentro: TestClient, sesion: Session, reloj: dict):
    ocupar_puestos(sesion, "C1", "C2")
    de_las_8_30 = reserva(sesion, el(HOY, 8, 30))
    reloj["ahora"] = el(HOY, 8, 30)
    casos.activar_reservas(sesion, ahora=reloj["ahora"])

    pagina = adentro.get("/fila").text

    assert "Reservó a 8:30 a.m.: va de primero" in pagina
    assert f"{de_las_8_30.numero}: llegó la hora de su reserva" in pagina


def test_marcar_que_llego_o_cancelar_una_reserva(adentro: TestClient, sesion: Session):
    llega = reserva(sesion, el(HOY, 10, 30))
    no_viene = reserva(sesion, el(HOY, 11, 30))

    respuesta = adentro.post(f"/turnos/{llega.turno.id}/llegada", headers=HTMX)
    assert aviso(respuesta) == f"{llega.numero} ya está en el local."
    assert estado(sesion, llega.turno.id) == EstadoTurno.LLEGO
    assert f"{llega.numero} llegó antes de su reserva" in respuesta.text

    respuesta = adentro.post(
        f"/turnos/{no_viene.turno.id}/cancelar", headers=HTMX, data={"motivo": "Llamó a cancelar"}
    )
    assert aviso(respuesta) == f"{no_viene.numero} cancelado."
    assert estado(sesion, no_viene.turno.id) == EstadoTurno.CANCELADO


# --- Configuración ---------------------------------------------------------------------------


def test_cierra_a_las_12_de_la_noche_y_cambia_como_se_reserva(adentro: TestClient, sesion: Session):
    formulario = formulario_de_configuracion(
        sesion,
        hora_apertura="06:30",
        hora_cierre="00:00",
        minutos_entre_horarios="30",
        dias_para_reservar="5",
        recordar_reserva_minutos="0",
    )

    respuesta = adentro.post("/configuracion", headers=HTMX, data=formulario)

    assert aviso(respuesta).startswith("Configuración guardada.")
    parametros = leer_parametros(sesion)
    assert (parametros.hora_apertura, parametros.hora_cierre) == (time(6, 30), time(0))
    assert parametros.entre_horarios == timedelta(minutes=30)
    assert parametros.dias_para_reservar == 5
    assert parametros.recordar_reserva == timedelta(0)  # 0: no se recuerda
    pagina = adentro.get("/configuracion").text
    assert "Reservas por WhatsApp" in pagina and 'value="00:00"' in pagina


def test_un_numero_fuera_de_rango_en_las_reservas_no_se_guarda(
    adentro: TestClient, sesion: Session
):
    formulario = formulario_de_configuracion(sesion, minutos_entre_horarios="5")

    respuesta = adentro.post("/configuracion", headers=HTMX, data=formulario)

    assert error(respuesta).startswith("No se guardó nada")
    assert "Escribe un número entre 15 y 240." in respuesta.text
    assert leer_parametros(sesion).entre_horarios == timedelta(hours=1)
