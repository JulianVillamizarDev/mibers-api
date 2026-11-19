from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from modules.content.models import Pieza, PiezaRed

from .helpers import make_client

pytestmark = pytest.mark.django_db

BOGOTA = ZoneInfo("America/Bogota")


def piece(cliente, when, *, estado="BORRADOR", titulo="P", redes=("INSTAGRAM",), tipo="POST"):
    obj = Pieza.objects.create(cliente=cliente, tipo=tipo, titulo=titulo, estado=estado, programada_para=when)
    for red in redes:
        PiezaRed.objects.create(pieza=obj, red=red)
    return obj


def local(y, m, d, h=9, minute=0):
    return datetime(y, m, d, h, minute, tzinfo=BOGOTA)


@pytest.fixture
def cliente():
    return make_client()


# --- RF20: cronograma semanal y mensual -------------------------------------------------------
def test_cronograma_semanal_agrupa_por_dia_de_colombia(staff_client, cliente):
    piece(cliente, local(2026, 8, 10), titulo="lunes")
    # 23:30 en Colombia = 04:30 UTC del dia siguiente: debe quedar el miercoles, no el jueves
    piece(cliente, local(2026, 8, 12, 23, 30), titulo="miercoles noche")
    piece(cliente, local(2026, 8, 16, 8), titulo="domingo")
    piece(cliente, local(2026, 8, 17, 8), titulo="lunes siguiente")
    piece(cliente, local(2026, 8, 9, 20), titulo="domingo anterior")

    body = staff_client.get(
        f"/api/v1/clientes/{cliente.pk}/cronograma", {"vista": "semana", "fecha": "2026-08-13"}
    ).json()
    assert body["desde"] == "2026-08-10" and body["hasta"] == "2026-08-16"
    assert [d["fecha"] for d in body["dias"]] == [f"2026-08-{n}" for n in range(10, 17)]
    by_day = {d["fecha"]: [p["titulo"] for p in d["piezas"]] for d in body["dias"]}
    assert by_day["2026-08-10"] == ["lunes"]
    assert by_day["2026-08-12"] == ["miercoles noche"]
    assert by_day["2026-08-13"] == []
    assert by_day["2026-08-16"] == ["domingo"]


def test_cronograma_mensual(staff_client, cliente):
    piece(cliente, local(2026, 2, 1), titulo="inicio")
    piece(cliente, local(2026, 2, 28, 22), titulo="fin")
    piece(cliente, local(2026, 3, 1), titulo="otro mes")
    body = staff_client.get(
        f"/api/v1/clientes/{cliente.pk}/cronograma", {"vista": "mes", "fecha": "2026-02-15"}
    ).json()
    assert body["desde"] == "2026-02-01" and body["hasta"] == "2026-02-28" and len(body["dias"]) == 28
    titles = [p["titulo"] for d in body["dias"] for p in d["piezas"]]
    assert titles == ["inicio", "fin"]


def test_cronograma_con_estado_de_cada_pieza_y_filtros(staff_client, cliente):
    piece(cliente, local(2026, 8, 10), titulo="ig aprobada", estado="APROBADA")
    piece(cliente, local(2026, 8, 11), titulo="fb borrador", redes=("FACEBOOK",))
    piece(cliente, local(2026, 8, 12), titulo="ambas", estado="APROBADA", redes=("INSTAGRAM", "FACEBOOK"))
    url = f"/api/v1/clientes/{cliente.pk}/cronograma"
    base = {"vista": "semana", "fecha": "2026-08-10"}

    def titles(**extra):
        body = staff_client.get(url, {**base, **extra}).json()
        return [p["titulo"] for d in body["dias"] for p in d["piezas"]]

    assert titles() == ["ig aprobada", "fb borrador", "ambas"]
    assert titles(red="FACEBOOK") == ["fb borrador", "ambas"]
    assert titles(estado="APROBADA") == ["ig aprobada", "ambas"]
    assert titles(red="FACEBOOK", estado="APROBADA") == ["ambas"]
    first = staff_client.get(url, base).json()["dias"][0]["piezas"][0]
    assert first["estado"] == "APROBADA" and first["redes"] == ["INSTAGRAM"]


def test_cronograma_no_mezcla_clientes(staff_client, cliente):
    otro = make_client("Otro")
    piece(otro, local(2026, 8, 10), titulo="ajena")
    body = staff_client.get(f"/api/v1/clientes/{cliente.pk}/cronograma", {"fecha": "2026-08-10"}).json()
    assert all(d["piezas"] == [] for d in body["dias"])


@pytest.mark.parametrize(
    "params", [{"vista": "anio"}, {"fecha": "10-08-2026"}, {"red": "TIKTOK"}, {"estado": "X"}]
)
def test_cronograma_valida_parametros(staff_client, cliente, params):
    assert staff_client.get(f"/api/v1/clientes/{cliente.pk}/cronograma", params).status_code == 400


def test_cronograma_exige_sesion(api_client, cliente):
    assert api_client.get(f"/api/v1/clientes/{cliente.pk}/cronograma").status_code == 401


# --- RF5: empresas actuales ----------------------------------------------------------------------
def test_empresas_actuales_con_proximas_publicaciones_y_pendientes(staff_client):
    activa = make_client("Activa")
    inactiva = make_client("Inactiva", activo=False)
    now = timezone.now()
    for i in range(7):  # mas de 5: solo se listan las 5 mas cercanas
        piece(activa, now + timedelta(hours=i + 1), estado="PROGRAMADA", titulo=f"prox{i}")
    piece(activa, now + timedelta(days=1), titulo="borrador")
    piece(activa, now + timedelta(days=1), estado="EN_REVISION")
    piece(activa, now + timedelta(days=1), estado="EN_REVISION")
    piece(activa, now + timedelta(days=1), estado="CAMBIOS_SOLICITADOS")
    piece(activa, now + timedelta(days=1), estado="APROBADA")
    piece(activa, now - timedelta(days=1), estado="FALLIDA")
    piece(activa, now - timedelta(days=2), estado="PUBLICADA", titulo="vieja")
    piece(inactiva, now + timedelta(days=1), titulo="de cliente inactivo")

    rows = staff_client.get("/api/v1/clientes/actuales").json()
    assert [r["cliente"]["nombre"] for r in rows] == ["Activa"]  # la desactivada no aparece
    row = rows[0]
    assert [p["titulo"] for p in row["proximas_publicaciones"]][:3] == ["prox0", "prox1", "prox2"]
    assert len(row["proximas_publicaciones"]) == 5
    assert row["pendientes"] == {
        "borradores": 1,
        "en_revision": 2,
        "cambios_solicitados": 1,
        "aprobadas_sin_programar": 1,
        "fallidas": 1,
    }
    assert row["semana_actual"]["plan"] == 3


def test_empresas_actuales_cuenta_lo_programado_en_la_semana(staff_client):
    cliente = make_client()
    now = timezone.now()
    piece(cliente, now, estado="PROGRAMADA")
    piece(cliente, now, estado="PUBLICADA")
    piece(cliente, now, estado="BORRADOR")  # no cuenta como programada
    piece(cliente, now + timedelta(days=14), estado="PROGRAMADA")  # otra semana
    row = staff_client.get("/api/v1/clientes/actuales").json()[0]
    assert row["semana_actual"] == {"plan": 3, "programadas": 2}


def test_empresas_actuales_sin_piezas(staff_client):
    make_client()
    row = staff_client.get("/api/v1/clientes/actuales").json()[0]
    assert row["proximas_publicaciones"] == [] and row["pendientes"]["borradores"] == 0
