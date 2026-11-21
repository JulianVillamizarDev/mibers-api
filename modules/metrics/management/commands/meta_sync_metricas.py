"""Sincroniza manualmente las metricas mensuales desde Meta."""

from datetime import date

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from modules.clients.models import Cliente
from modules.core.exceptions import DomainError
from modules.metrics import services


class Command(BaseCommand):
    help = "Sincroniza las metricas de un mes desde Meta (IG/FB)."

    def add_arguments(self, parser):
        parser.add_argument("--mes", help="Mes AAAA-MM (por defecto, el actual)")
        parser.add_argument("--cliente", help="UUID del cliente")
        parser.add_argument("--forzar", action="store_true", help="Sobrescribe tambien filas MANUAL")

    def _month(self, value):
        if not value:
            return None
        try:
            year, month = (int(part) for part in value.split("-", 1))
            return date(year, month, 1)
        except (TypeError, ValueError) as exc:
            raise CommandError("Usa --mes con formato AAAA-MM.") from exc

    def _cliente(self, value):
        if not value:
            return None
        try:
            cliente = Cliente.objects.filter(pk=value).first()
        except ValidationError as exc:
            raise CommandError("--cliente debe ser un UUID valido.") from exc
        if cliente is None:
            raise CommandError("No existe el cliente indicado.")
        return cliente

    def handle(self, *args, **options):
        try:
            result = services.sync_month(
                cliente=self._cliente(options["cliente"]),
                mes=self._month(options["mes"]),
                forzar=options["forzar"],
            )
        except DomainError as exc:
            raise CommandError(str(exc)) from exc
        if result["omitida"]:
            self.stdout.write(f"Sincronizacion omitida: {result['omitida']}.")
            return
        self.stdout.write(f"Mes: {result['mes']}")
        self.stdout.write(f"Cuentas revisadas: {result['revisadas']}")
        self.stdout.write(f"Creadas: {result['creadas']}")
        self.stdout.write(f"Actualizadas: {result['actualizadas']}")
        self.stdout.write(f"Omitidas por ser MANUAL: {result['omitidas_manual']}")
        self.stdout.write(f"Errores de metricas: {result['errores']}")
