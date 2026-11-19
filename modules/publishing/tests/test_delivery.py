"""RF18: envio al WhatsApp del cliente solo tras confirmarse la publicacion."""

from datetime import timedelta

import pytest
from django.utils import timezone

from modules.clients import services as clients
from modules.core import jobs
from modules.notifications import services as notifications
from modules.notifications.gateway import FakeGateway, WhatsAppError
from modules.notifications.models import Alerta, EnvioWhatsApp
from modules.publishing import services
from modules.publishing.models import TareaPublicacion

from .helpers import add_contact, build_piece, make_client_with_accounts, reload

pytestmark = pytest.mark.django_db

IG_LINK = "https://www.instagram.com/p/ABC123/"


@pytest.fixture(autouse=True)
def fake_whatsapp(settings):
    settings.WHATSAPP_BACKEND = "fake"
    FakeGateway.reset()
    yield
    FakeGateway.reset()


@pytest.fixture
def cliente():
    cliente = make_client_with_accounts(networks=("INSTAGRAM", "FACEBOOK"))
    add_contact(cliente, "+573001112233", "Laura")
    return cliente


def due_piece(cliente, staff_user, admin_user, *, when=None, redes=("INSTAGRAM",), **kwargs):
    when = when or timezone.now() + timedelta(hours=1)
    piece = build_piece(cliente, staff_user, admin_user, redes=redes, when=when, **kwargs)
    services.schedule_piece(piece, acting_user=staff_user, confirmed=True)
    services.run_tick(now=when + timedelta(seconds=30))
    return piece, when


def confirm(piece, red="INSTAGRAM", user=None):
    task = TareaPublicacion.objects.get(pieza=piece, red=red)
    link = IG_LINK if red == "INSTAGRAM" else "https://www.facebook.com/x/posts/1"
    return services.confirm_published(task, enlace=link, acting_user=user)


def client_envios(piece):
    return EnvioWhatsApp.objects.filter(entity_id=str(piece.pk), proposito="CLIENTE")


# --- solo despues de confirmarse publicada -------------------------------------------------------------------------
def test_no_se_envia_nada_antes_de_confirmar_la_publicacion(cliente, staff_user, admin_user):
    piece, when = due_piece(cliente, staff_user, admin_user)
    jobs.process_once(now=when + timedelta(hours=1))
    assert client_envios(piece).count() == 0
    assert [m for m in FakeGateway.sent if m["template"].startswith("pieza_cliente")] == []


def test_al_confirmar_se_encola_el_archivo_y_el_mensaje_para_los_contactos(cliente, staff_user, admin_user):
    piece, when = due_piece(cliente, staff_user, admin_user, mensaje_cliente="Hola Laura, aqui va tu post")
    confirm(piece, user=staff_user)
    envio = client_envios(piece).get()
    assert (
        envio.estado == "PENDIENTE" and envio.destino == "+573001112233" and envio.destino_nombre == "Laura"
    )
    assert envio.parametros == ["Hola Laura, aqui va tu post"] and envio.media_kind == "image"
    assert envio.media_ruta.startswith(f"clientes/{cliente.pk}/piezas/{piece.pk}/")
    assert reload(piece).estado == "PUBLICADA"


def test_el_envio_sale_dentro_de_la_ventana_y_la_pieza_queda_compartida(cliente, staff_user, admin_user):
    piece, when = due_piece(cliente, staff_user, admin_user)
    confirm(piece, user=staff_user)
    jobs.process_once(now=when + timedelta(minutes=1))
    sent = [m for m in FakeGateway.sent if m["template"] == "pieza_cliente_imagen"]
    assert len(sent) == 1 and sent[0]["to"] == "+573001112233" and sent[0]["header"].kind == "image"
    assert client_envios(piece).get().estado == "ENVIADO"
    assert reload(piece).estado == "COMPARTIDA"


def test_si_se_publica_antes_de_la_hora_el_envio_espera_a_la_hora_programada(cliente, staff_user, admin_user):
    when = timezone.now() + timedelta(hours=2)
    piece = build_piece(cliente, staff_user, admin_user, when=when)
    services.schedule_piece(piece, acting_user=staff_user, confirmed=True)
    services.run_tick(now=when)  # el equipo adelanta la publicacion (tick simulado a la hora)
    confirm(piece, user=staff_user)
    jobs.process_once()  # ahora: aun falta para la hora programada
    assert client_envios(piece).get().estado == "PENDIENTE"
    jobs.process_once(now=when + timedelta(seconds=5))
    assert client_envios(piece).get().estado == "ENVIADO"


def test_se_envia_a_cada_contacto_autorizado_y_solo_a_ellos(cliente, staff_user, admin_user):
    add_contact(cliente, "+573009998877", "Pedro")
    add_contact(cliente, "+573005556677", "Sin autorizacion", autorizado=False)
    piece, when = due_piece(cliente, staff_user, admin_user)
    confirm(piece, user=staff_user)
    jobs.process_once(now=when + timedelta(minutes=1))
    recipients = sorted(m["to"] for m in FakeGateway.sent if m["template"].startswith("pieza_cliente"))
    assert recipients == ["+573001112233", "+573009998877"]  # RNF13: sin autorizacion, no se envia


