"""Job de publicacion automatica en Meta (PLN0001 T2).

El tick solo encola. Este modulo ejecuta la publicacion por pasos, reutiliza contenedores
y publicaciones externas para no duplicar, y re-encola la espera de videos sin dormir.
"""

import json
import logging
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from modules.clients import services as client_services
from modules.clients.meta import GraphError, get_graph_client, page_token
from modules.clients.models import CuentaSocial
from modules.content.models import Tipo
from modules.core import jobs as job_queue
from modules.core.exceptions import DomainError
from modules.core.networks import Network

from . import services
from .models import TareaPublicacion
from .public_media import build_public_media_url
from .publishers import (
    GRAPH_JOB_KIND,
    automatic_available,
    fallback_to_manual,
    supports_task,
)

logger = logging.getLogger(__name__)

JOB_KIND = GRAPH_JOB_KIND
VIDEO_WAIT_SECONDS = 15
MAX_VIDEO_WAITS = 40
CHILDREN_PREFIX = "hijos:"


class _Deferred(Exception):
    """El job ya se re-encolo; no es un fallo de la publicacion."""


def _load(tarea_id) -> TareaPublicacion:
    return (
        TareaPublicacion.objects.select_related("pieza__cliente", "cuenta")
        .prefetch_related("pieza__archivos")
        .get(pk=tarea_id)
    )


def _state(tarea: TareaPublicacion, value: str) -> None:
    tarea.externo_estado = value
    tarea.save(update_fields=["externo_estado", "updated_at"])


def _required_id(payload: dict, what: str) -> str:
    value = str(payload.get("id") or "")
    if not value:
        raise GraphError(f"Meta no devolvio el id de {what}.", retryable=False)
    return value


def _account_problem(tarea: TareaPublicacion) -> str:
    cuenta = tarea.cuenta
    if cuenta.cliente_id != tarea.pieza.cliente_id:
        return f"la cuenta {cuenta.handle} no pertenece al cliente de la pieza"
    if cuenta.red != tarea.red:
        return f"la cuenta {cuenta.handle} no es de la red {tarea.get_red_display()}"
    if cuenta.estado != CuentaSocial.Estado.VINCULADA:
        return f"la cuenta {cuenta.handle} esta {cuenta.get_estado_display().lower()}"
    return ""


def _failure_reason(tarea: TareaPublicacion, error: GraphError) -> str:
    label = tarea.get_red_display()
    code = f" (codigo {error.code})" if error.code is not None else ""
    return f"No se pudo publicar en {label}{code}: {error}"


def _fail(tarea: TareaPublicacion, motivo: str) -> None:
    if tarea.estado != TareaPublicacion.Estado.PENDIENTE:
        return
    tarea.modo = TareaPublicacion.Modo.AUTOMATICA
    tarea.externo_estado = "FALLIDO"
    tarea.save(update_fields=["modo", "externo_estado", "updated_at"])
    services.report_failed(
        tarea,
        motivo=motivo,
        acting_user=None,
        actor_username="sistema",
    )


def _requeue(tarea: TareaPublicacion, waits: int) -> None:
    if waits >= MAX_VIDEO_WAITS:
        raise GraphError(
            "El contenido no termino de procesarse en Meta dentro del plazo de 10 minutos.",
            retryable=False,
        )
    _state(tarea, "PROCESANDO")
    job_queue.enqueue(
        JOB_KIND,
        {"tarea_id": str(tarea.pk), "esperas": waits + 1},
        run_at=timezone.now() + timedelta(seconds=VIDEO_WAIT_SECONDS),
        dedupe_key=f"graph:{tarea.pk}:video:{waits + 1}",
    )
    raise _Deferred


def _wait_for_containers(tarea: TareaPublicacion, client, container_ids: list[str], waits: int) -> None:
    for container_id in container_ids:
        data = client.get(f"/{container_id}", {"fields": "status_code"})
        status = data.get("status_code")
        if status == "FINISHED":
            continue
        if status in {"ERROR", "EXPIRED"}:
            raise GraphError(f"Meta no pudo procesar el contenido (estado {status}).", retryable=False)
        _requeue(tarea, waits)


def _finish(tarea: TareaPublicacion, client, *, token: str | None = None) -> None:
    if not tarea.externo_post_id:
        raise GraphError("Meta no devolvio el id de la publicacion.", retryable=False)
    field = "permalink" if tarea.red == Network.INSTAGRAM else "permalink_url"
    if not tarea.enlace:
        data = client.get(f"/{tarea.externo_post_id}", {"fields": field}, token=token)
        enlace = str(data.get(field) or "")
        if not enlace:
            raise GraphError("Meta publico el contenido pero no devolvio el enlace.", retryable=False)
    else:
        enlace = tarea.enlace
    tarea.externo_estado = "PUBLICADO"
    services.confirm_published(
        tarea,
        enlace=enlace,
        acting_user=None,
        actor_username="sistema",
    )


def _ig_container_data(tarea: TareaPublicacion, archivo) -> dict:
    url = build_public_media_url(archivo)
    if archivo.kind == "video":
        media_type = "STORIES" if tarea.pieza.tipo == Tipo.HISTORIA else "REELS"
        data = {"video_url": url, "media_type": media_type}
    else:
        data = {"image_url": url}
        if tarea.pieza.tipo == Tipo.HISTORIA:
            data["media_type"] = "STORIES"
    if tarea.pieza.tipo != Tipo.HISTORIA:
        data["caption"] = tarea.copy_final
    return data


