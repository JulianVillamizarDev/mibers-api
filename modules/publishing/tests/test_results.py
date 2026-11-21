"""RF17: resultado por red (publicada con enlace / fallida con motivo) y reintento."""

from datetime import timedelta

import pytest
from django.utils import timezone

from modules.audit.models import AuditLog
from modules.clients.models import CuentaSocial
from modules.content import services as content
from modules.core import jobs
from modules.notifications.gateway import FakeGateway
from modules.notifications.models import Alerta
from modules.publishing import services
from modules.publishing.models import TareaPublicacion

from .helpers import build_piece, make_client_with_accounts, reload

pytestmark = pytest.mark.django_db

IG_LINK = "https://www.instagram.com/p/ABC123/"
FB_LINK = "https://www.facebook.com/cafedelvalle/posts/123"


@pytest.fixture(autouse=True)
def fake_whatsapp(settings):
    settings.WHATSAPP_BACKEND = "fake"
    FakeGateway.reset()
    yield
    FakeGateway.reset()


@pytest.fixture
def cliente():
    return make_client_with_accounts()


@pytest.fixture
def due(cliente, staff_user, admin_user):
    """Pieza en IG y FB cuya hora ya llego y con sus tareas creadas."""
    when = timezone.now() + timedelta(hours=1)
    piece = build_piece(cliente, staff_user, admin_user, redes=("INSTAGRAM", "FACEBOOK"), when=when)
    services.schedule_piece(piece, acting_user=staff_user, confirmed=True)
    services.run_tick(now=when + timedelta(seconds=30))
    return piece


def task(piece, red):
    return TareaPublicacion.objects.get(pieza=piece, red=red)


def url(t, action):
    return f"/api/v1/publicaciones/{t.pk}/{action}"


# --- Bandeja de pendientes ------------------------------------------------------------------------------------
def test_la_bandeja_lista_lo_pendiente_con_cliente_cuenta_y_copy(staff_client, due):
    body = staff_client.get("/api/v1/publicaciones", {"estado": "PENDIENTE"}).json()
    assert body["count"] == 2
    row = next(r for r in body["results"] if r["red"] == "INSTAGRAM")
    assert row["cliente_nombre"] == "Cafe del Valle" and row["cuenta_handle"] == "@cafe_instagram"
    assert row["copy_final"] == "Ven por tu cafe" and row["estado"] == "PENDIENTE"


def test_filtros_de_la_bandeja(staff_client, due, cliente):
    assert staff_client.get("/api/v1/publicaciones", {"red": "FACEBOOK"}).json()["count"] == 1
    assert staff_client.get("/api/v1/publicaciones", {"cliente": str(cliente.pk)}).json()["count"] == 2
    assert staff_client.get("/api/v1/publicaciones", {"estado": "PUBLICADA"}).json()["count"] == 0


def test_resultado_por_red_de_una_pieza(staff_client, due):
    assert len(staff_client.get(f"/api/v1/piezas/{due.pk}/publicaciones").json()) == 2


def test_la_bandeja_exige_sesion(api_client, due):
    assert api_client.get("/api/v1/publicaciones").status_code == 401


# --- Confirmar publicacion con enlace -----------------------------------------------------------------------------
def test_confirmar_registra_enlace_y_la_pieza_pasa_a_publicada(staff_client, due):
    response = staff_client.post(url(task(due, "INSTAGRAM"), "confirmar"), {"enlace": IG_LINK}, format="json")
    assert response.status_code == 200
    body = response.json()
    assert body["estado"] == "PUBLICADA" and body["enlace"] == IG_LINK and body["publicada_at"]
    assert body["gestionada_por_username"] == "copy1"
    assert reload(due).estado == "PUBLICADA"  # basta una red (RF18)
    assert task(due, "FACEBOOK").estado == "PENDIENTE"  # la otra sigue pendiente
    entry = AuditLog.objects.get(action="PUBLICATION_PUBLISHED")
    assert entry.metadata["enlace"] == IG_LINK and entry.metadata["red"] == "INSTAGRAM"


