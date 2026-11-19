"""RF15 / RNF06: el scheduler crea las tareas a la hora, bloquea lo no aprobado y alerta."""

from datetime import timedelta

import pytest
from django.utils import timezone

from modules.audit.models import AuditLog
from modules.clients.models import CuentaSocial
from modules.content import services as content
from modules.content.models import Pieza
from modules.core import jobs
from modules.notifications.gateway import FakeGateway
from modules.notifications.models import Alerta
from modules.publishing import services
from modules.publishing.models import TareaPublicacion

from .helpers import build_piece, make_client_with_accounts, reload

pytestmark = pytest.mark.django_db


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
def when():
    return timezone.now() + timedelta(days=2)


@pytest.fixture
def scheduled(cliente, staff_user, admin_user, when):
    staff_user.whatsapp_number = "+573004440000"
    staff_user.save()
    piece = build_piece(cliente, staff_user, admin_user, redes=("INSTAGRAM", "FACEBOOK"), when=when)
    return services.schedule_piece(piece, acting_user=staff_user, confirmed=True)


def tick(when, seconds=60):
    return services.run_tick(now=when + timedelta(seconds=seconds))


# --- RF15: a la hora programada se crean las tareas por red ---------------------------------------------
def test_antes_de_la_hora_no_pasa_nada(scheduled, when):
    assert tick(when, seconds=-1)["piezas"] == 0
    assert TareaPublicacion.objects.count() == 0


def test_a_la_hora_crea_una_tarea_por_red_con_la_cuenta_y_el_copy_de_cada_una(scheduled, when, cliente):
    assert tick(when)["piezas"] == 1
    tasks = {t.red: t for t in TareaPublicacion.objects.all()}
    assert set(tasks) == {"INSTAGRAM", "FACEBOOK"}
    for red, task in tasks.items():
        assert task.estado == "PENDIENTE" and task.pieza_id == scheduled.pk
        assert task.cuenta.cliente_id == cliente.pk  # RNF09: cuenta del cliente
        assert task.cuenta.red == red and task.cuenta_handle == f"@cafe_{red.lower()}"
        assert task.copy_final == "Ven por tu cafe" and task.programada_para == when
    assert "PUBLICATION_DUE" in AuditLog.objects.values_list("action", flat=True)
    assert reload(scheduled).estado == "PROGRAMADA"  # sigue programada hasta que alguien confirme


def test_el_tick_es_idempotente(scheduled, when):
    tick(when)
    assert tick(when, seconds=120)["piezas"] == 0
    assert TareaPublicacion.objects.count() == 2


def test_avisa_al_responsable_por_whatsapp(scheduled, when):
    tick(when)
    jobs.process_once(now=when + timedelta(seconds=70))
    sent = FakeGateway.sent
    assert [m["to"] for m in sent] == ["+573004440000", "+573004440000"]
    assert all(m["template"] == "recordatorio_publicacion" for m in sent)
    assert "Cafe del Valle" in sent[0]["body_params"][0]


def test_la_tarea_registra_cuando_se_aviso_para_medir_rnf06(scheduled, when):
    tick(when, seconds=90)
    assert all(t.avisada_at == when + timedelta(seconds=90) for t in TareaPublicacion.objects.all())


def test_el_worker_ejecuta_el_tick_como_tarea_periodica(scheduled, when, monkeypatch):
    from modules.publishing import handlers

    calls = []
    monkeypatch.setattr(services, "run_tick", lambda now=None: calls.append(1))
    jobs.run_periodic_due(now=when)
    assert calls == [1] and "publishing.tick" in jobs._PERIODIC and handlers.TICK_SECONDS == 60


# --- RF15: sin aprobacion se bloquea la publicacion y se alerta -------------------------------------------
@pytest.mark.parametrize(
    ("estado", "fragmento"),
    [
        ("BORRADOR", "borrador"),
        ("EN_REVISION", "no fue aprobada"),
        ("CAMBIOS_SOLICITADOS", "cambios solicitados"),
        ("APROBADA", "no fue programada"),
    ],
)
def test_pieza_no_programada_o_sin_aprobacion_se_bloquea_y_alerta(
    cliente, staff_user, admin_user, when, estado, fragmento
):
    piece = build_piece(cliente, staff_user, admin_user, when=when, approve=False)
    Pieza.objects.filter(pk=piece.pk).update(estado=estado)
    tick(when)
    assert TareaPublicacion.objects.count() == 0  # NO se publica
    alert = Alerta.objects.get()
    assert (
        alert.tipo == "PUBLICACION_BLOQUEADA"
        and fragmento in alert.mensaje
        and "Cafe del Valle" in alert.mensaje
    )
    assert AuditLog.objects.filter(action="PUBLICATION_BLOCKED").count() == 1


def test_la_alerta_de_bloqueo_no_se_repite_en_cada_ciclo(cliente, staff_user, admin_user, when):
    build_piece(cliente, staff_user, admin_user, when=when, approve=False)
    for minutes in range(1, 6):
        tick(when, seconds=60 * minutes)
    assert Alerta.objects.count() == 1 and AuditLog.objects.filter(action="PUBLICATION_BLOCKED").count() == 1


