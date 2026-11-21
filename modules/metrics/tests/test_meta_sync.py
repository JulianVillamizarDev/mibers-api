"""PLN0002 T2: metricas IG/FB desde Meta con respaldo manual (HU-24)."""

from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from modules.audit.models import AuditLog
from modules.clients import services as clients
from modules.clients.meta import FakeGraphClient, GraphError
from modules.content.tests.helpers import make_client
from modules.core import jobs
from modules.metrics import services
from modules.metrics.models import MetricaMensual

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def sync_settings(settings):
    settings.META_PUBLISHING = "fake"
    settings.META_SYSTEM_USER_TOKEN = "TOKEN-DE-PRUEBA"


@pytest.fixture
def cliente():
    return make_client("Metricas del Valle")


@pytest.fixture
def ig_account(cliente, admin_user):
    return clients.link_account(
        cliente,
        red="INSTAGRAM",
        handle="metricas",
        url_perfil="https://instagram.com/metricas",
        id_externo="IG1",
        acting_user=admin_user,
    )


@pytest.fixture
def fb_account(cliente, admin_user):
    return clients.link_account(
        cliente,
        red="FACEBOOK",
        handle="Metricas FB",
        url_perfil="https://facebook.com/metricas",
        id_externo="FB1",
        acting_user=admin_user,
    )


@pytest.fixture
def fake_meta(monkeypatch):
    fake = FakeGraphClient()
    monkeypatch.setattr(services, "get_graph_client", lambda: fake)
    return fake


def insight(name, value):
    return {"data": [{"name": name, "total_value": {"value": value}}]}


def test_instagram_mapea_followers_views_reach_e_interacciones(cliente, ig_account, fake_meta):
    fake_meta.responses = [
        {"followers_count": 4500},
        insight("views", 30000),
        insight("reach", 12000),
        insight("total_interactions", 900),
    ]

    result = services.sync_month(cliente=cliente, mes=date(2026, 2, 1))

    metrica = MetricaMensual.objects.get(cliente=cliente, red="INSTAGRAM", mes=date(2026, 2, 1))
    assert (metrica.seguidores, metrica.visualizaciones, metrica.alcance, metrica.interacciones) == (
        4500,
        30000,
        12000,
        900,
    )
    assert metrica.origen == "API" and metrica.sincronizado_at and metrica.registrado_por is None
    assert result["creadas"] == 1 and result["errores"] == 0
    fields = [call["params"] for call in fake_meta.calls[1:]]
    assert all(item["period"] == "day" and item["metric_type"] == "total_value" for item in fields)
    audit = AuditLog.objects.get(action="METRICS_SYNCED")
    assert audit.actor is None and audit.actor_username == "sistema"


def test_facebook_usa_el_token_de_pagina_y_sus_metricas(cliente, fb_account, fake_meta):
    fake_meta.responses = [
        {"access_token": "PAGE-TOKEN"},
        {"followers_count": 800},
        insight("page_media_view", 9000),
        insight("page_total_media_view_unique", 7000),
        insight("page_post_engagements", 300),
    ]

    services.sync_month(cliente=cliente, mes=date(2026, 2, 1))

    metrica = MetricaMensual.objects.get(cliente=cliente, red="FACEBOOK", mes=date(2026, 2, 1))
    assert (metrica.seguidores, metrica.visualizaciones, metrica.alcance, metrica.interacciones) == (
        800,
        9000,
        7000,
        300,
    )
    token_calls = [call for call in fake_meta.calls if call.get("token") == "PAGE-TOKEN"]
    assert len(token_calls) == 4
    assert fake_meta.calls[2]["params"]["metric"] == "page_media_view"


def test_metrica_rechazada_deja_el_campo_vacio_sin_abortar(cliente, ig_account, fake_meta):
    fake_meta.responses = [
        {"followers_count": 10},
        GraphError("metrica no disponible", code=100, retryable=False),
        insight("reach", 7),
        insight("total_interactions", 3),
    ]

    result = services.sync_month(cliente=cliente, mes=date(2026, 2, 1))

    metrica = MetricaMensual.objects.get(cliente=cliente, red="INSTAGRAM", mes=date(2026, 2, 1))
    assert metrica.visualizaciones is None
    assert (metrica.seguidores, metrica.alcance, metrica.interacciones) == (10, 7, 3)
    assert result["errores"] == 1 and result["creadas"] == 1