@pytest.mark.parametrize(
    "link",
    [
        "http://www.instagram.com/p/ABC/",  # sin https
        "https://www.facebook.com/p/1",  # de otra red
        "https://evil.com/instagram.com/p/1",  # dominio enganoso
        "https://instagram.com.evil.com/p/1",
        "no-es-url",
    ],
)
def test_el_enlace_debe_ser_de_la_red_de_la_tarea(staff_client, due, link):
    response = staff_client.post(url(task(due, "INSTAGRAM"), "confirmar"), {"enlace": link}, format="json")
    assert response.status_code == 400
    assert task(due, "INSTAGRAM").estado == "PENDIENTE"


def test_acepta_subdominios_y_dominios_cortos_de_facebook(staff_client, due):
    for link in ("https://m.facebook.com/story.php?id=1", "https://fb.watch/abc/"):
        response = staff_client.post(url(task(due, "FACEBOOK"), "confirmar"), {"enlace": link}, format="json")
        assert response.status_code == 200
        TareaPublicacion.objects.filter(pk=response.json()["id"]).update(estado="PENDIENTE")


def test_no_se_confirma_dos_veces(staff_client, due):
    t = task(due, "INSTAGRAM")
    staff_client.post(url(t, "confirmar"), {"enlace": IG_LINK}, format="json")
    again = staff_client.post(url(t, "confirmar"), {"enlace": IG_LINK}, format="json")
    assert again.status_code == 409 and again.json()["code"] == "invalid_task_state"


def test_dos_redes_confirmadas_dejan_ambas_publicadas(staff_client, due):
    staff_client.post(url(task(due, "INSTAGRAM"), "confirmar"), {"enlace": IG_LINK}, format="json")
    staff_client.post(url(task(due, "FACEBOOK"), "confirmar"), {"enlace": FB_LINK}, format="json")
    assert set(TareaPublicacion.objects.values_list("estado", flat=True)) == {"PUBLICADA"}
    assert reload(due).estado == "PUBLICADA"


# --- Reportar fallo con motivo ----------------------------------------------------------------------------------------
def test_fallo_exige_motivo(staff_client, due):
    response = staff_client.post(url(task(due, "INSTAGRAM"), "fallar"), {"motivo": "  "}, format="json")
    assert response.status_code == 400 and response.json()["code"] == "reason_required"


def test_fallo_en_una_red_alerta_y_la_pieza_sigue_programada(staff_client, due):
    response = staff_client.post(
        url(task(due, "INSTAGRAM"), "fallar"), {"motivo": "Instagram rechazo el video"}, format="json"
    )
    assert response.status_code == 200
    assert (
        response.json()["estado"] == "FALLIDA" and response.json()["motivo"] == "Instagram rechazo el video"
    )
    assert reload(due).estado == "PROGRAMADA"  # la otra red aun esta pendiente
    alert = Alerta.objects.get(tipo="PUBLICACION_FALLIDA")
    assert "Instagram rechazo el video" in alert.mensaje and "Cafe del Valle" in alert.mensaje
    assert AuditLog.objects.filter(action="PUBLICATION_FAILED").count() == 1


def test_si_todas_las_redes_fallan_la_pieza_queda_fallida(staff_client, due):
    for red in ("INSTAGRAM", "FACEBOOK"):
        staff_client.post(url(task(due, red), "fallar"), {"motivo": "sin conexion"}, format="json")
    assert reload(due).estado == "FALLIDA"
    assert Alerta.objects.filter(tipo="PUBLICACION_FALLIDA").count() == 2


def test_solo_se_reporta_fallo_de_una_tarea_pendiente(staff_client, due):
    t = task(due, "INSTAGRAM")
    staff_client.post(url(t, "confirmar"), {"enlace": IG_LINK}, format="json")
    assert staff_client.post(url(t, "fallar"), {"motivo": "x"}, format="json").status_code == 409


# --- Reintentar --------------------------------------------------------------------------------------------------------------
def test_reintentar_deja_la_tarea_pendiente_y_vuelve_a_avisar(staff_client, due, staff_user):
    staff_user.whatsapp_number = "+573004440000"
    staff_user.save()
    t = task(due, "INSTAGRAM")
    staff_client.post(url(t, "fallar"), {"motivo": "token"}, format="json")
    response = staff_client.post(url(t, "reintentar"))
    assert response.status_code == 200
    body = response.json()
    assert body["estado"] == "PENDIENTE" and body["motivo"] == "" and body["intentos"] == 1
    assert AuditLog.objects.filter(action="PUBLICATION_RETRIED").count() == 1
    jobs.process_once()
    assert any("Instagram" in m["body_params"][0] for m in FakeGateway.sent)


