from django.db import models


class Role(models.TextChoices):
    """RF3: dos perfiles. Los permisos dependen solo del rol, nunca del cargo (RNF05)."""

    ADMIN = "ADMIN", "Administrador"
    STAFF = "STAFF", "Personal"
