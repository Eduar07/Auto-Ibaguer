"""El celular de quien escribe sin mostrar su número, y los dibujitos de los vehículos.

Lo que tiene que cumplirse siempre:
- a quien escribe con su usuario de WhatsApp (sin número) el bot le pide un celular para
  llamarlo, una sola vez y con la opción de decir que ahora no;
- a quien escribe con su celular nunca se le pide;
- el celular queda en el cliente, se ve en el dashboard con su botón de llamar y se borra
  si pide borrar sus datos;
- los botones de vehículo llevan su dibujito (🚗 🚙 🏍️) sin pasarse del largo de WhatsApp.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot.textos import LARGO_DE_UN_BOTON, con_emoji
from app.clientes import servicio as clientes
from app.clientes.tablas import Cliente
from app.comentarios import consultas as comentarios_consultas
from app.comentarios import servicio as comentarios
from app.comentarios.tablas import TipoComentario
from pruebas.fabricas import cliente_con_vehiculo, debe_rechazar
from pruebas.test_bot import Chat, paso

USUARIO = "CO.13491208655302741918"


def cliente_de(sesion: Session, telefono: str) -> Cliente | None:
    consulta = select(Cliente).where(Cliente.telefono == telefono)
    return sesion.scalars(consulta.execution_options(populate_existing=True)).one_or_none()


def hasta_el_nombre(sesion: Session, telefono: str = USUARIO) -> Chat:
    return Chat(sesion, telefono=telefono).escribe("hola").toca("Pedir turno").toca("Acepto")


# --- El celular ------------------------------------------------------------------------------


def test_sin_numero_visible_le_pide_el_celular_y_lo_guarda(sesion: Session):
    chat = hasta_el_nombre(sesion).escribe("Ana Ruiz")

    assert chat.texto.startswith("Tu WhatsApp no nos muestra tu número.")
    assert chat.opciones == ["Ahora no"] and paso(sesion, USUARIO) == "celular"

    chat.escribe("315 123 4567")
    assert chat.opciones == ["Automóvil", "Camioneta", "Moto"]  # sigue con el vehículo
    chat.toca("Moto").toca("Ahora no").toca("Lo decido en el local").toca("Confirmar turno")

    ana = cliente_de(sesion, USUARIO)
    assert (ana.nombre, ana.celular) == ("Ana Ruiz", "573151234567")


def test_un_celular_mal_escrito_se_pide_otra_vez(sesion: Session):
    chat = hasta_el_nombre(sesion).escribe("Ana Ruiz").escribe("12345678901")

    assert chat.texto.startswith("Ese celular no me cuadra.")
    assert chat.opciones == ["Ahora no"]


def test_puede_decir_que_ahora_no(sesion: Session):
    chat = hasta_el_nombre(sesion).escribe("Ana Ruiz").toca("Ahora no")

    assert chat.opciones == ["Automóvil", "Camioneta", "Moto"]
    chat.toca("Moto").toca("Ahora no").toca("Lo decido en el local").toca("Confirmar turno")
    assert cliente_de(sesion, USUARIO).celular is None


def test_escribir_no_tambien_vale(sesion: Session):
    chat = hasta_el_nombre(sesion).escribe("Ana Ruiz").escribe("no")

    assert chat.opciones == ["Automóvil", "Camioneta", "Moto"]


def test_a_quien_escribe_con_su_celular_no_se_le_pide(sesion: Session):
    chat = hasta_el_nombre(sesion, telefono="573001112233").escribe("Pedro Gil")

    assert chat.opciones == ["Automóvil", "Camioneta", "Moto"]


def test_al_cliente_conocido_sin_celular_se_le_pide_al_pedir_turno(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=USUARIO, nombre="Ana Ruiz")

    chat = Chat(sesion, telefono=USUARIO).escribe("hola").toca("Pedir turno")
    assert chat.texto.startswith("Tu WhatsApp no nos muestra tu número.")
    chat.escribe("+57 315 123 4567")

    assert cliente_de(sesion, USUARIO).celular == "573151234567"


def test_si_ya_dio_el_celular_no_se_le_vuelve_a_pedir(sesion: Session):
    cliente_con_vehiculo(sesion, telefono=USUARIO, nombre="Ana Ruiz")
    cliente_de(sesion, USUARIO).celular = "573151234567"
    sesion.flush()

    chat = Chat(sesion, telefono=USUARIO).escribe("hola").toca("Pedir turno")

    assert not chat.texto.startswith("Tu WhatsApp no nos muestra tu número.")


def test_en_el_dashboard_se_puede_llamar_a_ese_celular(adentro, sesion: Session):
    ana = Cliente(telefono=USUARIO, nombre="Ana Ruiz", celular="573151234567")
    sesion.add(ana)
    sesion.flush()
    comentarios.registrar(sesion, TipoComentario.QUIERE_HABLAR, "llámenme", cliente=ana)
    sesion.flush()

    [tarjeta] = comentarios_consultas.lista(sesion, solo_por_atender=False)
    assert tarjeta.llamar_a == "573151234567"
    assert 'href="tel:+573151234567"' in adentro.get("/comentarios").text
    assert 'href="tel:+573151234567"' in adentro.get(f"/clientes/{ana.id}/ficha").text


def test_el_celular_se_borra_con_sus_datos(sesion: Session):
    ana = Cliente(telefono=USUARIO, nombre="Ana Ruiz", celular="573151234567")
    sesion.add(ana)
    sesion.flush()

    clientes.borrar_datos(sesion, ana.id)

    assert sesion.get(Cliente, ana.id).celular is None


def test_la_base_solo_acepta_digitos(sesion: Session):
    with debe_rechazar(sesion, "ck_cliente_celular_solo_digitos"):
        sesion.add(Cliente(telefono=USUARIO, nombre="Ana", celular="315-123-4567"))


# --- Los dibujitos ---------------------------------------------------------------------------


def test_los_vehiculos_llevan_su_dibujito(sesion: Session):
    chat = Chat(sesion).escribe("hola").toca("Servicios y precios")

    titulos = [o.titulo for m in chat.ultimas for o in m.opciones]
    assert titulos == ["🚗 Automóvil", "🚙 Camioneta", "🏍️ Moto"]


def test_el_dibujito_solo_si_cabe_en_el_boton():
    assert con_emoji("Moto", "Moto ABC12D") == "🏍️ Moto ABC12D"
    assert con_emoji("Camioneta", "Camioneta sin placa") == "Camioneta sin placa"  # no cabe
    assert con_emoji("Bicicleta") == "Bicicleta"  # sin dibujito
    assert all(
        len(con_emoji(tipo)) <= LARGO_DE_UN_BOTON for tipo in ("Automóvil", "Camioneta", "Moto")
    )
