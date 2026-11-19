from django.conf import settings
from django.db import models

from modules.core.models import BaseModel
from modules.core.networks import Network


class TareaPublicacion(BaseModel):
    """Publicacion de una pieza en UNA red, en la cuenta del cliente (RF17).

    Meta exige ser Tech Provider para publicar en cuentas de terceros, y la agencia no lo es:
    la publicacion es manual asistida. A la hora programada el sistema crea la tarea y avisa;
    el personal publica en la red y confirma con el enlace, o reporta el fallo con su motivo.
    """

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente de publicar"
        PUBLICADA = "PUBLICADA", "Publicada"
        FALLIDA = "FALLIDA", "Fallida"
        CANCELADA = "CANCELADA", "Cancelada"

    pieza = models.ForeignKey("content.Pieza", on_delete=models.CASCADE, related_name="tareas")
    red = models.CharField(max_length=12, choices=Network.choices)
    # RNF09: la cuenta siempre es del cliente de la pieza (se valida en el servicio).
    cuenta = models.ForeignKey("clients.CuentaSocial", on_delete=models.PROTECT, related_name="+")
    cuenta_handle = models.CharField(max_length=150)  # copia: sobrevive a cambios de la cuenta
    cuenta_url = models.URLField(max_length=500)

    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE, db_index=True)
    copy_final = models.TextField()
    programada_para = models.DateTimeField(db_index=True)
    avisada_at = models.DateTimeField(null=True, blank=True)  # cuando se aviso al equipo (RNF06)
    atraso_alertado_at = models.DateTimeField(null=True, blank=True)

    enlace = models.URLField(max_length=500, blank=True)
    motivo = models.TextField(blank=True)
    publicada_at = models.DateTimeField(null=True, blank=True)
    gestionada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    intentos = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["programada_para", "red"]
        constraints = [models.UniqueConstraint(fields=["pieza", "red"], name="una_tarea_por_red_y_pieza")]

    def __str__(self):
        return f"{self.red} {self.estado} ({self.pieza_id})"
