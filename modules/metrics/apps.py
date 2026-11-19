from django.apps import AppConfig


class MetricsConfig(AppConfig):
    name = "modules.metrics"
    label = "metrics"

    def ready(self):
        from modules.core import jobs

        from . import services

        jobs.periodic("metrics.sincronizar_meta", every=24 * 3600)(services.sync_recent_months)
