from django.db import models

from modules.core.fields import EncryptedTextField
from modules.core.models import BaseModel


class EnvioWhatsApp(BaseModel):
    """Mensaje de WhatsApp saliente y su estado de entrega (RF18, RF19)."""

    class Proposito(models.TextChoices):
        CLIENTE = "CLIENTE", "Contenido para el cliente"
        ALERTA_ADMIN = "ALERTA_ADMIN", "Alerta al administrador"
        RECORDATORIO = "RECORDATORIO", "Recordatorio al personal"

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        ENVIADO = "ENVIADO", "Enviado"
        ENTREGADO = "ENTREGADO", "Entregado"
        LEIDO = "LEIDO", "Leido"
        FALLIDO = "FALLIDO", "Fallido"

    proposito = models.CharField(max_length=14, choices=Proposito.choices)
    # Numero y nombre cifrados en reposo (RNF02).
    destino = EncryptedTextField()
    destino_nombre = EncryptedTextField(blank=True, default="")
    plantilla = models.CharField(max_length=100, blank=True)
    parametros = models.JSONField(default=list, blank=True)
    # Archivo adjunto: ruta relativa a MEDIA_ROOT. kind: image | video.
    media_ruta = models.CharField(max_length=255, blank=True)
    media_nombre = models.CharField(max_length=255, blank=True)
    media_kind = models.CharField(max_length=5, blank=True)

    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    wa_message_id = models.CharField(max_length=200, blank=True, db_index=True)
    error = models.TextField(blank=True)
    enviado_at = models.DateTimeField(null=True, blank=True)
    entregado_at = models.DateTimeField(null=True, blank=True)

    entity_type = models.CharField(max_length=60, blank=True)
    entity_id = models.CharField(max_length=64, blank=True)
    contexto = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    def __str__(self):
        return f"{self.proposito} {self.estado} ({self.pk})"


class Alerta(BaseModel):
    """Alerta para el administrador. Queda registrada aunque no tenga WhatsApp configurado,
    para no perderla (RF19)."""

    class Tipo(models.TextChoices):
        PUBLICACION_FALLIDA = "PUBLICACION_FALLIDA", "Publicacion fallida"
        PUBLICACION_BLOQUEADA = "PUBLICACION_BLOQUEADA", "Publicacion bloqueada por falta de aprobacion"
        PUBLICACION_ATRASADA = "PUBLICACION_ATRASADA", "Publicacion pendiente sin confirmar"
        CUENTA_EXPIRADA = "CUENTA_EXPIRADA", "Cuenta de red expirada"
        CUENTA_DESVINCULADA = "CUENTA_DESVINCULADA", "Cuenta de red desvinculada"
        ENVIO_CLIENTE_FALLIDO = "ENVIO_CLIENTE_FALLIDO", "Fallo el envio al cliente"

    tipo = models.CharField(max_length=30, choices=Tipo.choices)
    mensaje = models.TextField()
    entity_type = models.CharField(max_length=60, blank=True)
    entity_id = models.CharField(max_length=64, blank=True)
    leida_at = models.DateTimeField(null=True, blank=True)
    # Evita repetir la misma alerta (p. ej. una publicacion bloqueada en cada ciclo del scheduler).
    dedupe_key = models.CharField(max_length=200, null=True, blank=True, unique=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tipo}: {self.mensaje[:40]}"
