"""RF4: las acciones de sesion y de gestion de cuentas quedan en el historial."""

import pytest

from modules.audit.models import AuditLog
from modules.conftest import PASSWORD

pytestmark = pytest.mark.django_db


def actions():
    return list(AuditLog.objects.order_by("created_at").values_list("action", flat=True))


def login(client, password=PASSWORD, username="copy1"):
    return client.post("/api/v1/auth/login", {"username": username, "password": password}, format="json")


def test_login_exitoso_y_logout_quedan_registrados(api_client, staff_user):
    tokens = login(api_client).json()
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    api_client.post("/api/v1/auth/logout", {}, format="json")
    assert actions() == ["LOGIN", "LOGOUT"]
    entry = AuditLog.objects.get(action="LOGIN")
    assert entry.actor == staff_user and entry.actor_username == "copy1"


def test_login_fallido_se_registra_aunque_el_usuario_no_exista(api_client):
    login(api_client, username="fantasma")
    entry = AuditLog.objects.get()
    assert entry.action == "LOGIN_FAILED"
    assert entry.actor is None and entry.actor_username == "fantasma"
    assert entry.metadata == {"reason": "unknown_user"}


def test_el_bloqueo_de_cuenta_queda_registrado(api_client, staff_user):
    for _ in range(5):
        login(api_client, password="mala")
    assert actions().count("LOGIN_FAILED") == 5
    assert actions().count("ACCOUNT_LOCKED") == 1


def test_no_se_guarda_la_contrasena_en_el_historial(api_client, staff_user):
    login(api_client, password="secreto-que-no-debe-aparecer")
    assert "secreto-que-no-debe-aparecer" not in str(AuditLog.objects.values())


def test_gestion_de_cuentas_registra_al_administrador(admin_client, admin_user, staff_user):
    created = admin_client.post("/api/v1/usuarios", {"username": "nuevo"}, format="json").json()
    admin_client.patch(f"/api/v1/usuarios/{created['id']}", {"cargo": "Editor"}, format="json")
    admin_client.post(f"/api/v1/usuarios/{created['id']}/restablecer-password")
    admin_client.post(f"/api/v1/usuarios/{created['id']}/desactivar")
    admin_client.post(f"/api/v1/usuarios/{created['id']}/activar")
    assert actions() == [
        "USER_CREATED",
        "USER_UPDATED",
        "PASSWORD_RESET",
        "USER_DEACTIVATED",
        "USER_ACTIVATED",
    ]
    assert set(AuditLog.objects.values_list("actor_username", flat=True)) == {"director"}
    assert AuditLog.objects.get(action="USER_UPDATED").metadata == {"fields": ["cargo"]}


def test_cambio_de_contrasena_queda_registrado(staff_client):
    staff_client.post(
        "/api/v1/auth/change-password",
        {"current_password": PASSWORD, "new_password": "Nueva-Clave-Larga-1"},
        format="json",
    )
    assert actions() == ["PASSWORD_CHANGED"]
