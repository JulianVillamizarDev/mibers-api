from django.db import models


class Network(models.TextChoices):
    """Redes soportadas (RF16). TikTok, LinkedIn y X estan fuera del alcance."""

    INSTAGRAM = "INSTAGRAM", "Instagram"
    FACEBOOK = "FACEBOOK", "Facebook"
