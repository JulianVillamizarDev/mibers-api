"""Verifica el token del usuario del sistema de Meta y descubre paginas e Instagram."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from modules.publishing.graph import FakeGraphClient, GraphClient, GraphError

FAKE_ME = {"id": "61596391660589", "name": "mibers-media-api"}
FAKE_ACCOUNTS = {
    "data": [
        {"id": "158691610656662", "name": "Mibers Creativos"},
        {"id": "1261870047327169", "name": "Whittingham Abogados"},
    ]
}
FAKE_PAGES = {
    "158691610656662": {
        "id": "158691610656662",
        "name": "Mibers Creativos",
        "instagram_business_account": {"id": "17841480467776205", "username": "mibers.creativos"},
    },
    "1261870047327169": {
        "id": "1261870047327169",
        "name": "Whittingham Abogados",
        "instagram_business_account": {"id": "17841401360959921", "username": "whittingham.abogados"},
    },
}


class Command(BaseCommand):
    help = "Comprueba META_SYSTEM_USER_TOKEN y lista paginas e Instagram con sus IDs, sin imprimir el token."

    def add_arguments(self, parser):
        parser.add_argument("--fake", action="store_true", help="Usa respuestas simuladas, sin llamar a Meta")

    def _client(self, fake: bool):
        if fake:
            return FakeGraphClient([FAKE_ME, FAKE_ACCOUNTS, *FAKE_PAGES.values()])
        if not settings.META_SYSTEM_USER_TOKEN:
            raise CommandError("Falta META_SYSTEM_USER_TOKEN para consultar Meta.")
        return GraphClient(
            token=settings.META_SYSTEM_USER_TOKEN,
            app_secret=settings.META_APP_SECRET,
            version=settings.META_GRAPH_VERSION,
        )

    def _table(self, headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
        widths = [len(header) for header in headers]
        for row in rows:
            for index, value in enumerate(row):
                widths[index] = max(widths[index], len(value))
        line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
        self.stdout.write(line)
        self.stdout.write("  ".join("-" * width for width in widths))
        for row in rows:
            self.stdout.write("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))

    def handle(self, *args, **options):
        client = self._client(options["fake"])
        try:
            me = client.get("/me")
            pages = client.get("/me/accounts").get("data", [])
            rows = []
            for page in pages:
                page_id = str(page.get("id") or "")
                details = client.get(
                    f"/{page_id}",
                    {"fields": "name,instagram_business_account{id,username}"},
                )
                instagram = details.get("instagram_business_account") or {}
                rows.append(
                    (
                        str(details.get("name") or page.get("name") or "-"),
                        page_id or "-",
                        str(instagram.get("username") or "-"),
                        str(instagram.get("id") or "-"),
                    )
                )
        except GraphError as exc:
            raise CommandError(f"No se pudo consultar Meta: {exc}") from exc
        finally:
            client.close()

        self.stdout.write("Usuario del sistema")
        self._table(("NOMBRE", "ID"), [(str(me.get("name") or "-"), str(me.get("id") or "-"))])
        self.stdout.write("")
        self.stdout.write("Paginas e Instagram")
        self._table(("PAGINA", "PAGE_ID", "INSTAGRAM", "IG_ID"), rows)
