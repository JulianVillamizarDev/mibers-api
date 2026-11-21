"""Comando meta_smoke: prepara una publicacion de prueba sin llamar a Meta con --fake."""

import re
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from PIL import Image

from modules.clients.models import Cliente, CuentaSocial
from modules.content.models import Pieza
from modules.core import jobs
from modules.publishing.management.commands.meta_smoke import CLIENT_NAME, COPY
from modules.publishing.models import TareaPublicacion

pytestmark = pytest.mark.django_db


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "smoke.png"
    Image.new("RGB", (1080, 1350), (20, 80, 140)).save(path, format="PNG")
    return path


@pytest.fixture(autouse=True)
def smoke_settings(settings):
    settings.META_PUBLISHING = "graph"
    settings.META_SYSTEM_USER_TOKEN = "TOKEN-QUE-NO-DEBE-SALIR"
    settings.PUBLIC_BASE_URL = "https://api.example.com"
    settings.PUBLIC_MEDIA_TTL_SECONDS = 3600


def _piece_id(output: str) -> str:
    match = re.search(r"Pieza: ([0-9a-f-]{36})", output)
    assert match, output
    return match.group(1)


def test_meta_smoke_fake_crea_cliente_cuentas_pieza_y_tareas(image_path, settings):
    before = timezone.now() + timedelta(minutes=2)
    output = StringIO()

    call_command(
        "meta_smoke",
        "--red",
        "ambas",
        "--imagen",
        str(image_path),
        "--fake",
        stdout=output,
    )
    text = output.getvalue()

    assert "Pieza:" in text and "Tarea INSTAGRAM:" in text and "Tarea FACEBOOK:" in text
    assert "TOKEN-QUE-NO-DEBE-SALIR" not in text
    piece = Pieza.objects.get(pk=_piece_id(text))
    assert piece.estado == "PROGRAMADA" and piece.copy_final("INSTAGRAM") == COPY
    assert before <= piece.programada_para <= timezone.now() + timedelta(minutes=2)

    user = get_user_model().objects.get(username="smoke-admin")
    assert user.role == "ADMIN" and user.is_active
    cliente = Cliente.objects.get(nombre=CLIENT_NAME)
    accounts = {a.red: a for a in CuentaSocial.objects.filter(cliente=cliente)}
    assert accounts["FACEBOOK"].id_externo == "158691610656662"
    assert accounts["INSTAGRAM"].id_externo == "17841480467776205"
    assert accounts["FACEBOOK"].estado == accounts["INSTAGRAM"].estado == "VINCULADA"

    tasks = {task.red: task for task in TareaPublicacion.objects.filter(pieza=piece)}
    assert set(tasks) == {"INSTAGRAM", "FACEBOOK"}
    assert all(task.estado == "PENDIENTE" and task.modo == "AUTOMATICA" for task in tasks.values())
    jobs_queued = jobs.Job.objects.filter(kind="publishing.graph_publish")
    assert jobs_queued.count() == 2
    assert all(job.run_at == piece.programada_para for job in jobs_queued)
    assert settings.META_PUBLISHING == "graph"  # --fake restaura el modo del proceso


def test_meta_smoke_reutiliza_usuario_cliente_y_cuentas(image_path):
    for red in ("instagram", "facebook"):
        call_command(
            "meta_smoke",
            "--red",
            red,
            "--imagen",
            str(image_path),
            "--fake",
            stdout=StringIO(),
        )

    user_model = get_user_model()
    assert user_model.objects.filter(username="smoke-admin").count() == 1
    assert Cliente.objects.filter(nombre=CLIENT_NAME).count() == 1
    assert CuentaSocial.objects.filter(cliente__nombre=CLIENT_NAME).count() == 2
    assert Pieza.objects.count() == 2


@pytest.mark.parametrize("fake", [False, True])
def test_meta_smoke_rechaza_si_no_es_graph(image_path, settings, fake):
    settings.META_PUBLISHING = "manual"
    args = ["meta_smoke", "--red", "instagram", "--imagen", str(image_path)]
    if fake:
        args.append("--fake")
    with pytest.raises(CommandError, match="META_PUBLISHING"):
        call_command(*args)


@pytest.mark.parametrize("base", ["http://localhost:8000", "http://127.0.0.1:8000"])
def test_meta_smoke_rechaza_public_base_url_local(image_path, settings, base):
    settings.PUBLIC_BASE_URL = base
    settings.META_PUBLISHING = "graph"
    settings.META_SYSTEM_USER_TOKEN = "TOKEN"
    with pytest.raises(CommandError, match="PUBLIC_BASE_URL"):
        call_command("meta_smoke", "--red", "instagram", "--imagen", str(image_path))