def test_el_bloqueo_alerta_por_whatsapp_al_admin(cliente, staff_user, admin_user, when):
    admin_user.whatsapp_number = "+573001110000"
    admin_user.save()
    build_piece(cliente, staff_user, admin_user, when=when, approve=False)
    tick(when)
    jobs.process_once(now=when + timedelta(seconds=70))
    assert FakeGateway.sent[0]["to"] == "+573001110000"
    assert "Publicacion bloqueada" in FakeGateway.sent[0]["body_params"][0]


def test_si_la_cuenta_se_desvincula_antes_de_la_hora_se_bloquea(scheduled, when, cliente):
    CuentaSocial.objects.filter(cliente=cliente, red="FACEBOOK").update(estado="EXPIRADA")
    tick(when)
    assert TareaPublicacion.objects.count() == 0
    assert "Facebook" in Alerta.objects.get(tipo="PUBLICACION_BLOQUEADA").mensaje


def test_si_el_contenido_dejo_de_ser_valido_se_bloquea(scheduled, when):
    Pieza.objects.filter(pk=scheduled.pk).update(copy_comun="")
    tick(when)
    assert TareaPublicacion.objects.count() == 0 and Alerta.objects.count() == 1


def test_pieza_editada_tras_programar_vuelve_a_revision_y_no_se_publica(scheduled, when, staff_user):
    content.update_piece(scheduled, acting_user=staff_user, copy_comun="Copy distinto sin aprobar")
    assert reload(scheduled).estado == "EN_REVISION"
    tick(when)
    assert TareaPublicacion.objects.count() == 0
    assert "no fue aprobada" in Alerta.objects.get().mensaje


def test_piezas_ya_publicadas_o_viejas_se_ignoran(cliente, staff_user, admin_user, when):
    done = build_piece(cliente, staff_user, admin_user, when=when, approve=False)
    Pieza.objects.filter(pk=done.pk).update(estado="PUBLICADA")
    old = build_piece(cliente, staff_user, admin_user, when=when - timedelta(days=30), approve=False)
    assert old.estado == "BORRADOR"
    tick(when)
    assert Alerta.objects.count() == 0


def test_un_error_en_una_pieza_no_frena_a_las_demas(cliente, staff_user, admin_user, when, monkeypatch):
    first = build_piece(cliente, staff_user, admin_user, when=when)
    second = build_piece(cliente, staff_user, admin_user, when=when)
    for piece in (first, second):
        services.schedule_piece(piece, acting_user=staff_user, confirmed=True)
    original = services.process_due_piece

    def flaky(pieza, now):
        if pieza.pk == first.pk:
            raise RuntimeError("falla simulada")
        return original(pieza, now)

    monkeypatch.setattr(services, "process_due_piece", flaky)
    tick(when)
    assert list(TareaPublicacion.objects.values_list("pieza_id", flat=True)) == [second.pk]


# --- Tareas atrasadas -------------------------------------------------------------------------------------------------
def test_tarea_sin_confirmar_alerta_una_sola_vez_pasado_el_plazo(scheduled, when, settings):
    settings.PUBLICATION_OVERDUE_MINUTES = 30
    tick(when)
    assert services.alert_overdue(when + timedelta(minutes=29)) == 0
    assert services.alert_overdue(when + timedelta(minutes=31)) == 2  # una por red
    assert services.alert_overdue(when + timedelta(minutes=60)) == 0
    assert Alerta.objects.filter(tipo="PUBLICACION_ATRASADA").count() == 2


def test_tarea_confirmada_no_genera_alerta_de_atraso(scheduled, when, staff_user):
    tick(when)
    for task in TareaPublicacion.objects.all():
        services.confirm_published(
            task, enlace=f"https://www.{task.red.lower()}.com/p/1", acting_user=staff_user
        )
    assert services.alert_overdue(when + timedelta(hours=2)) == 0


# --- RNF06: cumplimiento de horario ---------------------------------------------------------------------------------------
def test_cumplimiento_de_horario(cliente, staff_user, admin_user, when):
    # 20 piezas separadas 20 min: 19 se avisan dentro de +-5 min y una 10 min tarde -> 95 %
    for i in range(20):
        piece = build_piece(cliente, staff_user, admin_user, when=when + timedelta(minutes=20 * i))
        services.schedule_piece(piece, acting_user=staff_user, confirmed=True)
    for i in range(20):
        delay = 600 if i == 0 else 30
        services.run_tick(now=when + timedelta(minutes=20 * i, seconds=delay))
    result = services.punctuality(when - timedelta(hours=1), when + timedelta(hours=10))
    assert result == {"total": 20, "dentro_de_5_min": 19, "porcentaje": 95.0, "meta": 95}


def test_endpoint_de_cumplimiento_solo_para_el_admin(admin_client, staff_client):
    assert staff_client.get("/api/v1/publicaciones/cumplimiento").status_code == 403
    body = admin_client.get("/api/v1/publicaciones/cumplimiento").json()
    assert body["total"] == 0 and body["porcentaje"] is None
