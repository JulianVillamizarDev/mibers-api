"""Valida la configuracion de WhatsApp y opcionalmente envia una plantilla de prueba."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from modules.notifications.gateway import get_gateway


class Command(BaseCommand):
    help = "Valida la configuracion de WhatsApp Cloud API y opcionalmente envia una plantilla de prueba."

    def add_arguments(self, parser):
        parser.add_argument("--enviar-a", default="", help="Numero E.164 de prueba")
        parser.add_argument("--plantilla", default="hello_world", help="Plantilla aprobada a enviar")

    def handle(self, *args, **options):
        if settings.WHATSAPP_BACKEND != "cloud":
            raise CommandError("WHATSAPP_BACKEND debe ser 'cloud' para verificar WhatsApp.")
        try:
            client = get_gateway()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

        if options["enviar_a"]:
            result = client.send_template(
                to=options["enviar_a"],
                template=options["plantilla"],
                body_params=[],
            )
            self.stdout.write(self.style.SUCCESS(f"Mensaje enviado: {result.message_id}"))
            return

        info = client.get_phone_info()
        nombre = info.get("verified_name", "-")
        numero = info.get("display_phone_number", "-")
        self.stdout.write(self.style.SUCCESS(f"WhatsApp verificado: {nombre} ({numero})"))
