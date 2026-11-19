import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from modules.accounts import services
from modules.core.roles import Role


class Command(BaseCommand):
    help = "Crea el primer administrador (no hay registro publico). Sin --password genera una temporal."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        if User.objects.filter(username=username).exists():
            raise CommandError(f"El usuario '{username}' ya existe.")
        user, temporary = services.create_user(
            username=username,
            role=Role.ADMIN,
            first_name=options["first_name"],
            last_name=options["last_name"],
        )
        if options["password"]:
            user.set_password(options["password"])
            user.must_change_password = False
            user.save(update_fields=["password", "must_change_password"])
            self.stdout.write(self.style.SUCCESS(f"Administrador '{username}' creado."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Administrador '{username}' creado."))
            self.stdout.write(f"Contrasena temporal (debe cambiarse al ingresar): {temporary}")
