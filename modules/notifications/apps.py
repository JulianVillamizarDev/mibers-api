from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "modules.notifications"
    label = "notifications"

    def ready(self):
        from . import services  # noqa: F401 - registra el manejador del job de envio
