from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "modules.audit"
    label = "audit"

    def ready(self):
        from modules.core import jobs

        from . import services

        # Retencion de 12 meses del historial (RNF11), una vez al dia.
        jobs.periodic("audit.purge_expired", every=24 * 3600)(services.purge_expired)