def _ig_single_container(tarea: TareaPublicacion, client, data: dict) -> str:
    if tarea.externo_contenedor_id:
        return tarea.externo_contenedor_id
    _state(tarea, "CREANDO")
    payload = client.post(f"/{tarea.cuenta.id_externo}/media", data)
    container_id = _required_id(payload, "contenedor de Instagram")
    tarea.externo_contenedor_id = container_id
    tarea.save(update_fields=["externo_contenedor_id", "updated_at"])
    return container_id


def _ig_carousel_container(tarea: TareaPublicacion, client, archivos: list, waits: int) -> str:
    raw = tarea.externo_contenedor_id
    if raw and not raw.startswith(CHILDREN_PREFIX):
        return raw

    children = raw.removeprefix(CHILDREN_PREFIX).split(",") if raw else []
    children = [child for child in children if child]
    if len(children) > len(archivos):
        raise GraphError("Los contenedores guardados no coinciden con los archivos.", retryable=False)

    _state(tarea, "CREANDO")
    for archivo in archivos[len(children) :]:
        data = _ig_container_data(tarea, archivo)
        data.pop("caption", None)
        data["is_carousel_item"] = "true"
        payload = client.post(f"/{tarea.cuenta.id_externo}/media", data)
        children.append(_required_id(payload, "elemento del carrusel"))
        tarea.externo_contenedor_id = CHILDREN_PREFIX + ",".join(children)
        tarea.save(update_fields=["externo_contenedor_id", "updated_at"])

    if len(children) != len(archivos):
        raise GraphError("Meta no devolvio todos los elementos del carrusel.", retryable=False)

    video_children = [
        child_id for child_id, archivo in zip(children, archivos, strict=True) if archivo.kind == "video"
    ]
    if video_children:
        _wait_for_containers(tarea, client, video_children, waits)

    _state(tarea, "PUBLICANDO")
    payload = client.post(
        f"/{tarea.cuenta.id_externo}/media",
        {
            "media_type": "CAROUSEL",
            "children": json.dumps(children, separators=(",", ":")),
            "caption": tarea.copy_final,
        },
    )
    container_id = _required_id(payload, "carrusel de Instagram")
    tarea.externo_contenedor_id = container_id
    tarea.save(update_fields=["externo_contenedor_id", "updated_at"])
    return container_id


def _publish_instagram(tarea: TareaPublicacion, client, waits: int) -> None:
    if tarea.externo_post_id:
        _finish(tarea, client)
        return

    archivos = list(tarea.pieza.archivos.all())
    if tarea.pieza.tipo == Tipo.CARRUSEL:
        container_id = _ig_carousel_container(tarea, client, archivos, waits)
    else:
        data = _ig_container_data(tarea, archivos[0])
        container_id = _ig_single_container(tarea, client, data)
    _wait_for_containers(tarea, client, [container_id], waits)

    _state(tarea, "PUBLICANDO")
    payload = client.post(f"/{tarea.cuenta.id_externo}/media_publish", {"creation_id": container_id})
    post_id = _required_id(payload, "publicacion de Instagram")
    tarea.externo_post_id = post_id
    tarea.save(update_fields=["externo_post_id", "updated_at"])
    _finish(tarea, client)










def run_graph_publish(
    tarea_id,
    *,
    last_attempt: bool = False,
    waits: int = 0,
) -> None:
    tarea = _load(tarea_id)
    if tarea.estado != TareaPublicacion.Estado.PENDIENTE:
        return

    if tarea.modo != TareaPublicacion.Modo.AUTOMATICA:
        tarea.modo = TareaPublicacion.Modo.AUTOMATICA
        tarea.save(update_fields=["modo", "updated_at"])

    problem = _account_problem(tarea)
    if problem:
        _fail(tarea, f"No se publico: {problem}.")
        return

    if not automatic_available(tarea.cuenta) or not tarea.cuenta.id_externo or not supports_task(tarea):
        fallback_to_manual(tarea)
        return

    try:
        client = get_graph_client()
    except ImproperlyConfigured:
        fallback_to_manual(tarea)
        return

    try:
        if tarea.red == Network.INSTAGRAM:
            _publish_instagram(tarea, client, waits)
        else:
            _publish_facebook(tarea, client, waits)
    except _Deferred:
        return
    except GraphError as exc:
        if exc.code == 190:
            try:
                client_services.verify_meta_accounts()
            except Exception:  # noqa: BLE001 - la verificacion no debe ocultar el fallo original
                logger.exception("Fallo la verificacion de cuentas tras un token invalido")
        if exc.retryable and not last_attempt:
            _state(tarea, "REINTENTANDO")
            raise
        _fail(tarea, _failure_reason(tarea, exc))
    except DomainError as exc:
        _fail(tarea, f"No se pudo confirmar la publicacion en {tarea.get_red_display()}: {exc}")
    except Exception:
        if not last_attempt:
            raise
        logger.exception("Fallo definitivo del job de Meta para la tarea %s", tarea.pk)
        _fail(tarea, f"No se pudo publicar en {tarea.get_red_display()}: error inesperado.")


@job_queue.register(JOB_KIND)
def _graph_publish_job(payload, *, last_attempt):
    run_graph_publish(
        payload["tarea_id"],
        last_attempt=last_attempt,
        waits=int(payload.get("esperas", 0)),
    )
