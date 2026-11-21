"""Crea una publicacion real de humo para Instagram y/o Facebook."""

import ipaddress
import mimetypes
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from modules.accounts import services as accounts
from modules.clients import services as clients
from modules.clients.models import Cliente, CuentaSocial, Estrategia
from modules.content import services as content
from modules.core.exceptions import DomainError
from modules.core.networks import Network
from modules.core.roles import Role
from modules.publishing import services as publishing
from modules.publishing.models import TareaPublicacion

COPY = "Prueba de publicacion automatica MIBERS MEDIA PROJECT"
CLIENT_NAME = "Mibers Creativos"
SMOKE_USERNAME = "smoke-admin"

ACCOUNTS = {
    Network.FACEBOOK: {
        "handle": "Mibers Creativos",
        "url_perfil": "https://www.facebook.com/158691610656662",
        "id_externo": "158691610656662",
    },
    Network.INSTAGRAM: {
        "handle": "mibers.creativos",
        "url_perfil": "https://www.instagram.com/mibers.creativos/",
        "id_externo": "17841480467776205",
    },
}


class Command(BaseCommand):
    help = "Crea una pieza de prueba y la programa para publicar en Instagram y/o Facebook."

    def add_arguments(self, parser):
        parser.add_argument("--red", choices=["instagram", "facebook", "ambas"], required=True)
        parser.add_argument("--imagen", required=True, help="Ruta de una imagen JPG o PNG valida")
        parser.add_argument("--en-minutos", type=int, default=2, help="Minutos hasta la publicacion (def. 2)")
        parser.add_argument(
            "--fake",
            action="store_true",
            help="Usa el backend fake para pruebas, sin llamar a Meta",
        )

    def _admin(self):
        User = get_user_model()
        user = User.objects.filter(username=SMOKE_USERNAME).first()
        if user is None:
            user, _ = accounts.create_user(
                username=SMOKE_USERNAME,
                role=Role.ADMIN,
                first_name="Smoke",
                last_name="Admin",
                cargo="Prueba automatica",
            )
            return user
        if not user.is_active:
            accounts.activate_user(user)
        if user.role != Role.ADMIN:
            accounts.update_user(user, role=Role.ADMIN)
        return user

    def _client(self, admin):
        cliente = Cliente.objects.filter(nombre=CLIENT_NAME).first()
        if cliente is None:
            cliente = clients.create_client(
                acting_user=admin,
                nombre=CLIENT_NAME,
                posts_por_semana=1,
                contrato_inicio=timezone.localdate(),
                estrategia=Estrategia.POSICIONAR,
                objetivo="Cliente de prueba para publicacion automatica",
            )
        elif not cliente.activo:
            cliente = clients.reactivate_client(cliente, acting_user=admin)
        return cliente

    def _accounts(self, cliente, admin, redes):
        result = []
        for red in redes:
            data = ACCOUNTS[red]
            cuenta = CuentaSocial.objects.filter(cliente=cliente, red=red).first()
            if cuenta is None:
                cuenta = clients.link_account(cliente, red=red, acting_user=admin, **data)
            else:
                cuenta = clients.update_account(
                    cuenta,
                    acting_user=admin,
                    estado=CuentaSocial.Estado.VINCULADA,
                    **data,
                )
            result.append(cuenta)
        return result

    def _upload(self, path: Path) -> SimpleUploadedFile:
        if not path.is_file():
            raise CommandError(f"No existe la imagen: {path}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return SimpleUploadedFile(path.name, path.read_bytes(), content_type=content_type)

    def _prepare(self, redes, imagen: Path, minutes: int):
        admin = self._admin()
        cliente = self._client(admin)
        self._accounts(cliente, admin, redes)
        pieza = content.create_piece(
            cliente=cliente,
            tipo="POST",
            acting_user=admin,
            redes=[{"red": red} for red in redes],
            programada_para=timezone.now() + timedelta(minutes=minutes),
            copy_comun=COPY,
            titulo="Smoke test de publicacion automatica",
        )
        content.add_file(pieza, self._upload(imagen), acting_user=admin)
        pieza = content.submit_for_review(pieza, acting_user=admin)
        pieza = content.approve(pieza, acting_user=admin)
        pieza = publishing.schedule_piece(pieza, acting_user=admin, confirmed=True)
        publishing.process_due_piece(pieza, pieza.programada_para)
        tasks = list(TareaPublicacion.objects.filter(pieza_id=pieza.pk).order_by("red"))
        if len(tasks) != len(redes):
            raise CommandError("No se generaron las tareas esperadas para la publicacion de humo.")
        return pieza, tasks

    @staticmethod
    def _validate_public_url() -> None:
        host = (urlsplit(settings.PUBLIC_BASE_URL).hostname or "").lower()
        try:
            loopback = bool(host) and ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not host or host == "localhost" or host.endswith(".localhost") or loopback:
            raise CommandError(
                "PUBLIC_BASE_URL debe ser una URL publica, no localhost ni una direccion loopback."
            )

    def handle(self, *args, **options):
        if options["en_minutos"] < 1:
            raise CommandError("--en-minutos debe ser mayor que cero.")
        redes = (
            [Network.INSTAGRAM, Network.FACEBOOK]
            if options["red"] == "ambas"
            else [Network(options["red"].upper())]
        )
        if settings.META_PUBLISHING != "graph":
            raise CommandError(
                "META_PUBLISHING debe ser 'graph' para la prueba real; usa --fake para pruebas."
            )
        if not options["fake"] and not settings.META_SYSTEM_USER_TOKEN:
            raise CommandError("Falta META_SYSTEM_USER_TOKEN para la prueba real.")
        previous_backend = settings.META_PUBLISHING
        try:
            if options["fake"]:
                settings.META_PUBLISHING = "fake"
            self._validate_public_url()
            try:
                with transaction.atomic():
                    pieza, tasks = self._prepare(redes, Path(options["imagen"]), options["en_minutos"])
            except DomainError as exc:
                raise CommandError(f"No se pudo preparar la prueba: {exc}") from exc
        finally:
            if options["fake"]:
                settings.META_PUBLISHING = previous_backend

        self.stdout.write(self.style.SUCCESS(f"Pieza: {pieza.pk}"))
        self.stdout.write(f"Programada para: {pieza.programada_para.isoformat()}")
        for task in tasks:
            self.stdout.write(f"Tarea {task.red}: {task.pk} ({task.estado}, {task.modo})")
