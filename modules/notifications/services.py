import logging
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.dispatch import Signal
from django.utils import timezone

from modules.audit import services as audit
from modules.audit.models import Action
from modules.core import jobs
from modules.core.roles import Role

from . import gateway
from .models import Alerta, EnvioWhatsApp

logger = logging.getLogger(__name__)

# Se emite cuando cambia el estado de un envio. kwargs: envio. Lo escucha publishing
# para marcar la pieza como Compartida (RF18).
envio_actualizado = Signal()

JOB_KIND = "whatsapp.send"
Estado = EnvioWhatsApp.Estado
_ORDER = {Estado.PENDIENTE: 0, Estado.ENVIADO: 1, Estado.ENTREGADO: 2, Estado.LEIDO: 3}


def _system_audit(action, envio, **metadata):
    audit.record(
        action,
        actor_username="sistema",
        entity=envio,
        metadata={
            "proposito": envio.proposito,
            "entity": f"{envio.entity_type}:{envio.entity_id}",
            **metadata,
        },
    )


# --- Envio ------------------------------------------------------------------------------------
def queue_message(
    *,
    proposito,
    destino,
    plantilla="",
    parametros=None,
    destino_nombre="",
    media_ruta="",
    media_nombre="",
    media_kind="",
    entity_type="",
    entity_id="",
    contexto=None,
    run_at=None,
) -> EnvioWhatsApp:
    """Registra el mensaje y encola su envio. El worker lo envia en cuanto llegue `run_at`."""
    envio = EnvioWhatsApp.objects.create(
        proposito=proposito,
        destino=destino,
        destino_nombre=destino_nombre,
        plantilla=plantilla,
        parametros=list(parametros or []),
        media_ruta=media_ruta,
        media_nombre=media_nombre,
        media_kind=media_kind,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        contexto=contexto or {},
    )
    jobs.enqueue(JOB_KIND, {"envio_id": str(envio.pk)}, run_at=run_at, dedupe_key=f"wa:{envio.pk}")
    return envio


def _mark_failed(envio: EnvioWhatsApp, error: str) -> None:
    envio.estado = Estado.FALLIDO
    envio.error = error[:2000]
    envio.save(update_fields=["estado", "error", "updated_at"])
    _system_audit(Action.WHATSAPP_FAILED, envio, error=error[:300])
    envio_actualizado.send(sender=EnvioWhatsApp, envio=envio)
    if envio.proposito == EnvioWhatsApp.Proposito.CLIENTE:
        destinatario = envio.destino_nombre or "un contacto del cliente"
        send_alert(
            Alerta.Tipo.ENVIO_CLIENTE_FALLIDO,
            f"No se pudo enviar el contenido a {destinatario}: {error[:200]}",
            entity_type=envio.entity_type,
            entity_id=envio.entity_id,
            dedupe_key=f"envio_fallido:{envio.pk}",
        )


def deliver(envio_id, *, last_attempt: bool) -> None:
    """Envia un mensaje pendiente por el gateway configurado."""
    envio = EnvioWhatsApp.objects.get(pk=envio_id)
    if envio.estado != Estado.PENDIENTE:
        return  # ya procesado (reejecucion del job)
    try:
        header = None
        template = envio.plantilla
        if envio.media_ruta:
            path = Path(settings.MEDIA_ROOT) / envio.media_ruta
            if not path.exists():
                raise gateway.WhatsAppError("El archivo de la pieza ya no existe en el servidor.")
            header = gateway.build_header(
                str(path), envio.media_nombre or path.name, envio.media_kind or "image"
            )
            template = gateway.template_for_header(header.kind)
        result = gateway.get_gateway().send_template(
            to=envio.destino, template=template, body_params=envio.parametros, header=header
        )
    except gateway.WhatsAppError as exc:
        if exc.retryable and not last_attempt:
            raise  # el job se reintenta con backoff
        _mark_failed(envio, str(exc))
        return
    envio.estado = Estado.ENVIADO
    envio.wa_message_id = result.message_id
    envio.plantilla = template
    envio.enviado_at = timezone.now()
    envio.error = ""
    envio.save(update_fields=["estado", "wa_message_id", "plantilla", "enviado_at", "error", "updated_at"])
    _system_audit(Action.WHATSAPP_SENT, envio, wa_message_id=result.message_id)
    envio_actualizado.send(sender=EnvioWhatsApp, envio=envio)


@jobs.register(JOB_KIND)
def _send_job(payload, *, last_attempt):
    deliver(payload["envio_id"], last_attempt=last_attempt)


# --- Estado de entrega (webhook) ---------------------------------------------------------------------
_STATUS_MAP = {
    "sent": Estado.ENVIADO,
    "delivered": Estado.ENTREGADO,
    "read": Estado.LEIDO,
    "failed": Estado.FALLIDO,
}


def apply_status_update(wa_message_id: str, status: str, error: str = "") -> bool:
    """Aplica un estado de entrega de WhatsApp (RF18 'registrando si se entrego'). Los estados
    solo avanzan; un fallo posterior al envio se marca y avisa al administrador."""
    target = _STATUS_MAP.get(status)
    envio = EnvioWhatsApp.objects.filter(wa_message_id=wa_message_id).first() if target else None
    if envio is None:
        return False
    if target == Estado.FALLIDO:
        if envio.estado in (Estado.ENTREGADO, Estado.LEIDO, Estado.FALLIDO):
            return False
        _mark_failed(envio, error or "WhatsApp no pudo entregar el mensaje.")
        return True
    if envio.estado == Estado.FALLIDO or _ORDER[target] <= _ORDER[envio.estado]:
        return False
    envio.estado = target
    if target == Estado.ENTREGADO:
        envio.entregado_at = timezone.now()
    envio.save(update_fields=["estado", "entregado_at", "updated_at"])
    _system_audit(Action.WHATSAPP_DELIVERY, envio, estado=target)
    envio_actualizado.send(sender=EnvioWhatsApp, envio=envio)
    return True


# --- Alertas al administrador (RF19, RNF12) -------------------------------------------------------------




