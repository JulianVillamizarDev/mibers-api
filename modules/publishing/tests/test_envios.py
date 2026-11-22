"""HU-19 / RF18: consulta del registro de envios al cliente y de su estado de entrega."""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import time_machine
from django.db import connection
from django.test.utils import CaptureQueriesContext

from modules.content.models import Pieza
from modules.content.tests.helpers import make_client
from modules.core import jobs
from modules.notifications import services as notifications
from modules.notifications.gateway import FakeGateway
from modules.notifications.models import EnvioWhatsApp
from modules.publishing.serializers import mask_number

pytestmark = pytest.mark.django_db

ENVIOS = "/api/v1/envios"
BOGOTA = ZoneInfo("America/Bogota")
P = EnvioWhatsApp.Proposito


@pytest.fixture(autouse=True)
def fake_whatsapp(settings):
    settings.WHATSAPP_BACKEND = "fake"
    FakeGateway.reset()
    yield
    FakeGateway.reset()


@pytest.fixture
def cliente():
    return make_client()


def make_piece(cliente, titulo="Promo", tipo="POST") -> Pieza:
    return Pieza.objects.create(cliente=cliente, tipo=tipo, titulo=titulo)


def make_envio(
    pieza,
    *,
    numero="+573001112233",
    nombre="Laura",
    proposito=P.CLIENTE,
    archivo="foto.jpg",
    kind="image",
    contacto_id=None,
    archivo_id=None,
):
    """Replica lo que hace `publishing.services.share_with_client` para un contacto y un archivo."""
    return notifications.queue_message(
        proposito=proposito,
        destino=numero,
        destino_nombre=nombre,
        plantilla="pieza_cliente_imagen",
        parametros=["Hola"],
        media_nombre=archivo,
        media_kind=kind,
        entity_type="content.pieza",
        entity_id=pieza.pk,
        contexto={"contacto": str(contacto_id or uuid.uuid4()), "archivo": str(archivo_id or uuid.uuid4())},
    )


def at(year, month, day, hour=9, minute=0, second=0):
    """Instante en hora de Colombia, para fijar el reloj con time-machine."""
    return time_machine.travel(datetime(year, month, day, hour, minute, second, tzinfo=BOGOTA), tick=False)


def ids(response):
    return [row["id"] for row in response.json()["results"]]


# --- 1. permisos ------------------------------------------------------------------------------------------
def test_staff_y_admin_consultan_los_envios(staff_client, admin_client, cliente):
    make_envio(make_piece(cliente))
    assert staff_client.get(ENVIOS).status_code == 200
    assert admin_client.get(ENVIOS).status_code == 200


def test_exige_sesion(api_client, cliente):
    pieza = make_piece(cliente)
    assert api_client.get(ENVIOS).status_code == 401
    assert api_client.get(f"/api/v1/piezas/{pieza.pk}/envios").status_code == 401


def test_con_contrasena_temporal_no_se_puede_consultar(api_client, admin_client, cliente):
    """El resto de la API queda bloqueado mientras no se cambie la contrasena temporal."""
    created = admin_client.post("/api/v1/usuarios", {"username": "nueva"}, format="json").json()
    tokens = api_client.post(
        "/api/v1/auth/login", {"username": "nueva", "password": created["temporary_password"]}, format="json"
    ).json()
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert api_client.get(ENVIOS).status_code == 403


# --- 2. solo envios al cliente ----------------------------------------------------------------------------------
def test_solo_aparecen_los_envios_con_proposito_cliente(staff_client, cliente):
    pieza = make_piece(cliente)
    del_cliente = make_envio(pieza)
    make_envio(pieza, proposito=P.ALERTA_ADMIN)
    make_envio(pieza, proposito=P.RECORDATORIO)
    assert ids(staff_client.get(ENVIOS)) == [str(del_cliente.pk)]
    assert len(staff_client.get(f"/api/v1/piezas/{pieza.pk}/envios").json()) == 1


def test_no_aparecen_envios_de_otras_entidades(staff_client, cliente):
    otro = notifications.queue_message(
        proposito=P.CLIENTE, destino="+573001112233", plantilla="t", entity_type="otra.cosa", entity_id="1"
    )
    make_envio(make_piece(cliente))
    assert str(otro.pk) not in ids(staff_client.get(ENVIOS))


