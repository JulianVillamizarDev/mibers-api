"""Conexiones entre modulos: senales del dominio y la tarea periodica del scheduler."""

from django.dispatch import receiver

from modules.content.models import Estado, Pieza
from modules.content.signals import piece_state_changed
from modules.core import jobs
from modules.notifications.models import EnvioWhatsApp
from modules.notifications.services import envio_actualizado

from . import services

TICK_SECONDS = 60


@receiver(piece_state_changed, sender=Pieza)
def cancel_tasks_when_piece_leaves_schedule(sender, pieza, old_state, new_state, **kwargs):
    """Si una pieza programada vuelve a revision o se des-programa, sus tareas sin resultado
    se cancelan para que nada no aprobado llegue a publicarse (RF12, RF15)."""
    if old_state in (Estado.PROGRAMADA, Estado.FALLIDA) and new_state in (
        Estado.EN_REVISION,
        Estado.APROBADA,
    ):
        services.cancel_pending_tasks(pieza)


@receiver(envio_actualizado, sender=EnvioWhatsApp)
def share_state_follows_deliveries(sender, envio, **kwargs):
    services.refresh_shared_state(envio)


@jobs.periodic("publishing.tick", every=TICK_SECONDS)
def scheduler_tick():
    services.run_tick()
