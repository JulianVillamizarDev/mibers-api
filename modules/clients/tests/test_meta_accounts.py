"""HU-08: estado de cuentas desde Meta con alertas RF19 y actor sistema."""

from datetime import date
from io import StringIO

import pytest
from django.core.management import call_command

from modules.audit.models import AuditLog
from modules.clients import services
from modules.clients.meta import FakeGraphClient, GraphError
from modules.core import jobs
from modules.notifications.models import Alerta

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def meta_settings(settings):
    settings.META_PUBLISHING = "fake"
    settings.META_SYSTEM_USER_TOKEN = "TOKEN-DE-PRUEBA"
    settings.WHATSAPP_BACKEND = "fake"


@pytest.fixture
def cliente(admin_user):
    return services.create_client(
        acting_user=admin_user,
        nombre="Cafe de Prueba",
        posts_por_semana=1,
        contrato_inicio=date(2026, 1, 1),
        estrategia="POSICIONAR",
    )


def add_account(cliente, admin_user, red="INSTAGRAM", id_externo="IG1"):
    return services.link_account(
        cliente,
        red=red,
        handle=f"@{red.lower()}",
        url_perfil=f"https://{red.lower()}.com/cuenta",
        id_externo=id_externo,
        acting_user=admin_user,
    )


@pytest.fixture
def fake_meta(monkeypatch):
    fake = FakeGraphClient()
    monkeypatch.setattr(services, "get_graph_client", lambda: fake)
    return fake


def test_cuentas_expiradas_o_desvinculadas_vuelven_a_vinculadas(cliente, admin_user, fake_meta):
    cuenta = add_account(cliente, admin_user, red="INSTAGRAM", id_externo="IG1")
    services.update_account(cuenta, estado="EXPIRADA", acting_user=admin_user)
    alerts_before = Alerta.objects.count()
    fake_meta.responses = [{"username": "cuenta.recuperada"}]

    result = services.verify_meta_accounts()

    cuenta.refresh_from_db()
    assert cuenta.estado == "VINCULADA"
    assert result["vinculadas"] == 1 and result["revisadas"] == 1
    assert Alerta.objects.count() == alerts_before  # recuperarse no alerta
    audit = AuditLog.objects.filter(action="NETWORK_STATUS_CHANGED", metadata__a="VINCULADA").latest(
        "created_at"
    )
    assert audit.actor is None and audit.actor_username == "sistema"


@pytest.mark.parametrize("code", [10, 100, 200, 250, 299])
def test_codigo_sin_permiso_o_inexistente_desvincula(cliente, admin_user, fake_meta, code):
    cuenta = add_account(cliente, admin_user, red="INSTAGRAM", id_externo="IG1")
    fake_meta.responses = [GraphError("sin permiso", code=code, retryable=False)]

    result = services.verify_meta_accounts()

    cuenta.refresh_from_db()
    assert cuenta.estado == "DESVINCULADA" and result["desvinculadas"] == 1
    assert Alerta.objects.filter(tipo="CUENTA_DESVINCULADA").count() == 1
    audit = AuditLog.objects.filter(action="NETWORK_STATUS_CHANGED", metadata__a="DESVINCULADA").latest(
        "created_at"
    )
    assert audit.actor is None and audit.actor_username == "sistema"


def test_token_invalido_expira_todas_las_cuentas(cliente, admin_user, fake_meta):
    ig = add_account(cliente, admin_user, red="INSTAGRAM", id_externo="IG1")
    fb = add_account(cliente, admin_user, red="FACEBOOK", id_externo="FB1")
    fake_meta.responses = [GraphError("token invalido", code=190, retryable=False)]

    result = services.verify_meta_accounts()

    ig.refresh_from_db()
    fb.refresh_from_db()
    assert ig.estado == fb.estado == "EXPIRADA"
    assert result["expiradas"] == 2 and result["revisadas"] == 2
    assert Alerta.objects.filter(tipo="CUENTA_EXPIRADA").count() == 2


@pytest.mark.parametrize(
    "error",
    [
        GraphError("sin red", retryable=True),
        GraphError("Meta 502", code=500, retryable=True),
    ],
)
def test_error_de_red_o_5xx_no_cambia_el_estado(cliente, admin_user, fake_meta, error):
    cuenta = add_account(cliente, admin_user, red="INSTAGRAM", id_externo="IG1")
    fake_meta.responses = [error]

    result = services.verify_meta_accounts()

    cuenta.refresh_from_db()
    assert cuenta.estado == "VINCULADA"
    assert result["sin_cambios"] == 1 and result["expiradas"] == 0
    assert not Alerta.objects.exists()


def test_modo_manual_no_llama_a_meta(cliente, admin_user, fake_meta, settings):
    add_account(cliente, admin_user, red="INSTAGRAM", id_externo="IG1")
    settings.META_PUBLISHING = "manual"

    result = services.verify_meta_accounts()

    assert result["omitida"] == "META_PUBLISHING=manual"
    assert fake_meta.calls == []


def test_sin_token_no_llama_a_meta(cliente, admin_user, fake_meta, settings):
    add_account(cliente, admin_user, red="INSTAGRAM", id_externo="IG1")
    settings.META_SYSTEM_USER_TOKEN = ""

    result = services.verify_meta_accounts()

    assert result["omitida"] == "sin META_SYSTEM_USER_TOKEN"
    assert fake_meta.calls == []


def test_tarea_periodica_cada_seis_horas():
    every, handler = jobs._PERIODIC["clients.verificar_cuentas_meta"]
    assert every == 6 * 3600
    assert handler is services.verify_meta_accounts


def test_comando_meta_verificar_cuentas_muestra_resumen(cliente, admin_user, fake_meta):
    add_account(cliente, admin_user, red="INSTAGRAM", id_externo="IG1")
    fake_meta.responses = [{"username": "cuenta.ok"}]
    output = StringIO()

    call_command("meta_verificar_cuentas", stdout=output)

    text = output.getvalue()
    assert "Cuentas revisadas: 1" in text
    assert "Sin cambios: 1" in text
