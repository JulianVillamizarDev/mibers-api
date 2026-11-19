from django.conf import settings
from django.db import models

from modules.core.models import BaseModel
from modules.core.networks import Network


class MetricaMensual(BaseModel):
    """Alcance, visualizaciones, interacciones y seguidores de un cliente en una red y un mes (RF23).

    Sin ser Tech Provider no se pueden leer las metricas de las cuentas de los clientes por la API
    de Meta, asi que el equipo las registra a partir de Meta Business Suite. Sin analisis por
    horario ni exportacion de reportes (fuera de alcance). Un valor vacio significa "no disponible".
    """

    cliente = models.ForeignKey("clients.Cliente", on_delete=models.CASCADE, related_name="metricas")
    red = models.CharField(max_length=12, choices=Network.choices)
    mes = models.DateField(help_text="Primer dia del mes")
    alcance = models.PositiveBigIntegerField(null=True, blank=True)
    visualizaciones = models.PositiveBigIntegerField(null=True, blank=True)
    interacciones = models.PositiveBigIntegerField(null=True, blank=True)
    seguidores = models.PositiveBigIntegerField(null=True, blank=True)
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