# --- 3. orden, paginacion y filtros ------------------------------------------------------------------------------
def test_lista_paginada_del_mas_reciente_al_mas_antiguo(staff_client, cliente):
    pieza = make_piece(cliente)
    created = []
    for day in (1, 3, 2):
        with at(2026, 3, day):
            created.append(make_envio(pieza))
    body = staff_client.get(ENVIOS, {"page_size": 2}).json()
    assert body["count"] == 3 and len(body["results"]) == 2 and body["next"]
    assert [r["id"] for r in body["results"]] == [
        str(created[1].pk),
        str(created[2].pk),
    ]  # 3 de marzo, 2 de marzo


def test_filtra_por_cliente_y_por_pieza(staff_client, cliente):
    otro = make_client("Otro Cliente")
    p1, p2, p3 = make_piece(cliente, "P1"), make_piece(cliente, "P2"), make_piece(otro, "P3")
    e1, e2, e3 = make_envio(p1), make_envio(p2), make_envio(p3)
    assert sorted(ids(staff_client.get(ENVIOS, {"cliente": str(cliente.pk)}))) == sorted(
        [str(e1.pk), str(e2.pk)]
    )
    assert ids(staff_client.get(ENVIOS, {"cliente": str(otro.pk)})) == [str(e3.pk)]
    assert ids(staff_client.get(ENVIOS, {"pieza": str(p2.pk)})) == [str(e2.pk)]
    assert ids(staff_client.get(ENVIOS, {"cliente": str(cliente.pk), "pieza": str(p3.pk)})) == []


def test_filtra_por_estado(staff_client, cliente):
    pieza = make_piece(cliente)
    sent, failed = make_envio(pieza), make_envio(pieza)
    EnvioWhatsApp.objects.filter(pk=sent.pk).update(estado="ENTREGADO")
    EnvioWhatsApp.objects.filter(pk=failed.pk).update(estado="FALLIDO", error="numero sin WhatsApp")
    assert ids(staff_client.get(ENVIOS, {"estado": "ENTREGADO"})) == [str(sent.pk)]
    fallidos = staff_client.get(ENVIOS, {"estado": "FALLIDO"}).json()["results"]
    assert [r["id"] for r in fallidos] == [str(failed.pk)] and fallidos[0]["error"] == "numero sin WhatsApp"
    for estado in EnvioWhatsApp.Estado.values:  # los 5 valores son validos
        assert staff_client.get(ENVIOS, {"estado": estado}).status_code == 200


def test_desde_y_hasta_usan_el_dia_calendario_de_bogota(staff_client, cliente):
    pieza = make_piece(cliente)
    with at(2026, 3, 9, 0, 0, 0):
        inicio_del_9 = make_envio(pieza)  # 00:00 del 9 en Colombia = 05:00 UTC
    with at(2026, 3, 9, 23, 59, 59):
        fin_del_9 = make_envio(pieza)  # 23:59:59 del 9 = 04:59:59 UTC del dia 10
    with at(2026, 3, 10, 0, 0, 0):
        inicio_del_10 = make_envio(pieza)
    with at(2026, 3, 8, 23, 59, 59):
        fin_del_8 = make_envio(pieza)

    def day(**params):
        return set(ids(staff_client.get(ENVIOS, params)))

    assert day(desde="2026-03-09", hasta="2026-03-09") == {
        str(inicio_del_9.pk),
        str(fin_del_9.pk),
    }  # inclusivo
    assert day(desde="2026-03-10", hasta="2026-03-10") == {str(inicio_del_10.pk)}
    assert day(desde="2026-03-09") == {str(inicio_del_9.pk), str(fin_del_9.pk), str(inicio_del_10.pk)}
    assert day(hasta="2026-03-08") == {str(fin_del_8.pk)}
    assert day(desde="2026-03-11") == set()


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"estado": "ENVIANDO"}, "invalid_state"),
        ({"estado": "entregado"}, "invalid_state"),  # distingue mayusculas
        ({"desde": "10-03-2026"}, "invalid_date"),
        ({"hasta": "ayer"}, "invalid_date"),
        ({"desde": "2026-02-30"}, "invalid_date"),
        ({"cliente": "no-es-uuid"}, "invalid_id"),
        ({"pieza": "123"}, "invalid_id"),
    ],
)
def test_filtros_invalidos_devuelven_400(staff_client, cliente, params, code):
    make_envio(make_piece(cliente))
    response = staff_client.get(ENVIOS, params)
    assert response.status_code == 400 and response.json()["code"] == code


