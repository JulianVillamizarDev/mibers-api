"""Comando meta_check: descubre el usuario, paginas e Instagram sin exponer el token."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from modules.publishing.graph import FakeGraphClient
from modules.publishing.management.commands import meta_check


def test_meta_check_fake_muestra_nombres_e_ids_sin_token(settings):
    settings.META_SYSTEM_USER_TOKEN = "TOKEN-QUE-NO-DEBE-SALIR"
    output = StringIO()

    call_command("meta_check", "--fake", stdout=output)

    text = output.getvalue()
    assert "mibers-media-api" in text and "61596391660589" in text
    assert "Mibers Creativos" in text and "158691610656662" in text
    assert "mibers.creativos" in text and "17841480467776205" in text
    assert "TOKEN-QUE-NO-DEBE-SALIR" not in text
    assert "Usuario del sistema" in text and "Paginas e Instagram" in text


def test_meta_check_sin_token_falla_sin_llamar_a_meta(settings):
    settings.META_SYSTEM_USER_TOKEN = ""
    with pytest.raises(CommandError, match="META_SYSTEM_USER_TOKEN"):
        call_command("meta_check", stdout=StringIO())


def test_meta_check_real_consulta_los_endpoints_del_plan(settings, monkeypatch):
    settings.META_SYSTEM_USER_TOKEN = "SECRET"
    settings.META_APP_SECRET = "APP-SECRET"
    fake = FakeGraphClient(
        [
            {"id": "USER1", "name": "mibers-media-api"},
            {"data": [{"id": "PAGE1", "name": "Pagina Uno"}]},
            {
                "id": "PAGE1",
                "name": "Pagina Uno",
                "instagram_business_account": {"id": "IG1", "username": "pagina.uno"},
            },
        ]
    )
    monkeypatch.setattr(meta_check, "GraphClient", lambda **kwargs: fake)
    output = StringIO()

    call_command("meta_check", stdout=output)

    assert [(call["method"], call["path"]) for call in fake.calls] == [
        ("GET", "/me"),
        ("GET", "/me/accounts"),
        ("GET", "/PAGE1"),
    ]
    assert fake.calls[2]["params"] == {"fields": "name,instagram_business_account{id,username}"}
    text = output.getvalue()
    assert "Pagina Uno" in text and "PAGE1" in text and "IG1" in text
    assert "SECRET" not in text and "APP-SECRET" not in text
