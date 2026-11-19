import pytest

from modules.audit.models import AuditLog
from modules.content.tests.helpers import make_client
from modules.metrics.models import MetricaMensual

pytestmark = pytest.mark.django_db


@pytest.fixture
def cliente():
    return make_client()


def put(client, cliente, **body):
    payload = {"red": "INSTAGRAM", "mes": "2026-03", "alcance": 12000, **body}
    return client.put(f"/api/v1/clientes/{cliente.pk}/metricas", payload, format="json")


def test_registra_las_cuatro_metricas_del_mes(staff_client, cliente):
    response = put(staff_client, cliente, visualizaciones=30000, interacciones=900, seguidores=4500)
    assert response.status_code == 201
    body = response.json()
    assert (
        body["mes"] == "2026-03" and body["red"] == "INSTAGRAM" and body["cliente_nombre"] == "Cafe del Valle"
    )
    assert (body["alcance"], body["visualizaciones"], body["interacciones"], body["seguidores"]) == (
        12000,
        30000,
        900,
        4500,
    )
    assert body["registrado_por_username"] == "copy1"
    assert MetricaMensual.objects.get().mes.isoformat() == "2026-03-01"
    assert AuditLog.objects.get(action="METRICS_CHANGED").metadata["mes"] == "2026-03"


def test_volver_a_registrar_el_mismo_mes_corrige_el_valor(staff_client, admin_client, cliente):
    put(staff_client, cliente, alcance=100)
    response = put(admin_client, cliente, alcance=250)
    assert response.status_code == 200 and response.json()["alcance"] == 250
    assert MetricaMensual.objects.count() == 1
    assert response.json()["registrado_por_username"] == "director"


def test_los_valores_no_informados_quedan_vacios(staff_client, cliente):
    body = put(staff_client, cliente).json()
    assert body["seguidores"] is None and body["visualizaciones"] is None


@pytest.mark.parametrize(
    "extra",
    [{"alcance": -1}, {"mes": "2026-13"}, {"mes": "marzo"}, {"red": "TIKTOK"}, {"mes": "2999-01"}],
)
def test_validaciones(staff_client, cliente, extra):
    assert put(staff_client, cliente, **extra).status_code == 400


def test_consulta_por_cliente_red_y_periodo(staff_client, cliente):
    otro = make_client("Otro")
    for mes in ("2026-01", "2026-02", "2026-03"):
        put(staff_client, cliente, mes=mes)
    put(staff_client, cliente, mes="2026-02", red="FACEBOOK")
    put(staff_client, otro, mes="2026-02")

    def months(**params):
        body = staff_client.get("/api/v1/metricas", params).json()
        return sorted((r["mes"], r["red"]) for r in body["results"])

    assert len(months()) == 5
    assert months(cliente=str(cliente.pk), red="FACEBOOK") == [("2026-02", "FACEBOOK")]
    assert months(cliente=str(cliente.pk), desde="2026-02", hasta="2026-02") == [
        ("2026-02", "FACEBOOK"),
        ("2026-02", "INSTAGRAM"),
    ]
    assert months(cliente=str(cliente.pk), red="INSTAGRAM", desde="2026-02") == [
        ("2026-02", "INSTAGRAM"),
        ("2026-03", "INSTAGRAM"),
    ]
    assert staff_client.get("/api/v1/metricas", {"desde": "2026/02"}).status_code == 400


def test_la_lista_viene_del_mes_mas_reciente_al_mas_antiguo(staff_client, cliente):
    for mes in ("2026-01", "2026-03", "2026-02"):
        put(staff_client, cliente, mes=mes)
    meses = [r["mes"] for r in staff_client.get("/api/v1/metricas").json()["results"]]
    assert meses == ["2026-03", "2026-02", "2026-01"]


def test_exige_sesion_y_cliente_existente(api_client, staff_client, cliente):
    assert api_client.get("/api/v1/metricas").status_code == 401
    assert api_client.put(f"/api/v1/clientes/{cliente.pk}/metricas", {}, format="json").status_code == 401
    missing = "00000000-0000-0000-0000-000000000000"
    assert (
        staff_client.put(
            f"/api/v1/clientes/{missing}/metricas", {"red": "INSTAGRAM", "mes": "2026-01"}, format="json"
        ).status_code
        == 404
    )
