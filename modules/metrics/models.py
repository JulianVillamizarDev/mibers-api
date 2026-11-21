from django.conf import settings
from django.db import models

from modules.core.models import BaseModel
from modules.core.networks import Network


class MetricaMensual(BaseModel):
    """Alcance, visualizaciones, interacciones y seguidores por cliente, red y mes (RF23).

    Las metricas se sincronizan desde Meta cuando hay token; la carga manual del equipo se
    conserva como respaldo y no se sobrescribe salvo que se fuerce. Sin analisis por horario
    ni exportacion de reportes (fuera de alcance). Un valor vacio significa "no disponible".
    """

    class Origen(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        API = "API", "Graph API"

    cliente = models.ForeignKey("clients.Cliente", on_delete=models.CASCADE, related_name="metricas")
    red = models.CharField(max_length=12, choices=Network.choices)
    mes = models.DateField(help_text="Primer dia del mes")
    alcance = models.PositiveBigIntegerField(null=True, blank=True)
    visualizaciones = models.PositiveBigIntegerField(null=True, blank=True)
    interacciones = models.PositiveBigIntegerField(null=True, blank=True)
    seguidores = models.PositiveBigIntegerField(null=True, blank=True)
    origen = models.CharField(max_length=8, choices=Origen.choices, default=Origen.MANUAL)
    sincronizado_at = models.DateTimeField(null=True, blank=True)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-mes", "red"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "red", "mes"], name="una_metrica_por_cliente_red_mes")
        ]

    def __str__(self):
        return f"{self.cliente_id} {self.red} {self.mes:%Y-%m}"