def test_no_sobrescribe_manual_sin_forzar_y_si_con_forzar(cliente, ig_account, fake_meta):
    metrica, _ = services.upsert_metric(
        cliente,
        red="INSTAGRAM",
        mes=date(2026, 2, 1),
        acting_user=None,
        alcance=1,
        visualizaciones=2,
        interacciones=3,
        seguidores=4,
    )
    result = services.sync_month(cliente=cliente, mes=date(2026, 2, 1))
    metrica.refresh_from_db()
    assert result["omitidas_manual"] == 1 and fake_meta.calls == []
    assert (metrica.alcance, metrica.visualizaciones) == (1, 2)
    assert metrica.origen == "MANUAL"

    fake_meta.responses = [
        {"followers_count": 40},
        insight("views", 20),
        insight("reach", 10),
        insight("total_interactions", 5),
    ]
    services.sync_month(cliente=cliente, mes=date(2026, 2, 1), forzar=True)
    metrica.refresh_from_db()
    assert metrica.origen == "API"
    # Los seguidores de un mes pasado ya informados no se sobrescriben ni con --forzar.
    assert (metrica.seguidores, metrica.visualizaciones, metrica.alcance, metrica.interacciones) == (
        4,
        20,
        10,
        5,
    )
    assert AuditLog.objects.filter(action="METRICS_SYNCED").count() == 1


def test_seguidores_de_un_mes_pasado_no_se_sobrescriben(cliente, ig_account, fake_meta):
    MetricaMensual.objects.create(
        cliente=cliente,
        red="INSTAGRAM",
        mes=date(2026, 2, 1),
        seguidores=999,
        origen="API",
    )
    fake_meta.responses = [
        {"followers_count": 10},
        insight("views", 20),
        insight("reach", 30),
        insight("total_interactions", 40),
    ]

    services.sync_month(cliente=cliente, mes=date(2026, 2, 1))

    metrica = MetricaMensual.objects.get(cliente=cliente, red="INSTAGRAM", mes=date(2026, 2, 1))
    assert metrica.seguidores == 999
    assert metrica.visualizaciones == 20


def test_ventanas_de_un_mes_de_31_dias_suman_las_metricas(cliente, ig_account, fake_meta):
    fake_meta.responses = [
        {"followers_count": 1},
        insight("views", 10),
        {"data": []},
        insight("reach", 3),
        insight("reach", 4),
        insight("total_interactions", 5),
        insight("total_interactions", 6),
    ]

    services.sync_month(cliente=cliente, mes=date(2026, 1, 1))

    metrica = MetricaMensual.objects.get(cliente=cliente, red="INSTAGRAM", mes=date(2026, 1, 1))
    assert metrica.visualizaciones == 10 and metrica.alcance == 7 and metrica.interacciones == 11
    assert len(fake_meta.calls) == 7


@pytest.mark.parametrize(
    ("mode", "token", "reason"),
    [
        ("manual", "TOKEN", "META_PUBLISHING=manual"),
        ("fake", "", "sin META_SYSTEM_USER_TOKEN"),
    ],
)
def test_modo_manual_o_sin_token_no_llama_a_meta(
    cliente, ig_account, fake_meta, settings, mode, token, reason
):
    settings.META_PUBLISHING = mode
    settings.META_SYSTEM_USER_TOKEN = token
    result = services.sync_month(cliente=cliente, mes=date(2026, 2, 1))
    assert result["omitida"] == reason and fake_meta.calls == []


def test_tarea_periodica_y_meses_recientes(monkeypatch):
    every, handler = jobs._PERIODIC["metrics.sincronizar_meta"]
    assert every == 24 * 3600 and handler is services.sync_recent_months
    calls = []
    monkeypatch.setattr(services, "sync_month", lambda **kwargs: calls.append(kwargs["mes"]) or {})
    services.sync_recent_months()
    current = timezone.localdate().replace(day=1)
    previous = (current - timedelta(days=1)).replace(day=1)
    assert calls == [previous, current]


def test_comando_meta_sync_metricas(cliente, ig_account, fake_meta):
    fake_meta.responses = [
        {"followers_count": 1},
        insight("views", 2),
        insight("reach", 3),
        insight("total_interactions", 4),
    ]
    output = StringIO()

    call_command(
        "meta_sync_metricas",
        "--mes",
        "2026-02",
        "--cliente",
        str(cliente.pk),
        stdout=output,
    )

    assert "Mes: 2026-02" in output.getvalue()
    assert "Creadas: 1" in output.getvalue()
    assert MetricaMensual.objects.get().visualizaciones == 2


def test_endpoint_sincronizar_es_solo_admin_y_devuelve_filas(
    admin_client, staff_client, api_client, cliente, ig_account, fake_meta
):
    url = f"/api/v1/clientes/{cliente.pk}/metricas/sincronizar"
    fake_meta.responses = [
        {"followers_count": 1},
        insight("views", 2),
        insight("reach", 3),
        insight("total_interactions", 4),
    ]

    assert api_client.post(url, {"mes": "2026-02"}, format="json").status_code == 401
    assert staff_client.post(url, {"mes": "2026-02"}, format="json").status_code == 403
    response = admin_client.post(url, {"mes": "2026-02"}, format="json")
    assert response.status_code == 200
    assert response.json()[0]["visualizaciones"] == 2
    assert response.json()[0]["origen"] == "API"
    audit = AuditLog.objects.get(action="METRICS_SYNCED")
    assert audit.actor_username == "director"