# --- 4. numero enmascarado (RNF02) ----------------------------------------------------------------------------------
def test_el_numero_sale_enmascarado_y_nunca_completo(staff_client, cliente):
    pieza = make_piece(cliente)
    make_envio(pieza, numero="+573001112233")
    for response in (staff_client.get(ENVIOS), staff_client.get(f"/api/v1/piezas/{pieza.pk}/envios")):
        text = response.content.decode()
        row = response.json()["results"][0] if "results" in response.json() else response.json()[0]
        assert row["numero"] == "•••• 2233"
        assert "3001112233" not in text and "+57300111" not in text and "300111" not in text


@pytest.mark.parametrize(
    ("numero", "esperado"),
    [
        ("+573001112233", "•••• 2233"),
        ("+1 (415) 555-0199", "•••• 0199"),
        ("5551234", "•••• 1234"),
        ("123", "••••"),  # demasiado corto: no se muestra nada
        ("", "••••"),
        (None, "••••"),
    ],
)
def test_mask_number(numero, esperado):
    assert mask_number(numero) == esperado


# --- 5. envios de una pieza --------------------------------------------------------------------------------------------
def test_envios_de_una_pieza_en_orden_cronologico(staff_client, cliente):
    pieza = make_piece(cliente)
    otra = make_piece(cliente, "Otra")
    created = {}
    for name, day in (("tercero", 3), ("primero", 1), ("segundo", 2)):
        with at(2026, 3, day):
            created[name] = make_envio(pieza)
    make_envio(otra)
    body = staff_client.get(f"/api/v1/piezas/{pieza.pk}/envios").json()
    assert isinstance(body, list)  # sin paginar
    assert [e["id"] for e in body] == [str(created[n].pk) for n in ("primero", "segundo", "tercero")]


def test_pieza_sin_envios_devuelve_lista_vacia_y_pieza_inexistente_404(staff_client, cliente):
    pieza = make_piece(cliente)
    assert staff_client.get(f"/api/v1/piezas/{pieza.pk}/envios").json() == []
    assert staff_client.get(f"/api/v1/piezas/{uuid.uuid4()}/envios").status_code == 404


# --- 6. el estado refleja el webhook ------------------------------------------------------------------------------------
def test_el_estado_refleja_el_envio_y_la_entrega(staff_client, cliente):
    pieza = make_piece(cliente)
    envio = make_envio(pieza)
    row = staff_client.get(ENVIOS).json()["results"][0]
    assert row["estado"] == "PENDIENTE" and row["enviado_at"] is None and row["entregado_at"] is None

    jobs.process_once()  # el worker lo envia por el gateway simulado
    envio.refresh_from_db()
    row = staff_client.get(ENVIOS).json()["results"][0]
    assert row["estado"] == "ENVIADO" and row["enviado_at"] and row["entregado_at"] is None

    assert notifications.apply_status_update(envio.wa_message_id, "delivered")  # webhook de Meta
    row = staff_client.get(f"/api/v1/piezas/{pieza.pk}/envios").json()[0]
    assert row["estado"] == "ENTREGADO" and row["entregado_at"]

    notifications.apply_status_update(envio.wa_message_id, "read")
    assert staff_client.get(ENVIOS, {"estado": "LEIDO"}).json()["count"] == 1


def test_un_envio_fallido_muestra_el_motivo(staff_client, cliente):
    pieza = make_piece(cliente)
    envio = make_envio(pieza)
    from modules.notifications.gateway import WhatsAppError

    FakeGateway.fail_with = WhatsAppError("numero sin WhatsApp", retryable=False)
    jobs.process_once()
    row = staff_client.get(f"/api/v1/piezas/{pieza.pk}/envios").json()[0]
    assert row["id"] == str(envio.pk) and row["estado"] == "FALLIDO" and "sin WhatsApp" in row["error"]


