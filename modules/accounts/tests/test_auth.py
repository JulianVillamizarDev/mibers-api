from datetime import timedelta

import pytest
import time_machine
from django.contrib.auth import get_user_model
from django.utils import timezone

from modules.accounts.models import UserSession
from modules.conftest import PASSWORD

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
ME = "/api/v1/auth/me"

pytestmark = pytest.mark.django_db


def login(client, username="copy1", password=PASSWORD):
    return client.post(LOGIN, {"username": username, "password": password}, format="json")


def bearer(client, access):
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")


# --- RF1: login / logout ----------------------------------------------------
def test_login_correcto_devuelve_tokens_y_usuario(api_client, staff_user):
    response = login(api_client)
    assert response.status_code == 200
    body = response.json()
    assert body["access"] and body["refresh"]
    assert body["user"]["username"] == "copy1"
    assert body["user"]["role"] == "STAFF"
    assert "password" not in body["user"]


def test_login_con_contrasena_incorrecta_devuelve_401(api_client, staff_user):
    response = login(api_client, password="otra-cosa")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_con_usuario_inexistente_devuelve_el_mismo_error(api_client):
    response = login(api_client, username="fantasma")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_usuario_desactivado_no_puede_ingresar(api_client, staff_user):
    staff_user.is_active = False
    staff_user.save()
    assert login(api_client).status_code == 401


def test_endpoint_interno_exige_sesion(api_client):
    assert api_client.get(ME).status_code == 401


def test_me_con_token_valido(api_client, staff_user):
    bearer(api_client, login(api_client).json()["access"])
    response = api_client.get(ME)
    assert response.status_code == 200
    assert response.json()["username"] == "copy1"


def test_logout_revoca_la_sesion(api_client, staff_user):
    tokens = login(api_client).json()
    bearer(api_client, tokens["access"])
    assert (
        api_client.post("/api/v1/auth/logout", {"refresh": tokens["refresh"]}, format="json").status_code
        == 204
    )
    assert api_client.get(ME).status_code == 401
    # el refresh tambien queda inservible
    api_client.credentials()
    assert api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json").status_code == 401


# --- RNF01: bloqueo tras 5 intentos fallidos --------------------------------
def test_bloquea_15_minutos_tras_5_intentos_fallidos(api_client, staff_user):
    for _ in range(5):
        assert login(api_client, password="mala").status_code == 401
    # con la contrasena correcta igual esta bloqueada
    response = login(api_client)
    assert response.status_code == 423
    assert response.json()["code"] == "account_locked"
    with time_machine.travel(timezone.now() + timedelta(minutes=14), tick=False):
        assert login(api_client).status_code == 423
    with time_machine.travel(timezone.now() + timedelta(minutes=16), tick=False):
        assert login(api_client).status_code == 200


def test_login_exitoso_reinicia_el_contador(api_client, staff_user):
    for _ in range(4):
        login(api_client, password="mala")
    assert login(api_client).status_code == 200
    for _ in range(4):
        login(api_client, password="mala")
    assert login(api_client).status_code == 200  # no llego a 5 seguidos


def test_cuatro_fallos_no_bloquean(api_client, staff_user):
    for _ in range(4):
        login(api_client, password="mala")
    assert login(api_client).status_code == 200


# --- RNF01: cierre por 30 min de inactividad --------------------------------
@pytest.fixture
def long_lived_tokens(monkeypatch):
    """Access de 2 h para aislar la inactividad de la expiracion propia del JWT.
    SimpleJWT lee `lifetime` al importar la clase, por eso se parchea el atributo."""
    from rest_framework_simplejwt.tokens import AccessToken

    monkeypatch.setattr(AccessToken, "lifetime", timedelta(hours=2))


def test_sesion_expira_tras_30_minutos_sin_actividad(api_client, staff_user, long_lived_tokens):
    access = login(api_client).json()["access"]
    bearer(api_client, access)
    with time_machine.travel(timezone.now() + timedelta(minutes=29), tick=False):
        assert api_client.get(ME).status_code == 200
    # la actividad de arriba renueva la ventana: 29 + 29 min sigue viva
    with time_machine.travel(timezone.now() + timedelta(minutes=58), tick=False):
        assert api_client.get(ME).status_code == 200
    with time_machine.travel(timezone.now() + timedelta(minutes=58 + 31), tick=False):
        response = api_client.get(ME)
        assert response.status_code == 401
        assert response.json()["code"] == "session_expired"


def test_refresh_falla_si_hubo_inactividad(api_client, staff_user):
    tokens = login(api_client).json()
    with time_machine.travel(timezone.now() + timedelta(minutes=31), tick=False):
        response = api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
        assert response.status_code == 401
        assert response.json()["code"] == "session_expired"


def test_refresh_rota_el_token(api_client, staff_user):
    tokens = login(api_client).json()
    response = api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert response.status_code == 200
    assert response.json()["refresh"] != tokens["refresh"]
    # el anterior queda en la lista negra
    assert api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json").status_code == 401


def test_sesiones_revocadas_no_permiten_refresh(api_client, staff_user):
    tokens = login(api_client).json()
    UserSession.objects.update(revoked_at=timezone.now())
    assert api_client.post(REFRESH, {"refresh": tokens["refresh"]}, format="json").status_code == 401


def test_desactivar_usuario_cierra_sus_sesiones(api_client, admin_client, staff_user):
    bearer(api_client, login(api_client).json()["access"])
    assert api_client.get(ME).status_code == 200
    assert admin_client.post(f"/api/v1/usuarios/{staff_user.pk}/desactivar").status_code == 200
    assert api_client.get(ME).status_code == 401


def test_modelo_de_usuario_es_el_configurado():
    assert get_user_model()._meta.label == "accounts.User"