def test_reintentar_una_pieza_fallida_la_vuelve_a_programar_y_se_puede_publicar(staff_client, due):
    for red in ("INSTAGRAM", "FACEBOOK"):
        staff_client.post(url(task(due, red), "fallar"), {"motivo": "x"}, format="json")
    assert reload(due).estado == "FALLIDA"
    staff_client.post(url(task(due, "INSTAGRAM"), "reintentar"))
    assert reload(due).estado == "PROGRAMADA"
    staff_client.post(url(task(due, "INSTAGRAM"), "confirmar"), {"enlace": IG_LINK}, format="json")
    assert reload(due).estado == "PUBLICADA"


def test_una_red_fallida_se_puede_confirmar_directamente(staff_client, due):
    t = task(due, "FACEBOOK")
    staff_client.post(url(t, "fallar"), {"motivo": "x"}, format="json")
    assert staff_client.post(url(t, "confirmar"), {"enlace": FB_LINK}, format="json").status_code == 200


def test_solo_se_reintentan_las_fallidas(staff_client, due):
    response = staff_client.post(url(task(due, "INSTAGRAM"), "reintentar"))
    assert response.status_code == 409 and response.json()["code"] == "invalid_task_state"


def test_no_se_reintenta_con_la_cuenta_expirada(staff_client, due, cliente):
    t = task(due, "INSTAGRAM")
    staff_client.post(url(t, "fallar"), {"motivo": "token vencido"}, format="json")
    CuentaSocial.objects.filter(cliente=cliente, red="INSTAGRAM").update(estado="EXPIRADA")
    response = staff_client.post(url(t, "reintentar"))
    assert response.status_code == 400 and response.json()["code"] == "account_not_linked"


def test_reintentos_distintos_generan_alertas_distintas(staff_client, due):
    t = task(due, "INSTAGRAM")
    for _ in range(2):
        staff_client.post(url(t, "fallar"), {"motivo": "x"}, format="json")
        staff_client.post(url(t, "reintentar"))
    staff_client.post(url(t, "fallar"), {"motivo": "x"}, format="json")
    assert Alerta.objects.filter(tipo="PUBLICACION_FALLIDA").count() == 3


# --- Una pieza que vuelve a revision no se publica ----------------------------------------------------------------------------
def test_editar_la_pieza_cancela_las_tareas_pendientes(due, staff_user):
    assert TareaPublicacion.objects.filter(estado="PENDIENTE").count() == 2
    content.update_piece(due, acting_user=staff_user, copy_comun="Cambio sin aprobar")
    assert reload(due).estado == "EN_REVISION"
    assert set(TareaPublicacion.objects.values_list("estado", flat=True)) == {"CANCELADA"}


def test_tras_reaprobar_y_reprogramar_se_generan_tareas_nuevas(due, staff_user, admin_user):
    when = timezone.now() + timedelta(hours=3)
    content.update_piece(due, acting_user=staff_user, copy_comun="Nuevo copy", programada_para=when)
    # editar la dejo EN_REVISION: el admin la vuelve a aprobar directamente
    content.approve(due, acting_user=admin_user)
    services.schedule_piece(due, acting_user=staff_user, confirmed=True)
    services.run_tick(now=when + timedelta(seconds=30))
    tasks = TareaPublicacion.objects.all()
    assert tasks.count() == 2 and set(tasks.values_list("estado", flat=True)) == {"PENDIENTE"}
    assert {t.copy_final for t in tasks} == {"Nuevo copy"}


def test_cancelar_programacion_cancela_las_tareas(staff_client, due):
    response = staff_client.post(f"/api/v1/piezas/{due.pk}/cancelar-programacion")
    assert response.status_code == 200
    assert set(TareaPublicacion.objects.values_list("estado", flat=True)) == {"CANCELADA"}


def test_no_se_cancela_la_programacion_si_ya_se_publico(staff_client, due):
    staff_client.post(url(task(due, "INSTAGRAM"), "confirmar"), {"enlace": IG_LINK}, format="json")
    response = staff_client.post(f"/api/v1/piezas/{due.pk}/cancelar-programacion")
    assert response.status_code == 409
