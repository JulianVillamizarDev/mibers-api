"""GraphClient y FakeGraphClient sin red real (httpx.MockTransport)."""

import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.core.exceptions import ImproperlyConfigured

from modules.publishing import graph
from modules.publishing.graph import FakeGraphClient, GraphClient, GraphError


def make_client(handler, *, token="TOKEN", app_secret="SECRET", version="v25.0"):
    return GraphClient(
        token=token,
        app_secret=app_secret,
        version=version,
        transport=httpx.MockTransport(handler),
    )


def ok_json(request, payload=None):
    return httpx.Response(200, json=payload or {"id": "1"})


def test_get_envia_token_y_appsecret_proof():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        return ok_json(request, {"data": []})

    assert make_client(handler).get("/me", {"fields": "id,name"}) == {"data": []}
    parts = urlsplit(seen["url"])
    query = parse_qs(parts.query)
    assert parts.path == "/v25.0/me" and seen["auth"] == "Bearer TOKEN"
    assert query["fields"] == ["id,name"] and "access_token" not in query
    expected = hmac.new(b"SECRET", b"TOKEN", hashlib.sha256).hexdigest()
    assert query["appsecret_proof"] == [expected]


def test_sin_app_secret_no_envia_appsecret_proof():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen.update(parse_qs(urlsplit(str(request.url)).query))
        return ok_json(request)

    make_client(handler, app_secret="").get("/me")
    assert seen["auth"] == "Bearer TOKEN"
    assert "access_token" not in seen and "appsecret_proof" not in seen


def test_permite_usar_el_token_de_la_pagina_en_una_llamada():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["query"] = parse_qs(urlsplit(str(request.url)).query)
        return ok_json(request, {"data": []})

    make_client(handler).get("/page", token="PAGE_TOKEN")
    assert seen["auth"] == "Bearer PAGE_TOKEN"
    expected = hmac.new(b"SECRET", b"PAGE_TOKEN", hashlib.sha256).hexdigest()
    assert seen["query"]["appsecret_proof"] == [expected]


def test_post_envia_los_datos_como_formulario_con_credenciales():
    seen = {}

    def handler(request):
        seen["form"] = parse_qs(request.content.decode())
        seen["content_type"] = request.headers["content-type"]
        seen["auth"] = request.headers["Authorization"]
        return ok_json(request, {"id": "media-1"})

    result = make_client(handler).post("/ig/media", {"image_url": "https://api.test/foto.jpg"})
    assert result == {"id": "media-1"}
    assert seen["content_type"].startswith("application/x-www-form-urlencoded")
    assert seen["form"]["image_url"] == ["https://api.test/foto.jpg"]
    assert seen["auth"] == "Bearer TOKEN" and "access_token" not in seen["form"]
    assert "appsecret_proof" in seen["form"]


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (500, None, True),
        (429, None, True),
        (400, 4, True),  # limite de la Graph API
        (400, 9007, True),  # media no lista
        (400, 190, False),  # token invalido
        (400, 100, False),  # parametro invalido
    ],
)
def test_clasifica_errores_de_meta(status, code, retryable):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "boom", "code": code}})

    with pytest.raises(GraphError) as error:
        make_client(handler).get("/me")
    assert error.value.code == code
    assert error.value.retryable is retryable
    assert "boom" in str(error.value)


def test_subcodigo_de_media_no_lista_es_reintentable():
    def handler(request):
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Media not ready",
                    "code": 1,
                    "error_subcode": 2207027,
                }
            },
        )

    with pytest.raises(GraphError) as error:
        make_client(handler).post("/ig/media_publish", {"creation_id": "C1"})
    assert error.value.retryable is True and error.value.subcode == 2207027


def test_error_de_red_es_reintentable():
    def handler(request):
        raise httpx.ConnectTimeout("sin respuesta")

    with pytest.raises(GraphError) as error:
        make_client(handler).get("/me")
    assert error.value.retryable is True


def test_respuesta_exitosa_que_no_es_json_es_error():
    with pytest.raises(GraphError) as error:
        make_client(lambda request: httpx.Response(200, text="<html>")).get("/me")
    assert error.value.retryable is False


def test_fake_graph_client_registra_llamadas_y_respuestas():
    fallo = GraphError("sin permisos", code=200, retryable=False)
    fake = FakeGraphClient([{"data": [{"id": "pagina"}]}, fallo])

    assert fake.get("/me/accounts") == {"data": [{"id": "pagina"}]}
    with pytest.raises(GraphError):
        fake.post("/ig/media", {"image_url": "https://api.test/foto.jpg"})
    assert fake.calls == [
        {"method": "GET", "path": "/me/accounts", "params": {}, "data": {}},
        {
            "method": "POST",
            "path": "/ig/media",
            "params": {},
            "data": {"image_url": "https://api.test/foto.jpg"},
        },
    ]


def test_get_graph_client_segun_configuracion(settings):
    settings.META_PUBLISHING = "fake"
    assert isinstance(graph.get_graph_client(), FakeGraphClient)

    settings.META_PUBLISHING = "manual"
    with pytest.raises(ImproperlyConfigured):
        graph.get_graph_client()

    settings.META_PUBLISHING = "graph"
    settings.META_SYSTEM_USER_TOKEN = ""
    with pytest.raises(ImproperlyConfigured):
        graph.get_graph_client()

    settings.META_SYSTEM_USER_TOKEN = "TOKEN"
    client = graph.get_graph_client()
    assert isinstance(client, GraphClient)
    assert client.base == f"{graph.GRAPH_BASE_URL}/{settings.META_GRAPH_VERSION}"
    client.client.close()
