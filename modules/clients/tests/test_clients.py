from datetime import date, timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone

from modules.audit.models import AuditLog
from modules.clients import services
from modules.clients.models import Cliente, CuentaSocial

pytestmark = pytest.mark.django_db

CLIENTS = "/api/v1/clientes"


def payload(**extra):
    return {
        "nombre": "Cafe del Valle",
        "contacto_nombre": "Laura Gomez",
        "contacto_telefono": "+573001112233",
        "contacto_email": "laura@cafedelvalle.co",
        "posts_por_semana": 5,
        "contrato_inicio": "2026-01-15",
        "contrato_fin": "2026-12-31",
        "estrategia": "POSICIONAR",
        "objetivo": "Ser la cafeteria de referencia en Cali",
        **extra,
    }


@pytest.fixture
def cliente(staff_client):
    return staff_client.post(CLIENTS, payload(), format="json").json()


def actions():
    return list(AuditLog.objects.order_by("created_at").values_list("action", flat=True))


# --- RF5: registrar cliente -----------------------------------------------------
def test_personal_registra_cliente_con_plan_contrato_y_estrategia(staff_client):
    response = staff_client.post(CLIENTS, payload(), format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["posts_por_semana"] == 5 and body["estrategia"] == "POSICIONAR"
    assert body["contrato_inicio"] == "2026-01-15" and body["activo"] is True
    assert actions() == ["CLIENT_CREATED"]


def test_datos_de_contacto_se_guardan_cifrados(cliente):
    with connection.cursor() as cursor:
        cursor.execute("SELECT contacto_nombre, contacto_telefono, contacto_email FROM clients_cliente")
        raw = cursor.fetchone()
    assert all(value.startswith("enc1:") for value in raw)
    assert "Laura" not in raw[0] and "3001112233" not in raw[1] and "cafedelvalle" not in raw[2]
    assert Cliente.objects.get().contacto_nombre == "Laura Gomez"


@pytest.mark.parametrize(
    "extra",
    [
        {"estrategia": "OTRA"},
        {"posts_por_semana": 0},
        {"contrato_fin": "2025-12-31"},
        {"contacto_email": "no-es-correo"},
    ],
)
def test_validaciones_de_cliente(staff_client, extra):
    assert staff_client.post(CLIENTS, payload(**extra), format="json").status_code == 400


def test_sin_sesion_no_hay_acceso(api_client):
    assert api_client.get(CLIENTS).status_code == 401


def test_edita_cliente_y_audita_los_campos(staff_client, cliente):
    response = staff_client.patch(f"{CLIENTS}/{cliente['id']}", {"posts_por_semana": 3}, format="json")
    assert response.status_code == 200 and response.json()["posts_por_semana"] == 3
    assert AuditLog.objects.get(action="CLIENT_UPDATED").metadata == {"fields": ["posts_por_semana"]}


def test_lista_filtra_por_activo_y_busca_por_nombre(staff_client, cliente):
    staff_client.post(CLIENTS, payload(nombre="Zapateria Ruiz"), format="json")
    assert staff_client.get(CLIENTS, {"search": "zapat"}).json()["count"] == 1
    staff_client.post(f"{CLIENTS}/{cliente['id']}/desactivar")
    assert staff_client.get(CLIENTS, {"activo": "true"}).json()["count"] == 1
    assert staff_client.get(CLIENTS, {"activo": "false"}).json()["count"] == 1


def test_manual_de_marca_se_sube_y_se_descarga_autenticado(staff_client, api_client):
    pdf = SimpleUploadedFile("Manual Marca.pdf", b"%PDF-1.4 contenido", content_type="application/pdf")
    response = staff_client.post(CLIENTS, {**payload(), "manual_marca": pdf}, format="multipart")
    assert response.status_code == 201
    body = response.json()
    assert body["tiene_manual_marca"] is True and body["manual_marca_nombre"] == "Manual Marca.pdf"
    download = staff_client.get(f"{CLIENTS}/{body['id']}/manual-marca")
    assert download.status_code == 200
    assert b"".join(download.streaming_content) == b"%PDF-1.4 contenido"
    # sin sesion no se puede descargar
    assert api_client.get(f"{CLIENTS}/{body['id']}/manual-marca").status_code == 401


def test_manual_de_marca_rechaza_formatos_peligrosos(staff_client):
    exe = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
    response = staff_client.post(CLIENTS, {**payload(), "manual_marca": exe}, format="multipart")
    assert response.status_code == 400


def test_archivo_se_guarda_con_nombre_aleatorio(staff_client):
    pdf = SimpleUploadedFile("secreto-cliente.pdf", b"%PDF", content_type="application/pdf")
    staff_client.post(CLIENTS, {**payload(), "manual_marca": pdf}, format="multipart")
    stored = Cliente.objects.get().manual_marca.name
    assert stored.startswith("clientes/") and "secreto" not in stored and stored.endswith(".pdf")


# --- RF5: desactivar conservando historial ------------------------------------------
def test_desactivar_marca_fecha_y_reactivar_la_limpia(staff_client, cliente):
    staff_client.post(f"{CLIENTS}/{cliente['id']}/desactivar")
    obj = Cliente.objects.get()
    assert obj.activo is False and obj.desactivado_at is not None
    assert staff_client.post(f"{CLIENTS}/{cliente['id']}/desactivar").json()["code"] == "already_inactive"
    staff_client.post(f"{CLIENTS}/{cliente['id']}/reactivar")
    obj.refresh_from_db()
    assert obj.activo is True and obj.desactivado_at is None
    assert actions()[-2:] == ["CLIENT_DEACTIVATED", "CLIENT_ACTIVATED"]


def test_retencion_elimina_solo_clientes_desactivados_hace_mas_de_un_anio(staff_client, admin_user):
    viejo = services.create_client(acting_user=admin_user, **_fields("Viejo"))
    reciente = services.create_client(acting_user=admin_user, **_fields("Reciente"))
    activo = services.create_client(acting_user=admin_user, **_fields("Activo"))
    services.deactivate_client(viejo, acting_user=admin_user)
    services.deactivate_client(reciente, acting_user=admin_user)
    Cliente.objects.filter(pk=viejo.pk).update(desactivado_at=timezone.now() - timedelta(days=366))
    Cliente.objects.filter(pk=reciente.pk).update(desactivado_at=timezone.now() - timedelta(days=300))

    assert services.purge_expired_clients() == 1
    assert set(Cliente.objects.values_list("nombre", flat=True)) == {"Reciente", "Activo"}
    assert activo.activo


def _fields(nombre):
    return {
        "nombre": nombre,
        "posts_por_semana": 3,
        "contrato_inicio": date(2026, 1, 1),
        "estrategia": "VENDER",
    }


# --- RF6: contactos de WhatsApp ---------------------------------------------------------
def test_registra_uno_o_mas_contactos_con_autorizacion(staff_client, cliente):
    url = f"{CLIENTS}/{cliente['id']}/contactos"
    first = staff_client.post(
        url,
        {
            "nombre": "Laura",
            "numero": "+573001112233",
            "autorizado": True,
            "evidencia": "Correo del 2026-01-10",
        },
        format="json",
    )
    assert first.status_code == 201
    assert first.json()["autorizado"] is True and first.json()["autorizacion_at"]
    second = staff_client.post(
        url, {"nombre": "Pedro", "numero": "+573009998877", "autorizado": False}, format="json"
    )
    assert second.json()["autorizado"] is False
    assert len(staff_client.get(url).json()) == 2


def test_numero_debe_ser_e164_y_no_repetirse(staff_client, cliente):
    url = f"{CLIENTS}/{cliente['id']}/contactos"
    assert (
        staff_client.post(
            url, {"nombre": "A", "numero": "3001112233", "autorizado": True}, format="json"
        ).status_code
        == 400
    )
    ok = {"nombre": "A", "numero": "+573001112233", "autorizado": True}
    assert staff_client.post(url, ok, format="json").status_code == 201
    duplicate = staff_client.post(url, ok, format="json")
    assert duplicate.status_code == 400 and duplicate.json()["code"] == "duplicate_contact"


def test_nombre_y_numero_del_contacto_se_cifran(staff_client, cliente):
    staff_client.post(
        f"{CLIENTS}/{cliente['id']}/contactos",
        {"nombre": "Laura", "numero": "+573001112233", "autorizado": True},
        format="json",
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT nombre, numero FROM clients_contactowhatsapp")
        nombre, numero = cursor.fetchone()
    assert nombre.startswith("enc1:") and numero.startswith("enc1:")


def test_revocar_autorizacion_y_eliminar_contacto(staff_client, cliente):
    url = f"{CLIENTS}/{cliente['id']}/contactos"
    contact = staff_client.post(
        url, {"nombre": "Laura", "numero": "+573001112233", "autorizado": True}, format="json"
    ).json()
    revoked = staff_client.patch(f"{url}/{contact['id']}", {"autorizado": False}, format="json")
    assert revoked.json()["autorizado"] is False and revoked.json()["autorizacion_at"] is None
    assert staff_client.delete(f"{url}/{contact['id']}").status_code == 204
    assert staff_client.get(url).json() == []
    assert actions().count("CONTACT_CHANGED") == 3


def test_contacto_de_otro_cliente_no_es_accesible(staff_client, cliente):
    other = staff_client.post(CLIENTS, payload(nombre="Otro"), format="json").json()
    contact = staff_client.post(
        f"{CLIENTS}/{other['id']}/contactos",
        {"nombre": "X", "numero": "+573001112233", "autorizado": True},
        format="json",
    ).json()
    assert staff_client.delete(f"{CLIENTS}/{cliente['id']}/contactos/{contact['id']}").status_code == 404


# --- RF7 / RF3: cuentas sociales solo las gestiona el Administrador --------------------------
ACCOUNT = {"red": "INSTAGRAM", "handle": "@cafedelvalle", "url_perfil": "https://instagram.com/cafedelvalle"}


def test_personal_no_puede_vincular_redes(staff_client, cliente):
    response = staff_client.post(f"{CLIENTS}/{cliente['id']}/cuentas", ACCOUNT, format="json")
    assert response.status_code == 403


def test_admin_vincula_cuenta_sin_guardar_contrasenas(admin_client, staff_client, cliente):
    url = f"{CLIENTS}/{cliente['id']}/cuentas"
    created = admin_client.post(url, ACCOUNT, format="json")
    assert created.status_code == 201
    body = created.json()
    assert body["estado"] == "VINCULADA" and "password" not in str(body).lower()
    # el personal si puede consultarlas (las necesita para programar)
    assert len(staff_client.get(url).json()) == 1
    assert actions()[-1] == "NETWORK_LINKED"


def test_una_cuenta_por_red_y_cliente(admin_client, cliente):
    url = f"{CLIENTS}/{cliente['id']}/cuentas"
    admin_client.post(url, ACCOUNT, format="json")
    again = admin_client.post(url, {**ACCOUNT, "handle": "@otra"}, format="json")
    assert again.status_code == 400 and again.json()["code"] == "duplicate_account"
    other_network = admin_client.post(
        url, {**ACCOUNT, "red": "FACEBOOK", "handle": "Cafe del Valle"}, format="json"
    )
    assert other_network.status_code == 201


def test_red_fuera_del_alcance_es_rechazada(admin_client, cliente):
    response = admin_client.post(
        f"{CLIENTS}/{cliente['id']}/cuentas", {**ACCOUNT, "red": "TIKTOK"}, format="json"
    )
    assert response.status_code == 400


def test_estado_de_la_cuenta_cambia_y_se_audita(admin_client, staff_client, cliente):
    url = f"{CLIENTS}/{cliente['id']}/cuentas"
    account = admin_client.post(url, ACCOUNT, format="json").json()
    for state in ("EXPIRADA", "DESVINCULADA", "VINCULADA"):
        response = admin_client.patch(f"{url}/{account['id']}", {"estado": state}, format="json")
        assert response.json()["estado"] == state
    assert (
        staff_client.patch(f"{url}/{account['id']}", {"estado": "EXPIRADA"}, format="json").status_code == 403
    )
    changes = AuditLog.objects.filter(action="NETWORK_STATUS_CHANGED")
    assert [(c.metadata["de"], c.metadata["a"]) for c in changes.order_by("created_at")] == [
        ("VINCULADA", "EXPIRADA"),
        ("EXPIRADA", "DESVINCULADA"),
        ("DESVINCULADA", "VINCULADA"),
    ]
    assert CuentaSocial.objects.get().estado == "VINCULADA"
