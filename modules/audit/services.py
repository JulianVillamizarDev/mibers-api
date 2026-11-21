from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from .models import Action, AuditLog

RETENTION_MONTHS = 12


def client_ip(request) -> str | None:
    """IP de origen. Detras del proxy de Render la primera entrada de X-Forwarded-For es el cliente."""
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return ip or None


def record(
    action: Action | str,
    *,
    actor=None,
    actor_username: str = "",
    entity=None,
    entity_type: str = "",
    entity_id="",
    metadata: dict | None = None,
    request=None,
) -> AuditLog:
    """Registra una accion (RF4). `entity` puede ser una instancia de modelo."""
    if entity is not None:
        entity_type = entity_type or entity._meta.label_lower
        entity_id = entity_id or entity.pk
    if actor is not None and not getattr(actor, "is_authenticated", True):
        actor = None
    if actor is not None and not actor_username:
        actor_username = actor.get_username()
    return AuditLog.objects.create(
        action=action,
        actor=actor,
        actor_username=actor_username,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else "",
        metadata=metadata or {},
        ip_address=client_ip(request),
    )


def purge_expired(*, now=None) -> int:
    """Retencion de 12 meses (RNF11). Unico camino autorizado para borrar registros."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=RETENTION_MONTHS * 30)
    with transaction.atomic(), connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            # Habilita el DELETE en el trigger solo dentro de esta transaccion.
            cursor.execute("SET LOCAL mibers.audit_purge = 'on'")
        table = AuditLog._meta.db_table
        cursor.execute(f"DELETE FROM {table} WHERE created_at < %s", [cutoff])  # noqa: S608
        return cursor.rowcount
