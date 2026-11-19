from django.db import models

from modules.core.fields import EncryptedTextField
from modules.core.files import upload_path
from modules.core.models import BaseModel
from modules.core.networks import Network


def manual_marca_path(instance, filename):
    return upload_path("clientes", str(instance.pk), "manual", filename=filename)


class Estrategia(models.TextChoices):
    VIRALIZAR = "VIRALIZAR", "Viralizar"
    POSICIONAR = "POSICIONAR", "Posicionar"
    VENDER = "VENDER", "Vender"


class Cliente(BaseModel):
    """Cliente de la agencia con su plan, contrato y objetivo (RF5)."""

    nombre = models.CharField(max_length=200)
    # Datos de contacto cifrados en reposo (RNF02).
    contacto_nombre = EncryptedTextField(blank=True, default="")
    contacto_telefono = EncryptedTextField(blank=True, default="")
    contacto_email = EncryptedTextField(blank=True, default="")

    posts_por_semana = models.PositiveSmallIntegerField(help_text="Plan contratado, p. ej. 3 o 5")
    contrato_inicio = models.DateField()
    contrato_fin = models.DateField(null=True, blank=True)
    estrategia = models.CharField(max_length=12, choices=Estrategia.choices)
    objetivo = models.TextField(blank=True)
    manual_marca = models.FileField(upload_to=manual_marca_path, blank=True)
    manual_marca_nombre = models.CharField(max_length=255, blank=True)

    activo = models.BooleanField(default=True)
    # Inicio del plazo de retencion de un anio (RF5).
    desactivado_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["nombre"]
        indexes = [models.Index(fields=["activo", "desactivado_at"])]

    def __str__(self):
        return self.nombre


class ContactoWhatsApp(BaseModel):
    """Destinatario del contenido por WhatsApp (RF6). Sin autorizacion del titular no se envia
    nada (RNF13, Ley 1581 de 2012)."""

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="contactos")
    nombre = EncryptedTextField()
    numero = EncryptedTextField(help_text="Formato E.164, p. ej. +573001112233")
    autorizacion_at = models.DateTimeField(null=True, blank=True)
    autorizacion_evidencia = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Contacto {self.pk} de {self.cliente_id}"

    @property
    def autorizado(self) -> bool:
        return self.autorizacion_at is not None


class CuentaSocial(BaseModel):
    """Cuenta de Instagram/Facebook del cliente (RF7).

    La agencia no es Tech Provider de Meta, por lo que la cuenta se registra manualmente
    (sin guardar contrasenas) y la publicacion la confirma el personal. El estado lo
    gestiona el administrador."""

    class Estado(models.TextChoices):
        VINCULADA = "VINCULADA", "Vinculada"
        EXPIRADA = "EXPIRADA", "Expirada"
        DESVINCULADA = "DESVINCULADA", "Desvinculada"

    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, related_name="cuentas")
    red = models.CharField(max_length=12, choices=Network.choices)
    handle = models.CharField(max_length=150)
    url_perfil = models.URLField(max_length=500)
    id_externo = models.CharField(max_length=100, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.VINCULADA)
    estado_actualizado_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["red"]
        constraints = [
            models.UniqueConstraint(fields=["cliente", "red"], name="una_cuenta_por_red_y_cliente")
        ]

    def __str__(self):
        return f"{self.red} {self.handle}"
