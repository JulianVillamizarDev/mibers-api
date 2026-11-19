"""Fixtures compartidas. Los usuarios se crean con el modelo configurado (sin importar
modulos de capas superiores) para respetar el contrato de import-linter."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from modules.core.roles import Role

PASSWORD = "Contrasena-Segura-123"


def _make_user(username, role, **extra):
    User = get_user_model()
    return User.objects.create_user(username=username, password=PASSWORD, role=role, **extra)


@pytest.fixture
def admin_user(db):
    return _make_user("director", Role.ADMIN, first_name="Directora", last_name="Mibers")


@pytest.fixture
def staff_user(db):
    return _make_user("copy1", Role.STAFF, first_name="Carlos", last_name="Copy", cargo="Copywriter")


@pytest.fixture
def api_client():
    return APIClient()


def _client_for(user):
    # Autenticacion real por JWT + sesion, igual que en produccion.
    from rest_framework_simplejwt.tokens import RefreshToken

    from modules.accounts.models import UserSession

    session = UserSession.objects.create(user=user)
    refresh = RefreshToken.for_user(user)
    refresh["sid"] = str(session.id)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def admin_client(admin_user):
    return _client_for(admin_user)


@pytest.fixture
def staff_client(staff_user):
    return _client_for(staff_user)
