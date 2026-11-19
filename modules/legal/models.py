from django.db import models
from django.utils import timezone

from modules.core.models import BaseModel


class PoliticaTratamiento(BaseModel):
    """Politica de tratamiento de datos personales (Ley 1581 de 2012, Decreto 1377 de 2013).

    Se versiona: publicar una nueva version no borra las anteriores.
    """

    version = models.CharField(max_length=20, unique=True)
    titulo = models.CharField(max_length=200)
    contenido = models.TextField()
    vigente_desde = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-vigente_desde"]
        verbose_name_plural = "politicas de tratamiento"

    def __str__(self):
        return f"{self.titulo} v{self.version}"
