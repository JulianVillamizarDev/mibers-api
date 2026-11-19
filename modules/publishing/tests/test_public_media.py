"""URL firmada que Meta usa para descargar los archivos de una publicacion."""

from datetime import timedelta
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from django.utils import timezone
from PIL import Image

from modules.clients.models import CuentaSocial
from modules.publishing.models import TareaPublicacion
from modules.publishing.public_media import build_public_media_url

from .helpers import build_piece, make_client_with_accounts

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def public_settings(settings):
    settings.PUBLIC_BASE_URL = "https://api.test/"
    settings.PUBLIC_MEDIA_TTL_SECONDS = 3600


@pytest.fixture
def cliente():
    return make_client_with_accounts()


@pytest.fixture
def make_scheduled_piece(cliente, staff_user, admin_user):
    def make(redes=("INSTAGRAM",), estado=TareaPublicacion.Estado.PENDIENTE):
        piece = build_piece(cliente, staff_user, admin_user, redes=redes)
        for red in redes:
            account = CuentaSocial.objects.get(cliente=cliente, red=red)
            TareaPublicacion.objects.create(
                pieza=piece,
                red=red,
                cuenta=account,
                cuenta_handle=account.handle,
                cuenta_url=account.url_perfil,
                estado=estado,
                copy_final=piece.copy_final(red),
                programada_para=timezone.now() + timedelta(minutes=5),
            )
        return piece

    return make


def download(api_client, url: str):
    parts = urlsplit(url)
    return api_client.get(f"{parts.path}?{parts.query}")


def test_la_url_firmada_usa_la_base_publica_y_vence(make_scheduled_piece):
    archivo = make_scheduled_piece(redes=("FACEBOOK",)).archivos.get()
    url = build_public_media_url(archivo)
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert url.startswith("https://api.test/api/v1/public/media/")
    assert parts.path == f"/api/v1/public/media/{archivo.pk}"
    assert int(query["exp"][0]) > int(timezone.now().timestamp())
    assert len(query["sig"][0]) == 64


def test_sin_sesion_descarga_un_archivo_con_tarea_pendiente(api_client, make_scheduled_piece):
    archivo = make_scheduled_piece(redes=("FACEBOOK",)).archivos.get()
    response = download(api_client, build_public_media_url(archivo))
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert b"".join(response.streaming_content).startswith(b"\x89PNG")


def test_tambien_sirve_archivos_de_una_tarea_fallida(api_client, make_scheduled_piece):
    archivo = make_scheduled_piece(redes=("FACEBOOK",), estado="FALLIDA").archivos.get()
    assert download(api_client, build_public_media_url(archivo)).status_code == 200


@pytest.mark.parametrize("estado", ["PUBLICADA", "CANCELADA"])
def test_no_sirve_archivos_sin_tarea_pendiente_o_fallida(api_client, make_scheduled_piece, estado):
    archivo = make_scheduled_piece(redes=("FACEBOOK",), estado=estado).archivos.get()
    assert download(api_client, build_public_media_url(archivo)).status_code == 404


def test_no_sirve_un_archivo_sin_tareas(api_client, cliente, staff_user, admin_user):
    piece = build_piece(cliente, staff_user, admin_user, redes=("FACEBOOK",))
    archivo = piece.archivos.get()
    assert download(api_client, build_public_media_url(archivo)).status_code == 404


def test_rechaza_una_url_vencida(api_client, make_scheduled_piece):
    archivo = make_scheduled_piece(redes=("FACEBOOK",)).archivos.get()
    url = build_public_media_url(archivo, ttl=-1)
    assert download(api_client, url).status_code == 404


def test_rechaza_una_firma_alterada(api_client, make_scheduled_piece):
    archivo = make_scheduled_piece(redes=("FACEBOOK",)).archivos.get()
    parts = urlsplit(build_public_media_url(archivo))
    query = parse_qs(parts.query)
    query["sig"] = ["0" * 64]
    altered = f"{parts.path}?exp={query['exp'][0]}&sig={query['sig'][0]}"
    assert api_client.get(altered).status_code == 404


def test_rechaza_una_url_sin_firma(api_client, make_scheduled_piece):
    archivo = make_scheduled_piece(redes=("FACEBOOK",)).archivos.get()
    assert api_client.get(f"/api/v1/public/media/{archivo.pk}").status_code == 404


def test_instagram_recibe_jpeg_aunque_el_original_sea_png(api_client, make_scheduled_piece):
    archivo = make_scheduled_piece(redes=("INSTAGRAM",)).archivos.get()
    response = download(api_client, build_public_media_url(archivo))
    assert response.status_code == 200
    body = b"".join(response.streaming_content)
    assert response["Content-Type"] == "image/jpeg"
    assert body.startswith(b"\xff\xd8")
    with Image.open(BytesIO(body)) as image:
        assert image.format == "JPEG"


def test_facebook_conserva_el_png_original(api_client, make_scheduled_piece):
    archivo = make_scheduled_piece(redes=("FACEBOOK",)).archivos.get()
    response = download(api_client, build_public_media_url(archivo))
    assert response["Content-Type"] == "image/png"
    assert b"".join(response.streaming_content).startswith(b"\x89PNG")
