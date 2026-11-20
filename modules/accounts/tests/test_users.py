import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from modules.conftest import PASSWORD, _client_for

User = get_user_model()
USERS = "/api/v1/usuarios"
CHANGE = "/api/v1/auth/change-password"

pytestmark = pytest.mark.django_db


def new_user_payload(**extra):
    return {"username": "nuevo", "first_name": "Nora", "last_name": "Nueva", "cargo": "Disenadora", **extra}


# --- RF3: permisos por rol ---------------------------------------------------
def test_personal_no_puede_gestionar_cuentas(staff_client):
    assert staff_client.get(USERS).status_code == 403
    assert staff_client.post(USERS, new_user_payload(), format="json").status_code == 403


def test_admin_lista_usuarios(admin_client, staff_user):
    response = admin_client.get(USERS)
    assert response.status_code == 200
    assert {u["username"] for u in response.json()} == {"director", "copy1"}


# --- RF2: crear con contrasena temporal -------------------------------------
def test_admin_crea_personal_con_contrasena_temporal_sin_correo(admin_client, api_client):
    response = admin_client.post(USERS, new_user_payload(), format="json")
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "STAFF" and body["must_change_password"] is True
    temporary = body["temporary_password"]
    assert User.objects.get(username="nuevo").email == ""
    # la temporal permite ingresar
    login = api_client.post("/api/v1/auth/login", {"username": "nuevo", "password": temporary}, format="json")
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True


def test_cargo_no_cambia_permisos(admin_client, staff_user):
    admin_client.patch(f"{USERS}/{staff_user.pk}", {"cargo": "Director creativo"}, format="json")
    staff_user.refresh_from_db()
    assert staff_user.role == "STAFF"


def test_no_permite_usernames_duplicados_sin_importar_mayusculas(admin_client, staff_user):
    response = admin_client.post(USERS, new_user_payload(username="COPY1"), format="json")
    assert response.status_code == 400


def test_whatsapp_se_valida_y_se_guarda_cifrado(admin_client):
    bad = admin_client.post(USERS, new_user_payload(whatsapp_number="3001112233"), format="json")
    assert bad.status_code == 400
    ok = admin_client.post(USERS, new_user_payload(whatsapp_number="+573001112233"), format="json")
    assert ok.status_code == 201
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT whatsapp_number FROM accounts_user WHERE username = 'nuevo'")
        raw = cursor.fetchone()[0]
    assert raw.startswith("enc1:") and "3001112233" not in raw
    assert User.objects.get(username="nuevo").whatsapp_number == "+573001112233"


# --- RF2: primer ingreso obliga a cambiar la temporal -------------------------
def test_con_contrasena_temporal_solo_puede_cambiarla(admin_client, api_client):
    temporary = admin_client.post(USERS, new_user_payload(), format="json").json()["temporary_password"]
    tokens = api_client.post(
        "/api/v1/auth/login", {"username": "nuevo", "password": temporary}, format="json"
    ).json()
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

    blocked = api_client.get(USERS)
    assert blocked.status_code == 403
    assert api_client.get("/api/v1/auth/me").status_code == 200  # permitido

    changed = api_client.post(
        CHANGE, {"current_password": temporary, "new_password": "Otra-Clave-Larga-987"}, format="json"
    )
    assert changed.status_code == 200
    user = User.objects.get(username="nuevo")
    assert user.must_change_password is False
    assert user.check_password("Otra-Clave-Larga-987")


def test_cambiar_contrasena_valida_la_actual(staff_client):
    response = staff_client.post(
        CHANGE, {"current_password": "incorrecta", "new_password": "Nueva-Clave-Larga-1"}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "wrong_current_password"


def test_cambiar_contrasena_aplica_validadores(staff_client):
    response = staff_client.post(
        CHANGE, {"current_password": PASSWORD, "new_password": "corta"}, format="json"
    )
    assert response.status_code == 400


def test_nueva_contrasena_debe_ser_distinta(staff_client):
    response = staff_client.post(
        CHANGE, {"current_password": PASSWORD, "new_password": PASSWORD}, format="json"
    )
    assert response.json()["code"] == "same_password"


def test_cambiar_contrasena_cierra_las_demas_sesiones(staff_client, staff_user, api_client):
    other = _client_for(staff_user)
    assert other.get("/api/v1/auth/me").status_code == 200
    staff_client.post(
        CHANGE, {"current_password": PASSWORD, "new_password": "Nueva-Clave-Larga-1"}, format="json"
    )
    assert other.get("/api/v1/auth/me").status_code == 401
    assert staff_client.get("/api/v1/auth/me").status_code == 200


# --- RF2: restablecer contrasena ---------------------------------------------
def test_admin_restablece_contrasena_y_desbloquea(admin_client, staff_user, api_client):
    for _ in range(5):
        api_client.post("/api/v1/auth/login", {"username": "copy1", "password": "mala"}, format="json")
    response = admin_client.post(f"{USERS}/{staff_user.pk}/restablecer-password")
    temporary = response.json()["temporary_password"]
    login = api_client.post("/api/v1/auth/login", {"username": "copy1", "password": temporary}, format="json")
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True


# --- RF2: desactivar sin perder historial ------------------------------------
def test_desactivar_conserva_el_registro(admin_client, staff_user):
    assert admin_client.post(f"{USERS}/{staff_user.pk}/desactivar").status_code == 200
    staff_user.refresh_from_db()
    assert staff_user.is_active is False
    assert User.objects.filter(pk=staff_user.pk).exists()
    assert admin_client.post(f"{USERS}/{staff_user.pk}/activar").json()["is_active"] is True


def test_no_se_puede_desactivar_a_si_mismo(admin_client, admin_user):
    response = admin_client.post(f"{USERS}/{admin_user.pk}/desactivar")
    assert response.status_code == 400
    assert response.json()["code"] == "cannot_deactivate_self"


def test_no_se_puede_degradar_ni_desactivar_al_ultimo_admin(admin_client, admin_user):
    response = admin_client.patch(f"{USERS}/{admin_user.pk}", {"role": "STAFF"}, format="json")
    assert response.status_code == 400
    assert response.json()["code"] == "last_admin"


def test_puede_degradar_admin_si_hay_otro(admin_client, admin_user):
    other = User.objects.create_user(username="otro", password=PASSWORD, role="ADMIN")
    response = admin_client.patch(f"{USERS}/{other.pk}", {"role": "STAFF"}, format="json")
    assert response.status_code == 200 and response.json()["role"] == "STAFF"


# --- createadmin --------------------------------------------------------------
def test_createadmin_con_contrasena():
    call_command("createadmin", username="jefe", password="Clave-Inicial-12345")
    user = User.objects.get(username="jefe")
    assert user.role == "ADMIN" and user.check_password("Clave-Inicial-12345")
    assert user.must_change_password is False


def test_createadmin_sin_contrasena_genera_temporal(capsys):
    call_command("createadmin", username="jefe2")
    assert "temporal" in capsys.readouterr().out
    assert User.objects.get(username="jefe2").must_change_password is True


def test_createadmin_rechaza_duplicados():
    call_command("createadmin", username="jefe", password="Clave-Inicial-12345")
    with pytest.raises(CommandError):
        call_command("createadmin", username="jefe")
