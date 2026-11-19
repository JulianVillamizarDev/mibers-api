"""Puerto de publicacion por red.

Hoy solo existe `ManualPublisher` (publicacion manual asistida), porque publicar en cuentas de
clientes por la Graph API exige ser Tech Provider de Meta y la agencia aun no lo es. Si algun dia
se obtiene, basta con anadir un `GraphApiPublisher` con el mismo contrato y elegirlo en `get_publisher`
sin tocar el resto del dominio (el scheduler, las tareas ni los estados).
"""

from typing import Protocol

from modules.notifications import services as notifications

from .models import TareaPublicacion


class Publisher(Protocol):
    def dispatch(self, tarea: TareaPublicacion) -> None:
        """Se llama cuando llega la hora de publicar. Debe dejar la tarea lista para completarse:
        el publicador automatico publicaria aqui; el manual avisa a quien debe hacerlo."""


class ManualPublisher:
    def dispatch(self, tarea: TareaPublicacion) -> None:
        pieza = tarea.pieza
        notifications.send_staff_reminder(
            pieza.responsable,
            f"Hora de publicar {pieza.get_tipo_display().lower()} de {pieza.cliente.nombre} "
            f"en {tarea.get_red_display()} ({tarea.cuenta_handle}).",
            entity_type="publishing.tareapublicacion",
            entity_id=tarea.pk,
        )


def get_publisher(red: str) -> Publisher:
    return ManualPublisher()
