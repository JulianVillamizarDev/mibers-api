from django.apps import AppConfig


class ClientsConfig(AppConfig):
    name = "modules.clients"
    label = "clients"

    def ready(self):
        from modules.core import jobs

        from . import services

        # Retencion de un anio para clientes desactivados (RF5), una vez al dia.
        jobs.periodic("clients.purge_expired", every=24 * 3600)(services.purge_expired_clients)
