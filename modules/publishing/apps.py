from django.apps import AppConfig


class PublishingConfig(AppConfig):
    name = "modules.publishing"
    label = "publishing"

    def ready(self):
        from . import handlers  # noqa: F401 - conecta senales y registra el tick del scheduler
