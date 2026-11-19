from django.conf import settings
from django.db import models

from modules.core.files import upload_path
from modules.core.models import BaseModel
from modules.core.networks import Network


class Tipo(models.TextChoices):
    POST = "POST", "Post"
    CARRUSEL = "CARRUSEL", "Carrusel"
    REEL = "REEL", "Reel"
    HISTORIA = "HISTORIA", "Historia"
    PORTADA = "PORTADA", "Portada"


class Estado(models.TextChoices):
    """RF10."""

    BORRADOR = "BORRADOR", "Borrador"
    EN_REVISION = "EN_REVISION", "En revision"
    CAMBIOS_SOLICITADOS = "CAMBIOS_SOLICITADOS", "Cambios solicitados"
    APROBADA = "APROBADA", "Aprobada"
    PROGRAMADA = "PROGRAMADA", "Programada"
    PUBLICADA = "PUBLICADA", "Publicada"
    FALLIDA = "FALLIDA", "Fallida"
    COMPARTIDA = "COMPARTIDA", "Compartida"


def archivo_path(instance, filename):
    pieza = instance.pieza
    return upload_path("clientes", str(pieza.cliente_id), "piezas", str(pieza.pk), filename=filename)


def editable_path(instance, filename):
    return upload_path(
        "clientes", str(instance.cliente_id), "piezas", str(instance.pk), "editable", filename=filename
    )


class Pieza(BaseModel):
    """Pieza de contenido con su ficha (RF9)."""

    cliente = models.ForeignKey("clients.Cliente", on_delete=models.CASCADE, related_name="piezas")
    titulo = models.CharField(max_length=200, blank=True)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.BORRADOR, db_index=True)
    # Fecha y hora de publicacion; se guarda en UTC y se muestra en hora de Colombia (RF14).
    programada_para = models.DateTimeField(null=True, blank=True, db_index=True)
    copy_comun = models.TextField(blank=True)
    hashtags = models.JSONField(default=list, blank=True)
    enlaces = models.JSONField(default=list, blank=True)
    campana = models.CharField(max_length=150, blank=True)
    responsable = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="piezas_a_cargo",
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    mensaje_cliente = models.TextField(blank=True)
    archivo_editable = models.FileField(upload_to=editable_path, blank=True, max_length=255)
    archivo_editable_nombre = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["programada_para", "created_at"]
        indexes = [models.Index(fields=["cliente", "estado"])]

    def __str__(self):
        return self.titulo or f"{self.get_tipo_display()} {self.pk}"

    def copy_final(self, red: str) -> str:
        """Texto que se publica en `red`: el copy propio de la red o el comun, mas los hashtags."""
        own = next((r.copy for r in self.redes.all() if r.red == red), "")
        text = (own or self.copy_comun).strip()
        tags = " ".join(self.hashtags or [])
        return f"{text}\n\n{tags}".strip() if tags else text

    def network_list(self) -> list[str]:
        return [r.red for r in self.redes.all()]


class PiezaRed(BaseModel):
    """Red destino de una pieza. `copy` vacio = usa el copy comun (RF11)."""

    pieza = models.ForeignKey(Pieza, on_delete=models.CASCADE, related_name="redes")
    red = models.CharField(max_length=12, choices=Network.choices)
    copy = models.TextField(blank=True)

    class Meta:
        ordering = ["red"]
        constraints = [models.UniqueConstraint(fields=["pieza", "red"], name="una_fila_por_red_y_pieza")]

    def __str__(self):
        return f"{self.pieza_id} -> {self.red}"


class ArchivoPieza(BaseModel):
    pieza = models.ForeignKey(Pieza, on_delete=models.CASCADE, related_name="archivos")
    archivo = models.FileField(upload_to=archivo_path, max_length=255)
    nombre_original = models.CharField(max_length=255)
    orden = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField(max_length=5)  # image | video
    formato = models.CharField(max_length=10)
    tamano = models.PositiveBigIntegerField(default=0)
    ancho = models.PositiveIntegerField(default=0)
    alto = models.PositiveIntegerField(default=0)
    duracion = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ["orden", "created_at"]

    def __str__(self):
        return self.nombre_original


class ComentarioRevision(BaseModel):
    """Historial de decisiones de revision (RF12)."""

    class Decision(models.TextChoices):
        ENVIADA = "ENVIADA", "Enviada a revision"
        APROBADA = "APROBADA", "Aprobada"
        CAMBIOS = "CAMBIOS", "Cambios solicitados"
        DEVUELTA = "DEVUELTA", "Devuelta a revision por edicion"

    pieza = models.ForeignKey(Pieza, on_delete=models.CASCADE, related_name="revisiones")
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comentario = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]