# --- forma de cada envio --------------------------------------------------------------------------------------------------
def test_forma_completa_de_un_envio(staff_client, cliente):
    pieza = make_piece(cliente, "Promo de junio", tipo="REEL")
    contacto, archivo = uuid.uuid4(), uuid.uuid4()
    envio = make_envio(
        pieza,
        nombre="Laura Gomez",
        archivo="reel.mp4",
        kind="video",
        contacto_id=contacto,
        archivo_id=archivo,
    )
    row = staff_client.get(ENVIOS).json()["results"][0]
    assert set(row) == {
        "id", "pieza", "cliente", "contacto", "numero", "archivo",
        "estado", "error", "enviado_at", "entregado_at", "created_at",
    }  # fmt: skip
    assert row["id"] == str(envio.pk)
    assert row["pieza"] == {"id": str(pieza.pk), "titulo": "Promo de junio", "tipo": "REEL"}
    assert row["cliente"] == {"id": str(cliente.pk), "nombre": "Cafe del Valle"}
    assert row["contacto"] == {"id": str(contacto), "nombre": "Laura Gomez"}
    assert row["archivo"] == {"id": str(archivo), "nombre": "reel.mp4", "kind": "video"}
    assert row["estado"] == "PENDIENTE" and row["error"] == ""


def test_las_fechas_salen_en_hora_de_colombia(staff_client, cliente):
    with at(2026, 3, 9, 21, 30):
        make_envio(make_piece(cliente))
    assert staff_client.get(ENVIOS).json()["results"][0]["created_at"] == "2026-03-09T21:30:00-05:00"


def test_contacto_y_archivo_sin_ids_en_el_contexto_salen_null(staff_client, cliente):
    pieza = make_piece(cliente)
    notifications.queue_message(
        proposito=P.CLIENTE,
        destino="+573001112233",
        destino_nombre="Laura",
        plantilla="t",
        entity_type="content.pieza",
        entity_id=pieza.pk,
    )
    row = staff_client.get(ENVIOS).json()["results"][0]
    assert row["contacto"]["id"] is None and row["archivo"]["id"] is None


def test_si_la_pieza_ya_no_existe_el_envio_se_conserva_con_pieza_y_cliente_null(staff_client, cliente):
    """Tras la purga por retencion del cliente el registro del envio sigue siendo consultable."""
    pieza = make_piece(cliente)
    make_envio(pieza)
    pieza.delete()
    row = staff_client.get(ENVIOS).json()["results"][0]
    assert row["pieza"] is None and row["cliente"] is None and row["numero"] == "•••• 2233"


# --- 7. sin N+1 ----------------------------------------------------------------------------------------------------------------
def count_queries(client, url, **params):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url, params)
    assert response.status_code == 200
    return len(queries), response


def test_numero_de_consultas_acotado_para_una_pagina_de_25(staff_client, cliente):
    make_envio(make_piece(cliente))
    base, _ = count_queries(staff_client, ENVIOS)

    for i in range(24):
        make_envio(make_piece(make_client(f"Cliente {i}"), titulo=f"P{i}"))
    with CaptureQueriesContext(connection) as queries:
        response = staff_client.get(ENVIOS)
    assert response.status_code == 200 and len(response.json()["results"]) == 25
    assert len(queries) == base  # 25 envios de 25 piezas y clientes distintos: mismas consultas que con 1
    assert len(queries) <= 8


def test_numero_de_consultas_de_envios_de_una_pieza_no_crece(staff_client, cliente):
    pieza = make_piece(cliente)
    make_envio(pieza)
    base, _ = count_queries(staff_client, f"/api/v1/piezas/{pieza.pk}/envios")
    for _ in range(20):
        make_envio(pieza)
    total, response = count_queries(staff_client, f"/api/v1/piezas/{pieza.pk}/envios")
    assert len(response.json()) == 21 and total == base and total <= 7
