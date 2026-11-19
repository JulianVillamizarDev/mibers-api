import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from modules.core.exceptions import DomainError


class AuditImmutableError(DomainError):
    def __init__(self):
        super().__init__("El historial de acciones es de solo lectura.", "audit_immutable", 403)


class Action(models.TextChoices):
    """Catalogo de acciones registrables (RF4)."""

    # Sesion y cuentas
    LOGIN = "LOGIN", "Inicio de sesion"
    LOGIN_FAILED = "LOGIN_FAILED", "Inicio de sesion fallido"
    LOGOUT = "LOGOUT", "Cierre de sesion"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED", "Cuenta bloqueada"
    PASSWORD_CHANGED = "PASSWORD_CHANGED", "Cambio de contrasena"
    PASSWORD_RESET = "PASSWORD_RESET", "Restablecimiento de contrasena"
    USER_CREATED = "USER_CREATED", "Cuenta creada"
    USER_UPDATED = "USER_UPDATED", "Cuenta modificada"
    USER_DEACTIVATED = "USER_DEACTIVATED", "Cuenta desactivada"
    USER_ACTIVATED = "USER_ACTIVATED", "Cuenta reactivada"
    # Clientes y redes
    CLIENT_CREATED = "CLIENT_CREATED", "Cliente creado"
    CLIENT_UPDATED = "CLIENT_UPDATED", "Cliente modificado"
    CLIENT_DEACTIVATED = "CLIENT_DEACTIVATED", "Cliente desactivado"
    CLIENT_ACTIVATED = "CLIENT_ACTIVATED", "Cliente reactivado"
    CONTACT_CHANGED = "CONTACT_CHANGED", "Contacto de WhatsApp modificado"
    NETWORK_LINKED = "NETWORK_LINKED", "Red social vinculada"
    NETWORK_STATUS_CHANGED = "NETWORK_STATUS_CHANGED", "Estado de red social modificado"
    # Piezas
    PIECE_CREATED = "PIECE_CREATED", "Pieza creada"
    PIECE_UPDATED = "PIECE_UPDATED", "Pieza modificada"
    PIECE_SUBMITTED = "PIECE_SUBMITTED", "Pieza enviada a revision"
    PIECE_APPROVED = "PIECE_APPROVED", "Pieza aprobada"
    PIECE_CHANGES_REQUESTED = "PIECE_CHANGES_REQUESTED", "Cambios solicitados en pieza"
    PIECE_RETURNED_TO_REVIEW = "PIECE_RETURNED_TO_REVIEW", "Pieza devuelta a revision por edicion"
    PIECE_SCHEDULED = "PIECE_SCHEDULED", "Pieza programada"
    PIECE_UNSCHEDULED = "PIECE_UNSCHEDULED", "Programacion cancelada"
    # Publicacion y envios
    PUBLICATION_BLOCKED = "PUBLICATION_BLOCKED", "Publicacion bloqueada"
    PUBLICATION_DUE = "PUBLICATION_DUE", "Publicacion pendiente de realizar"
    PUBLICATION_PUBLISHED = "PUBLICATION_PUBLISHED", "Publicacion confirmada"
    PUBLICATION_FAILED = "PUBLICATION_FAILED", "Publicacion fallida"
    PUBLICATION_RETRIED = "PUBLICATION_RETRIED", "Publicacion reintentada"
    WHATSAPP_SENT = "WHATSAPP_SENT", "Envio por WhatsApp"
    WHATSAPP_FAILED = "WHATSAPP_FAILED", "Envio por WhatsApp fallido"
    WHATSAPP_DELIVERY = "WHATSAPP_DELIVERY", "Estado de entrega de WhatsApp"
    ALERT_SENT = "ALERT_SENT", "Alerta enviada al administrador"
    # Metricas
    METRICS_CHANGED = "METRICS_CHANGED", "Metricas registradas"
    METRICS_SYNCED = "METRICS_SYNCED", "Metricas sincronizadas desde Meta"


class AuditQuerySet(models.QuerySet):
    """Bloquea modificaciones masivas desde el ORM (RNF11)."""

    def update(self, **kwargs):
        raise AuditImmutableError()

    def delete(self):
        raise AuditImmutableError()


class AuditLog(models.Model):
    """Registro de solo anexar: usuario, accion, fecha y hora (RF4).

    La inmutabilidad se aplica en tres niveles: este modelo, el QuerySet y, en PostgreSQL,
    un trigger (ver migracion 0002). Solo el job de retencion puede borrar, y unicamente
    registros con mas de 12 meses.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    # PROTECT: las cuentas se desactivan, nunca se borran, para no perder historial.
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    # Copia del nombre de usuario: sirve tambien para intentos de ingreso con usuarios inexistentes.
    actor_username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    entity_type = models.CharField(max_length=60, blank=True)
    entity_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_username or '-'} {self.action}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AuditImmutableError()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AuditImmutableError()
