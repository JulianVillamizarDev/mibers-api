"""CloudApiGateway contra un transporte HTTP simulado (sin red)."""

import json

import httpx
import pytest
from django.core.exceptions import ImproperlyConfigured

from modules.notifications import gateway as gw
from modules.notifications.gateway import CloudApiGateway, WhatsAppError

BASE = "https://graph.facebook.com/v21.0/12345"


def make_gateway(handler):
    return CloudApiGateway(
        token="TOKEN", phone_number_id="12345", language="es", transport=httpx.MockTransport(handler)
    )


def ok_send(request):
    return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})


# --- sanitizacion de parametros -------------------------------------------------------------
def test_sanitiza_saltos_de_linea_tabs_y_espacios_repetidos():
    assert gw.sanitize_param("Hola\n\nmundo\t  como     estas") == "Hola mundo como estas"


def test_recorta_parametros_largos_y_nunca_envia_vacio():
    assert len(gw.sanitize_param("a" * 5000)) == gw.PARAM_MAX_CHARS
    assert gw.sanitize_param("   ") == "-"


# --- envio de plantilla ---------------------------------------------------------------------------
def test_envia_plantilla_solo_texto():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["json"] = (
            str(request.url),
            request.headers["Authorization"],
            json.loads(request.content),
        )
        return ok_send(request)

    result = make_gateway(handler).send_template(
        to="+573001112233", template="alerta_director", body_params=["Fallo\nuna publicacion"]
    )
    assert result.message_id == "wamid.ABC"
    assert seen["url"] == f"{BASE}/messages" and seen["auth"] == "Bearer TOKEN"
    payload = seen["json"]
    assert payload["messaging_product"] == "whatsapp" and payload["to"] == "573001112233"  # sin '+'
    template = payload["template"]
    assert template["name"] == "alerta_director" and template["language"] == {"code": "es"}
    assert template["components"] == [
        {"type": "body", "parameters": [{"type": "text", "text": "Fallo una publicacion"}]}
    ]


def test_sube_el_archivo_y_lo_adjunta_en_la_cabecera(tmp_path):
    calls = []
    media = tmp_path / "foto.png"
    media.write_bytes(b"\x89PNG-bytes")

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/media"):
            assert b'name="messaging_product"' in request.content and b"foto.png" in request.content
            assert request.headers["content-type"].startswith("multipart/form-data")
            return httpx.Response(200, json={"id": "MEDIA-1"})
        body = json.loads(request.content)
        header = body["template"]["components"][0]
        assert header == {"type": "header", "parameters": [{"type": "image", "image": {"id": "MEDIA-1"}}]}
        return ok_send(request)

    header = gw.build_header(str(media), "foto.png", "image")
    make_gateway(handler).send_template(
        to="+573001112233", template="pieza_cliente_imagen", body_params=["Hola"], header=header
    )
    assert calls == [f"{BASE}/media", f"{BASE}/messages"]


def test_documento_incluye_el_nombre_del_archivo(tmp_path):
    media = tmp_path / "reel.mp4"
    media.write_bytes(b"0" * (17 * 1024 * 1024))

    def handler(request):
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "M2"})
        header = json.loads(request.content)["template"]["components"][0]
        assert header["parameters"][0] == {
            "type": "document",
            "document": {"id": "M2", "filename": "reel.mp4"},
        }
        return ok_send(request)

    header = gw.build_header(str(media), "reel.mp4", "video")
    assert header.kind == "document"
    make_gateway(handler).send_template(to="+57300", template="t", body_params=["x"], header=header)


@pytest.mark.parametrize(
    ("kind", "size", "expected"),
    [
        ("image", 5 * gw.MB, "image"),
        ("image", 5 * gw.MB + 1, "document"),
        ("video", 16 * gw.MB, "video"),
        ("video", 16 * gw.MB + 1, "document"),
        ("video", 100 * gw.MB, "document"),
    ],
)
def test_limites_de_tamano_por_tipo_de_cabecera(tmp_path, kind, size, expected):
    path = tmp_path / "f.bin"
    with open(path, "wb") as handle:
        handle.truncate(size)
    assert gw.build_header(str(path), "f.mp4" if kind == "video" else "f.png", kind).kind == expected


def test_mas_de_100mb_no_se_puede_enviar(tmp_path):
    path = tmp_path / "f.mp4"
    with open(path, "wb") as handle:
        handle.truncate(100 * gw.MB + 1)
    with pytest.raises(WhatsAppError) as error:
        gw.build_header(str(path), "f.mp4", "video")
    assert not error.value.retryable


# --- errores ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (500, None, True),
        (503, None, True),
        (429, None, True),
        (400, 80007, True),  # limite de tasa
        (400, 131047, False),  # fuera de la ventana de 24 h
        (400, 132000, False),  # parametros de la plantilla no coinciden
        (401, 190, False),  # token invalido
    ],
)
def test_clasifica_errores_reintentables(status, code, retryable):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "boom", "code": code}})

    with pytest.raises(WhatsAppError) as error:
        make_gateway(handler).send_template(to="+57300", template="t", body_params=["x"])
    assert error.value.retryable is retryable and error.value.code == code and "boom" in str(error.value)


def test_error_de_red_es_reintentable():
    def handler(request):
        raise httpx.ConnectTimeout("sin respuesta")

    with pytest.raises(WhatsAppError) as error:
        make_gateway(handler).send_template(to="+57300", template="t", body_params=["x"])
    assert error.value.retryable


def test_respuesta_sin_json_no_rompe_la_clasificacion():
    with pytest.raises(WhatsAppError) as error:
        make_gateway(lambda r: httpx.Response(502, text="<html>Bad gateway</html>")).send_template(
            to="+57300", template="t", body_params=["x"]
        )
    assert error.value.retryable


def test_respuesta_exitosa_sin_id_es_error():
    with pytest.raises(WhatsAppError):
        make_gateway(lambda r: httpx.Response(200, json={})).send_template(
            to="+57300", template="t", body_params=["x"]
        )


# --- seleccion del adaptador ---------------------------------------------------------------------------------------
def test_get_gateway_segun_configuracion(settings):
    settings.WHATSAPP_BACKEND = "fake"
    assert isinstance(gw.get_gateway(), gw.FakeGateway)
    settings.WHATSAPP_BACKEND = "cloud"
    settings.WHATSAPP_TOKEN = settings.WHATSAPP_PHONE_NUMBER_ID = ""
    with pytest.raises(ImproperlyConfigured):
        gw.get_gateway()
    settings.WHATSAPP_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID = "T", "999"
    assert isinstance(gw.get_gateway(), CloudApiGateway)
    settings.WHATSAPP_BACKEND = "otro"
    with pytest.raises(ImproperlyConfigured):
        gw.get_gateway()
