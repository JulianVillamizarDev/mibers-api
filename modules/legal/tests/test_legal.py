from datetime import timedelta

import pytest
from django.utils import timezone

from modules.legal.models import PoliticaTratamiento

pytestmark = pytest.mark.django_db

PUBLIC = "/api/v1/legal/politica"
ADMIN = "/api/v1/legal/politicas"


def test_politica_publica_sin_sesion_devuelve_la_vigente(api_client):
    PoliticaTratamiento.objects.create(
        version="1.0", titulo="Politica", contenido="viejo", vigente_desde=timezone.now() - timedelta(days=30)
    )
    PoliticaTratamiento.objects.create(version="2.0", titulo="Politica", contenido="nuevo")
    response = api_client.get(PUBLIC)
    assert response.status_code == 200
    assert response.json()["version"] == "2.0" and response.json()["contenido"] == "nuevo"


def test_sin_politica_publicada_responde_404(api_client):
    response = api_client.get(PUBLIC)
    assert response.status_code == 404 and response.json()["code"] == "policy_not_published"


def test_solo_el_admin_publica_versiones(admin_client, staff_client):
    body = {"version": "1.0", "titulo": "Politica de tratamiento", "contenido": "Texto"}
    assert staff_client.post(ADMIN, body, format="json").status_code == 403
    assert admin_client.post(ADMIN, body, format="json").status_code == 201
    assert admin_client.post(ADMIN, body, format="json").status_code == 400  # version repetida
    assert len(admin_client.get(ADMIN).json()) == 1
