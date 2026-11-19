"""Verifica contra Meta el estado de las cuentas vinculadas (HU-08)."""

from django.core.management.base import BaseCommand

from modules.clients import services


class Command(BaseCommand):
    help = "Verifica las cuentas de Instagram y Facebook contra Meta y actualiza su estado."

    def handle(self, *args, **options):
        result = services.verify_meta_accounts()
        if result["omitida"]:
            self.stdout.write(f"Verificacion omitida: {result['omitida']}.")
            return
        self.stdout.write(f"Cuentas revisadas: {result['revisadas']}")
        self.stdout.write(f"Vinculadas recuperadas: {result['vinculadas']}")
        self.stdout.write(f"Expiradas: {result['expiradas']}")
        self.stdout.write(f"Desvinculadas: {result['desvinculadas']}")
        self.stdout.write(f"Sin cambios: {result['sin_cambios']}")
