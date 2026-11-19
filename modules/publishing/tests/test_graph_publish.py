"""Publicacion automatica por Graph API: mapa, idempotencia, esperas y fallos (PLN0001 T2)."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from modules.audit.models import AuditLog
from modules.clients.models import CuentaSocial
from modules.content import services as content
from modules.content.models import Pieza
from modules.content.tests.helpers import image_file, patch_video_probe, video_file
from modules.core import jobs as job_queue
from modules.notifications.gateway import FakeGateway
from modules.notifications.models import Alerta
from modules.publishing import jobs as graph_jobs
from modules.publishing import services
from modules.publishing.graph import FakeGraphClient, GraphError
from modules.publishing.models import TareaPublicacion
from modules.publishing.publishers import (
    GraphApiPublisher,
    ManualPublisher,
    get_publisher,
    supports,
)

from .helpers import make_client_with_accounts

pytestmark = pytest.mark.django_db

IG_ACCOUNT = "ig-123"
FB_ACCOUNT = "page-456"
IG_LINK = "https://www.instagram.com/p/IGPOST/"
FB_LINK = "https://www.facebook.com/cafe/posts/FBPOST"


@pytest.fixture(autouse=True)
def graph_settings(settings):
    settings.META_PUBLISHING = "fake"
    settings.META_SYSTEM_USER_TOKEN = ""
    settings.PUBLIC_BASE_URL = "https://api.test"
    settings.PUBLIC_MEDIA_TTL_SECONDS = 3600
    settings.WHATSAPP_BACKEND = "fake"
    FakeGateway.reset()
    yield
    FakeGateway.reset()


@pytest.fixture
def cliente():
    cliente = make_client_with_accounts()
    CuentaSocial.objects.filter(cliente=cliente, red="INSTAGRAM").update(id_externo=IG_ACCOUNT)
    CuentaSocial.objects.filter(cliente=cliente, red="FACEBOOK").update(id_externo=FB_ACCOUNT)
    return cliente


@pytest.fixture
def fake_client(monkeypatch):
    fake = FakeGraphClient()
    monkeypatch.setattr(graph_jobs, "get_graph_client", lambda: fake)
    return fake


def _image_size(red: str, tipo: str) -> tuple[int, int]:
    if tipo == "HISTORIA":
        return 1080, 1920
    if tipo == "PORTADA":
        return (1080, 1080) if red == "INSTAGRAM" else (1350, 540)
    return 1080, 1350


def _video_size(red: str, tipo: str) -> tuple[int, int]:
    if tipo in {"REEL", "HISTORIA"}:
        return 1080, 1920
    return 1080, 1200


@pytest.fixture
def make_task(cliente, staff_user, admin_user, monkeypatch):
    def make(
        red="INSTAGRAM",
        tipo="POST",
        kinds=("image",),
        *,
        account=None,
        external=None,
        estado=TareaPublicacion.Estado.PENDIENTE,
    ):
        # Las reglas de contenido aun no permiten crear un POST de Instagram con video.
        # Se construye como REEL (valido) y se cambia el tipo para probar el mapa del publicador.
        creation_tipo = "REEL" if red == "INSTAGRAM" and tipo == "POST" and "video" in kinds else tipo
        piece = content.create_piece(
            cliente=cliente,
            tipo=creation_tipo,
            acting_user=staff_user,
            redes=[{"red": red}],
            programada_para=timezone.now() + timedelta(days=2),
            copy_comun="Ven por tu cafe",
            titulo="Promo",
        )
        for index, kind in enumerate(kinds):
            if kind == "image":
                width, height = _image_size(red, tipo)
                upload = image_file(width, height, name=f"f{index}.png")
            else:
                width, height = _video_size(red, tipo)
                patch_video_probe(monkeypatch, width=width, height=height, duration=10.0)
                upload = video_file(name=f"v{index}.mp4")
            content.add_file(piece, upload, acting_user=staff_user)
        piece = content.submit_for_review(piece, acting_user=staff_user)
        piece = content.approve(piece, acting_user=admin_user)
        piece = services.schedule_piece(piece, acting_user=staff_user, confirmed=True)
        if creation_tipo != tipo:
            Pieza.objects.filter(pk=piece.pk).update(tipo=tipo)
            piece.tipo = tipo

        cuenta = account or CuentaSocial.objects.get(cliente=cliente, red=red)
        task = TareaPublicacion.objects.create(
            pieza=piece,
            red=red,
            cuenta=cuenta,
            cuenta_handle=cuenta.handle,
            cuenta_url=cuenta.url_perfil,
            estado=estado,
            copy_final=piece.copy_final(red),
            programada_para=piece.programada_para,
            **(external or {}),
        )
        return piece, task

    return make


def run(task, *, last_attempt=False, waits=0):
    graph_jobs.run_graph_publish(task.pk, last_attempt=last_attempt, waits=waits)
    task.refresh_from_db()
    return task


def calls(fake):
    return [(call["method"], call["path"]) for call in fake.calls]


def test_instagram_post_con_imagen(fake_client, make_task):
    fake_client.responses = [
        {"id": "IGC1"},
        {"status_code": "FINISHED"},
        {"id": "IGP1"},
        {"permalink": IG_LINK},
    ]
    piece, task = make_task("INSTAGRAM", "POST", ("image",))

    task = run(task)

    assert calls(fake_client) == [
        ("POST", f"/{IG_ACCOUNT}/media"),
        ("GET", "/IGC1"),
        ("POST", f"/{IG_ACCOUNT}/media_publish"),
        ("GET", "/IGP1"),
    ]
    first = fake_client.calls[0]["data"]
    assert first["image_url"].startswith("https://api.test/api/v1/public/media/")
    assert first["caption"] == "Ven por tu cafe"
    assert fake_client.calls[2]["data"]["creation_id"] == "IGC1"
    assert task.estado == "PUBLICADA" and task.enlace == IG_LINK
    assert task.modo == "AUTOMATICA" and task.externo_post_id == "IGP1"
    assert task.externo_estado == "PUBLICADO" and task.gestionada_por is None
    piece.refresh_from_db()
    assert piece.estado == "PUBLICADA"


def test_instagram_post_con_video_usa_reels_y_espera(fake_client, make_task):
    fake_client.responses = [
        {"id": "IGC1"},
        {"status_code": "FINISHED"},
        {"id": "IGP1"},
        {"permalink": IG_LINK},
    ]
    _, task = make_task("INSTAGRAM", "POST", ("video",))

    task = run(task)

    assert fake_client.calls[0]["data"]["media_type"] == "REELS"
    assert fake_client.calls[0]["data"]["video_url"].startswith("https://api.test/")
    assert calls(fake_client)[1] == ("GET", "/IGC1")
    assert task.estado == "PUBLICADA"


def test_instagram_carrusel_crea_hijos_y_contenedor(fake_client, make_task):
    fake_client.responses = [
        {"id": "IGC1"},
        {"id": "IGC2"},
        {"id": "IGPARENT"},
        {"status_code": "FINISHED"},
        {"id": "IGP1"},
        {"permalink": IG_LINK},
    ]
    _, task = make_task("INSTAGRAM", "CARRUSEL", ("image", "image"))

    task = run(task)

    assert [call["data"].get("is_carousel_item") for call in fake_client.calls[:2]] == [
        "true",
        "true",
    ]
    parent = fake_client.calls[2]
    assert parent["path"] == f"/{IG_ACCOUNT}/media"
    assert json.loads(parent["data"]["children"]) == ["IGC1", "IGC2"]
    assert parent["data"]["media_type"] == "CAROUSEL"
    assert task.externo_contenedor_id == "IGPARENT" and task.estado == "PUBLICADA"


def test_instagram_reel(fake_client, make_task):
    fake_client.responses = [
        {"id": "IGC1"},
        {"status_code": "FINISHED"},
        {"id": "IGP1"},
        {"permalink": IG_LINK},
    ]
    _, task = make_task("INSTAGRAM", "REEL", ("video",))

    assert run(task).estado == "PUBLICADA"
    assert fake_client.calls[0]["data"]["media_type"] == "REELS"


def test_instagram_historia(fake_client, make_task):
    fake_client.responses = [
        {"id": "IGC1"},
        {"status_code": "FINISHED"},
        {"id": "IGP1"},
        {"permalink": IG_LINK},
    ]
    _, task = make_task("INSTAGRAM", "HISTORIA", ("image",))

    assert run(task).estado == "PUBLICADA"
    data = fake_client.calls[0]["data"]
    assert data["media_type"] == "STORIES" and data["image_url"].startswith("https://api.test/")
    assert "caption" not in data


def test_facebook_post_con_foto(fake_client, make_task):
    fake_client.responses = [
        {"access_token": "PAGE_TOKEN"},
        {"post_id": "FBPOST"},
        {"permalink_url": FB_LINK},
    ]
    _, task = make_task("FACEBOOK", "POST", ("image",))

    task = run(task)

    assert calls(fake_client) == [
        ("GET", f"/{FB_ACCOUNT}"),
        ("POST", f"/{FB_ACCOUNT}/photos"),
        ("GET", "/FBPOST"),
    ]
    photo = fake_client.calls[1]
    assert photo["data"]["published"] == "true"
    assert photo["data"]["caption"] == "Ven por tu cafe"
    assert photo["token"] == "PAGE_TOKEN"
    assert task.estado == "PUBLICADA" and task.enlace == FB_LINK


def test_facebook_post_con_video(fake_client, make_task):
    fake_client.responses = [
        {"access_token": "PAGE_TOKEN"},
        {"id": "FBVIDEO"},
        {"permalink_url": FB_LINK},
    ]
    _, task = make_task("FACEBOOK", "POST", ("video",))

    assert run(task).estado == "PUBLICADA"
    video = fake_client.calls[1]
    assert video["path"] == f"/{FB_ACCOUNT}/videos"
    assert video["data"]["description"] == "Ven por tu cafe"
    assert video["data"]["file_url"].startswith("https://api.test/")


def test_facebook_carrusel_sube_fotos_y_publica_en_feed(fake_client, make_task):
    fake_client.responses = [
        {"access_token": "PAGE_TOKEN"},
        {"id": "PHOTO1"},
        {"id": "PHOTO2"},
        {"id": "FBPOST"},
        {"permalink_url": FB_LINK},
    ]
    _, task = make_task("FACEBOOK", "CARRUSEL", ("image", "image"))

    task = run(task)

    photos = fake_client.calls[1:3]
    assert all(call["data"]["published"] == "false" for call in photos)
    feed = fake_client.calls[3]
    assert feed["path"] == f"/{FB_ACCOUNT}/feed"
    assert json.loads(feed["data"]["attached_media"]) == [
        {"media_fbid": "PHOTO1"},
        {"media_fbid": "PHOTO2"},
    ]
    assert task.externo_contenedor_id == "PHOTO1,PHOTO2" and task.estado == "PUBLICADA"


def test_facebook_reel(fake_client, make_task):
    fake_client.responses = [
        {"access_token": "PAGE_TOKEN"},
        {"id": "FBVIDEO"},
        {"permalink_url": FB_LINK},
    ]
    _, task = make_task("FACEBOOK", "REEL", ("video",))

    assert run(task).estado == "PUBLICADA"
    assert fake_client.calls[1]["path"] == f"/{FB_ACCOUNT}/videos"


@pytest.mark.parametrize(
    ("red", "tipo", "kinds", "expected"),
    [
        ("INSTAGRAM", "POST", ["image"], True),
        ("INSTAGRAM", "POST", ["video"], True),
        ("INSTAGRAM", "CARRUSEL", ["image", "image"], True),
        ("INSTAGRAM", "REEL", ["video"], True),
        ("INSTAGRAM", "HISTORIA", ["image"], True),
        ("INSTAGRAM", "PORTADA", ["image"], False),
        ("FACEBOOK", "POST", ["image"], True),
        ("FACEBOOK", "POST", ["video"], True),
        ("FACEBOOK", "CARRUSEL", ["image", "image"], True),
        ("FACEBOOK", "CARRUSEL", ["video", "image"], False),
        ("FACEBOOK", "REEL", ["video"], True),
        ("FACEBOOK", "HISTORIA", ["image"], False),
        ("FACEBOOK", "PORTADA", ["image"], False),
    ],
)
def test_mapa_tipo_red(red, tipo, kinds, expected):
    assert supports(red, tipo, kinds) is expected


@pytest.mark.parametrize(
    ("red", "tipo"),
    [("INSTAGRAM", "PORTADA"), ("FACEBOOK", "HISTORIA"), ("FACEBOOK", "PORTADA")],
)
def test_tipos_no_soportados_quedan_en_manual(fake_client, make_task, red, tipo):
    _, task = make_task(red, tipo, ("image",))

    GraphApiPublisher().dispatch(task)

    task.refresh_from_db()
    assert task.modo == "MANUAL" and task.externo_estado == "MANUAL"
    assert not job_queue.Job.objects.filter(kind=graph_jobs.JOB_KIND).exists()
    assert fake_client.calls == []


def test_dispatch_solo_encola_el_job(fake_client, make_task):
    _, task = make_task("INSTAGRAM", "POST", ("image",))

    GraphApiPublisher().dispatch(task)

    task.refresh_from_db()
    job = job_queue.Job.objects.get(kind=graph_jobs.JOB_KIND)
    assert job.status == "PENDING" and job.payload == {"tarea_id": str(task.pk)}
    assert task.modo == "AUTOMATICA" and task.externo_estado == "ENCOLADO"
    assert fake_client.calls == []


def test_get_publisher_cae_a_manual_sin_credenciales(settings, cliente):
    settings.META_PUBLISHING = "graph"
    settings.META_SYSTEM_USER_TOKEN = ""
    cuenta = CuentaSocial.objects.get(cliente=cliente, red="INSTAGRAM")
    assert isinstance(get_publisher("INSTAGRAM", cuenta), ManualPublisher)


def test_get_publisher_cae_a_manual_sin_id_externo(settings, cliente):
    settings.META_PUBLISHING = "fake"
    cuenta = CuentaSocial.objects.get(cliente=cliente, red="INSTAGRAM")
    cuenta.id_externo = ""
    cuenta.save(update_fields=["id_externo", "updated_at"])
    assert isinstance(get_publisher("INSTAGRAM", cuenta), ManualPublisher)


def test_get_publisher_usa_graph_con_credenciales_y_cuenta_externa(cliente):
    cuenta = CuentaSocial.objects.get(cliente=cliente, red="INSTAGRAM")
    assert isinstance(get_publisher("INSTAGRAM", cuenta), GraphApiPublisher)


def test_video_en_proceso_reen_cola_sin_dormir(fake_client, make_task):
    fake_client.responses = [{"id": "IGC1"}, {"status_code": "IN_PROGRESS"}]
    _, task = make_task("INSTAGRAM", "POST", ("video",))

    task = run(task)

    task.refresh_from_db()
    poll = job_queue.Job.objects.get(kind=graph_jobs.JOB_KIND)
    assert task.estado == "PENDIENTE" and task.externo_estado == "PROCESANDO"
    assert task.externo_contenedor_id == "IGC1" and not task.externo_post_id
    assert poll.payload == {"tarea_id": str(task.pk), "esperas": 1}
    assert poll.run_at > timezone.now() + timedelta(seconds=10)

    fake_client.responses.extend([{"status_code": "FINISHED"}, {"id": "IGP1"}, {"permalink": IG_LINK}])
    job_queue.process_once(now=timezone.now() + timedelta(seconds=30))

    task.refresh_from_db()
    assert task.estado == "PUBLICADA" and task.externo_post_id == "IGP1"


def test_imagen_con_contenedor_en_proceso_tambien_reen_cola(fake_client, make_task):
    fake_client.responses = [{"id": "IGC1"}, {"status_code": "IN_PROGRESS"}]
    _, task = make_task("INSTAGRAM", "POST", ("image",))

    task = run(task)

    task.refresh_from_db()
    poll = job_queue.Job.objects.get(kind=graph_jobs.JOB_KIND)
    assert task.estado == "PENDIENTE" and task.externo_estado == "PROCESANDO"
    assert poll.payload["esperas"] == 1

    fake_client.responses.extend([{"status_code": "FINISHED"}, {"id": "IGP1"}, {"permalink": IG_LINK}])
    job_queue.process_once(now=timezone.now() + timedelta(seconds=30))

    task.refresh_from_db()
    assert task.estado == "PUBLICADA"


def test_error_reintentable_deja_el_job_pendiente(fake_client, make_task):
    fake_client.responses = [GraphError("Meta no responde", code=1, retryable=True)]
    _, task = make_task("INSTAGRAM", "POST", ("image",))
    GraphApiPublisher().dispatch(task)

    job_queue.process_once()

    task.refresh_from_db()
    job = job_queue.Job.objects.get(kind=graph_jobs.JOB_KIND)
    assert task.estado == "PENDIENTE" and task.externo_estado == "REINTENTANDO"
    assert job.status == "PENDING" and job.attempts == 1 and job.run_at > timezone.now()
    assert not Alerta.objects.filter(tipo="PUBLICATION_FAILED").exists()


def test_media_no_lista_es_reintentable_en_el_job(fake_client, make_task):
    fake_client.responses = [
        {"id": "IGC1"},
        {"status_code": "FINISHED"},
        GraphError("Media not ready", code=9007, retryable=True),
    ]
    _, task = make_task("INSTAGRAM", "POST", ("image",))
    GraphApiPublisher().dispatch(task)

    job_queue.process_once()

    task.refresh_from_db()
    job = job_queue.Job.objects.get(kind=graph_jobs.JOB_KIND)
    assert task.estado == "PENDIENTE" and task.externo_estado == "REINTENTANDO"
    assert job.status == "PENDING"


def test_error_definitivo_falla_y_alerta_con_codigo(fake_client, make_task):
    fake_client.responses = [GraphError("Permiso denegado por Meta", code=190, retryable=False)]
    _, task = make_task("INSTAGRAM", "POST", ("image",))

    task = run(task)

    assert task.estado == "FALLIDA" and task.externo_estado == "FALLIDO"
    assert "No se pudo publicar en Instagram" in task.motivo
    assert "codigo 190" in task.motivo
    alert = Alerta.objects.get(tipo="PUBLICACION_FALLIDA")
    assert "codigo 190" in alert.mensaje
    entry = AuditLog.objects.get(action="PUBLICATION_FAILED")
    assert entry.actor is None and entry.actor_username == "sistema"


def test_ultimo_intento_reintentable_falla(fake_client, make_task):
    fake_client.responses = [GraphError("temporal", retryable=True)]
    _, task = make_task("INSTAGRAM", "POST", ("image",))

    task = run(task, last_attempt=True)

    assert task.estado == "FALLIDA"


def test_cuenta_de_otro_cliente_bloquea_sin_llamar_meta(fake_client, make_task):
    otro = make_client_with_accounts("Otro Cliente")
    CuentaSocial.objects.filter(cliente=otro, red="INSTAGRAM").update(id_externo="otra-cuenta")
    cuenta_ajena = CuentaSocial.objects.get(cliente=otro, red="INSTAGRAM")
    _, task = make_task("INSTAGRAM", "POST", ("image",), account=cuenta_ajena)

    task = run(task)

    assert fake_client.calls == []
    assert task.estado == "FALLIDA" and "no pertenece al cliente" in task.motivo
    assert Alerta.objects.filter(tipo="PUBLICACION_FALLIDA").count() == 1


def test_no_republica_si_ya_existe_post(fake_client, make_task):
    fake_client.responses = [{"permalink": IG_LINK}]
    _, task = make_task(
        "INSTAGRAM",
        "POST",
        ("image",),
        external={"externo_post_id": "IGPOST", "modo": "AUTOMATICA"},
    )

    task = run(task)

    assert calls(fake_client) == [("GET", "/IGPOST")]
    assert task.estado == "PUBLICADA" and task.externo_post_id == "IGPOST"


def test_reutiliza_contenedor_existente(fake_client, make_task):
    fake_client.responses = [
        {"status_code": "FINISHED"},
        {"id": "IGP1"},
        {"permalink": IG_LINK},
    ]
    _, task = make_task(
        "INSTAGRAM",
        "POST",
        ("image",),
        external={"externo_contenedor_id": "IGC1", "modo": "AUTOMATICA"},
    )

    task = run(task)

    assert calls(fake_client) == [
        ("GET", "/IGC1"),
        ("POST", f"/{IG_ACCOUNT}/media_publish"),
        ("GET", "/IGP1"),
    ]
    assert task.estado == "PUBLICADA"


def test_reejecutar_un_job_terminado_no_publica_dos_veces(fake_client, make_task):
    fake_client.responses = [
        {"id": "IGC1"},
        {"status_code": "FINISHED"},
        {"id": "IGP1"},
        {"permalink": IG_LINK},
    ]
    _, task = make_task("INSTAGRAM", "POST", ("image",))
    task = run(task)
    before = len(fake_client.calls)

    run(task)

    assert len(fake_client.calls) == before
    assert AuditLog.objects.filter(action="PUBLICATION_PUBLISHED").count() == 1


def test_reintento_manual_limpia_un_contenedor_sin_post(fake_client, make_task, staff_user):
    _, task = make_task(
        "INSTAGRAM",
        "POST",
        ("image",),
        estado="FALLIDA",
        external={
            "modo": "AUTOMATICA",
            "externo_contenedor_id": "IGC1",
            "externo_estado": "FALLIDO",
        },
    )

    services.retry(task, acting_user=staff_user)

    task.refresh_from_db()
    assert task.estado == "PENDIENTE" and task.externo_contenedor_id == ""
    assert job_queue.Job.objects.get(kind=graph_jobs.JOB_KIND).status == "PENDING"


def test_confirmacion_automatica_audita_como_sistema(fake_client, make_task):
    fake_client.responses = [
        {"id": "IGC1"},
        {"status_code": "FINISHED"},
        {"id": "IGP1"},
        {"permalink": IG_LINK},
    ]
    _, task = make_task("INSTAGRAM", "POST", ("image",))

    run(task)

    entry = AuditLog.objects.get(action="PUBLICATION_PUBLISHED")
    assert entry.actor is None and entry.actor_username == "sistema"
    assert entry.metadata["enlace"] == IG_LINK


def test_reintento_con_post_existente_conciliar_sin_republicar(fake_client, make_task, staff_user):
    fake_client.responses = [{"permalink": IG_LINK}]
    _, task = make_task(
        "INSTAGRAM",
        "POST",
        ("image",),
        estado="FALLIDA",
        external={
            "modo": "AUTOMATICA",
            "externo_contenedor_id": "IGC1",
            "externo_post_id": "IGPOST",
            "externo_estado": "REINTENTANDO",
        },
    )

    services.retry(task, acting_user=staff_user)
    job_queue.process_once()

    task.refresh_from_db()
    assert task.estado == "PUBLICADA" and task.externo_contenedor_id == "IGC1"
    assert calls(fake_client) == [("GET", "/IGPOST")]
    assert AuditLog.objects.filter(action="PUBLICATION_PUBLISHED").count() == 1
