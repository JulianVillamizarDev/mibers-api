import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from modules.core.fields import EncryptedTextField
from modules.core.models import BaseModel
from modules.core.roles import Role


class User(AbstractUser):
    """Miembro del equipo. Sin correo obligatorio ni registro publico (RF1, RF2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.STAFF)
    # Informativo: no otorga permisos (RF3, RNF05).
    cargo = models.CharField(max_length=100, blank=True)
    # Para recibir alertas y recordatorios por WhatsApp (RF19). Cifrado en reposo.
    whatsapp_number = EncryptedTextField(blank=True, null=True)
    must_change_password = models.BooleanField(default=False)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def display_name(self) -> str:
        return self.get_full_name() or self.username


class UserSession(BaseModel):
    """Sesion de login. El JWT lleva su id en el claim `sid`; permite cerrar sesion
    y expirar por inactividad (RNF01) aunque el token siga vigente."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    last_seen_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "revoked_at"])]
