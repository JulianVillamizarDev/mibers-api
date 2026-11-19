"""Comando whatsapp_check con transporte simulado; nunca imprime el token."""

import json
from io import StringIO

import httpx
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from modules.notifications.gateway import CloudApiGateway
from modules.notifications.management.commands import whatsapp_check

TOKEN = "TOKEN-WHATSAPP-QUE-NO-DEBE-SALIR"


def make_gateway(handler):
    return CloudApiGateway(
        token=TOKEN,
        phone_number_id="12345",
        language="es",
        transport=httpx.MockTransport(handler),
    )


def test_whatsapp_check_valida_la_configuracion(settings, monkeypatch):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["fields"] = request.url.params["fields"]
        return httpx.Response(
            200,
            json={"display_phone_number": "+1 555 0100", "verified_name": "Mibers Test"},
        )

    gateway = make_gateway(handler)
    monkeypatch.setattr(whatsapp_check, "get_gateway", lambda: gateway)
    settings.WHATSAPP_BACKEND = "cloud"
    output = StringIO()

    call_command("whatsapp_check", stdout=output)

    text = output.getvalue()
    assert seen["path"] == "/v21.0/12345"
    assert seen["fields"] == "display_phone_number,verified_name"
    assert "Mibers Test" in text and "+1 555 0100" in text
    assert TOKEN not in text


def test_whatsapp_check_envia_hello_world_y_muestra_message_id(settings, monkeypatch):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.SMOKE"}]})

    gateway = make_gateway(handler)
    monkeypatch.setattr(whatsapp_check, "get_gateway", lambda: gateway)
    settings.WHATSAPP_BACKEND = "cloud"
    output = StringIO()

    call_command("whatsapp_check", "--enviar-a", "+573001112233", stdout=output)

    text = output.getvalue()
    payload = seen["payload"]
    assert seen["path"] == "/v21.0/12345/messages"
    assert payload["to"] == "573001112233"
    assert payload["template"]["name"] == "hello_world"
    assert payload["template"]["components"] == []
    assert "wamid.SMOKE" in text and TOKEN not in text


def test_whatsapp_check_exige_backend_cloud(settings):
    settings.WHATSAPP_BACKEND = "fake"
    with pytest.raises(CommandError, match="WHATSAPP_BACKEND"):
        call_command("whatsapp_check", stdout=StringIO())
