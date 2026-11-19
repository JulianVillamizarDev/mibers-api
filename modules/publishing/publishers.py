"""Puerto de publicacion por red (RF17, ADR0007).

El publicador automatico solo encola un `Job`; la llamada a Meta ocurre en el worker.
Si faltan credenciales, la cuenta no tiene id externo o el tipo de pieza no esta soportado,
la tarea vuelve al flujo manual asistido.
"""

from typing import Protocol

from django.conf import settings

from modules.content.models import Tipo
from modules.core import jobs
from modules.core.networks import Network
from modules.notifications import services as notifications

from .models import TareaPublicacion

GRAPH_JOB_KIND = "publishing.graph_publish"


class Publisher(Protocol):
    def dispatch(self, tarea: TareaPublicacion) -> None:
        """Deja la tarea lista para completarse (aviso manual o job automatico)."""


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


def supports(red: str, tipo: str, kinds: list[str]) -> bool:
    """Tipos que el publicador automatico puede manejar en cada red (§4.4 del plan)."""
    if red == Network.INSTAGRAM:
        if tipo == Tipo.POST:
            return len(kinds) == 1 and kinds[0] in {"image", "video"}
        if tipo == Tipo.CARRUSEL:
            return 2 <= len(kinds) <= 10 and all(kind in {"image", "video"} for kind in kinds)
        if tipo == Tipo.REEL:
            return len(kinds) == 1 and kinds[0] == "video"
        if tipo == Tipo.HISTORIA:
            return len(kinds) == 1 and kinds[0] in {"image", "video"}
        return False  # PORTADA
    if red == Network.FACEBOOK:
        if tipo == Tipo.POST:
            return len(kinds) == 1 and kinds[0] in {"image", "video"}
        if tipo == Tipo.CARRUSEL:
            return 2 <= len(kinds) <= 10 and all(kind == "image" for kind in kinds)
        if tipo == Tipo.REEL:
            return len(kinds) == 1 and kinds[0] == "video"
        return False  # HISTORIA y PORTADA
    return False


def supports_task(tarea: TareaPublicacion) -> bool:
    return supports(
        tarea.red,
        tarea.pieza.tipo,
        [archivo.kind for archivo in tarea.pieza.archivos.all()],
    )


def _mark(tarea: TareaPublicacion, modo: str, estado: str) -> None:
    tarea.modo = modo
    tarea.externo_estado = estado
    tarea.save(update_fields=["modo", "externo_estado", "updated_at"])


def fallback_to_manual(tarea: TareaPublicacion) -> None:
    """La tarea no se puede automatizar: se avisa al equipo sin cambiar su estado."""
    _mark(tarea, TareaPublicacion.Modo.MANUAL, "MANUAL")
    ManualPublisher().dispatch(tarea)


def automatic_available(cuenta) -> bool:
    backend = settings.META_PUBLISHING
    if backend == "fake":
        return True
    return backend == "graph" and bool(settings.META_SYSTEM_USER_TOKEN)


class GraphApiPublisher:
    def dispatch(self, tarea: TareaPublicacion) -> None:
        if tarea.estado != TareaPublicacion.Estado.PENDIENTE:
            return
        if not supports_task(tarea):
            fallback_to_manual(tarea)
            return
        _mark(tarea, TareaPublicacion.Modo.AUTOMATICA, "ENCOLADO")
        jobs.enqueue(
            GRAPH_JOB_KIND,
            {"tarea_id": str(tarea.pk)},
            dedupe_key=f"graph:{tarea.pk}:inicio",
        )


def get_publisher(red: str, cuenta) -> Publisher:
    if (
        automatic_available(cuenta)
        and cuenta.red == red
        and cuenta.id_externo
        and cuenta.estado == cuenta.Estado.VINCULADA
    ):
        return GraphApiPublisher()
    return ManualPublisher()