def test_pieza_con_varios_archivos_envia_uno_por_archivo(cliente, staff_user, admin_user):
    piece, when = due_piece(
        cliente, staff_user, admin_user, tipo="CARRUSEL", n_files=3, mensaje_cliente="Carrusel nuevo"
    )
    confirm(piece, user=staff_user)
    envios = list(client_envios(piece).order_by("created_at"))
    assert [e.parametros[0] for e in envios] == ["Carrusel nuevo", "Archivo 2 de 3", "Archivo 3 de 3"]


def test_mensaje_por_defecto_si_la_pieza_no_tiene_mensaje_para_el_cliente(cliente, staff_user, admin_user):
    piece, when = due_piece(cliente, staff_user, admin_user)
    confirm(piece, user=staff_user)
    assert "ya esta publicado" in client_envios(piece).get().parametros[0]


def test_confirmar_la_segunda_red_no_duplica_el_envio(cliente, staff_user, admin_user):
    piece, when = due_piece(cliente, staff_user, admin_user, redes=("INSTAGRAM", "FACEBOOK"))
    confirm(piece, "INSTAGRAM", staff_user)
    confirm(piece, "FACEBOOK", staff_user)
    assert client_envios(piece).count() == 1


def test_reintentar_y_confirmar_una_red_fallida_no_duplica_el_envio(cliente, staff_user, admin_user):
    piece, when = due_piece(cliente, staff_user, admin_user, redes=("INSTAGRAM", "FACEBOOK"))
    confirm(piece, "INSTAGRAM", staff_user)
    fb = TareaPublicacion.objects.get(pieza=piece, red="FACEBOOK")
    services.report_failed(fb, motivo="x", acting_user=staff_user)
    services.retry(fb, acting_user=staff_user)
    confirm(piece, "FACEBOOK", staff_user)
    assert client_envios(piece).count() == 1


# --- Registro de entrega y fallos ----------------------------------------------------------------------------------------------
def test_se_registra_si_se_entrego(cliente, staff_user, admin_user):
    piece, when = due_piece(cliente, staff_user, admin_user)
    confirm(piece, user=staff_user)
    jobs.process_once(now=when + timedelta(minutes=1))
    envio = client_envios(piece).get()
    notifications.apply_status_update(envio.wa_message_id, "delivered")
    envio.refresh_from_db()
    assert envio.estado == "ENTREGADO" and envio.entregado_at
    assert reload(piece).estado == "COMPARTIDA"  # la entrega no la retrocede


def test_un_fallo_de_envio_alerta_y_la_pieza_sigue_publicada(
    cliente, staff_user, admin_user, admin_with_number=None
):
    piece, when = due_piece(cliente, staff_user, admin_user)
    confirm(piece, user=staff_user)
    FakeGateway.fail_with = WhatsAppError("numero sin WhatsApp", retryable=False)
    jobs.process_once(now=when + timedelta(minutes=1))
    assert client_envios(piece).get().estado == "FALLIDO"
    assert reload(piece).estado == "PUBLICADA"
    assert Alerta.objects.filter(tipo="ENVIO_CLIENTE_FALLIDO").count() == 1


def test_si_un_contacto_falla_y_otro_no_la_pieza_no_pasa_a_compartida(cliente, staff_user, admin_user):
    add_contact(cliente, "+573009998877", "Pedro")
    piece, when = due_piece(cliente, staff_user, admin_user)
    confirm(piece, user=staff_user)
    FakeGateway.fail_with = WhatsAppError("rechazado", retryable=False)  # falla solo el primero
    jobs.process_once(now=when + timedelta(minutes=1))
    assert sorted(client_envios(piece).values_list("estado", flat=True)) == ["ENVIADO", "FALLIDO"]
    assert reload(piece).estado == "PUBLICADA"


def test_sin_contactos_autorizados_se_alerta_en_vez_de_enviar(staff_user, admin_user):
    cliente = make_client_with_accounts("Sin Contactos", networks=("INSTAGRAM",))
    add_contact(cliente, "+573001112233", "Laura", autorizado=False)
    piece, when = due_piece(cliente, staff_user, admin_user)
    confirm(piece, user=staff_user)
    assert client_envios(piece).count() == 0
    alert = Alerta.objects.get(tipo="ENVIO_CLIENTE_FALLIDO")
    assert "no hay contactos de WhatsApp autorizados" in alert.mensaje
    assert reload(piece).estado == "PUBLICADA"


def test_el_contacto_revocado_antes_de_confirmar_no_recibe_nada(cliente, staff_user, admin_user):
    piece, when = due_piece(cliente, staff_user, admin_user)
    contacto = cliente.contactos.get()
    clients.update_contact(contacto, acting_user=staff_user, autorizado=False)
    confirm(piece, user=staff_user)
    assert client_envios(piece).count() == 0
