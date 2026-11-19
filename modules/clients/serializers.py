from pathlib import Path

from rest_framework import serializers

from modules.core.validators import validate_e164

from .models import Cliente, ContactoWhatsApp, CuentaSocial, Estrategia

MANUAL_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".ppt", ".pptx", ".zip"}
MANUAL_MAX_BYTES = 25 * 1024 * 1024


class CuentaSocialSerializer(serializers.ModelSerializer):
    class Meta:
        model = CuentaSocial
        fields = [
            "id",
            "cliente",
            "red",
            "handle",
            "url_perfil",
            "id_externo",
            "estado",
            "estado_actualizado_at",
        ]
        read_only_fields = ["id", "cliente", "estado_actualizado_at"]


class CuentaSocialCreateSerializer(serializers.Serializer):
    red = serializers.ChoiceField(choices=CuentaSocial._meta.get_field("red").choices)
    handle = serializers.CharField(max_length=150)
    url_perfil = serializers.URLField(max_length=500)
    id_externo = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class CuentaSocialUpdateSerializer(serializers.Serializer):
    handle = serializers.CharField(max_length=150, required=False)
    url_perfil = serializers.URLField(max_length=500, required=False)
    id_externo = serializers.CharField(max_length=100, required=False, allow_blank=True)
    estado = serializers.ChoiceField(choices=CuentaSocial.Estado.choices, required=False)


class ContactoSerializer(serializers.ModelSerializer):
    autorizado = serializers.BooleanField(read_only=True)

    class Meta:
        model = ContactoWhatsApp
        fields = [
            "id",
            "cliente",
            "nombre",
            "numero",
            "autorizado",
            "autorizacion_at",
            "autorizacion_evidencia",
        ]
        read_only_fields = fields


class ContactoCreateSerializer(serializers.Serializer):
    nombre = serializers.CharField(max_length=200)
    numero = serializers.CharField(validators=[validate_e164])
    autorizado = serializers.BooleanField(
        help_text="El titular autorizo el tratamiento de sus datos (Ley 1581 de 2012)."
    )
    evidencia = serializers.CharField(required=False, allow_blank=True, default="")


class ContactoUpdateSerializer(serializers.Serializer):
    nombre = serializers.CharField(max_length=200, required=False)
    numero = serializers.CharField(validators=[validate_e164], required=False)
    autorizado = serializers.BooleanField(required=False)
    evidencia = serializers.CharField(required=False, allow_blank=True)


class ClienteSerializer(serializers.ModelSerializer):
    tiene_manual_marca = serializers.SerializerMethodField()
    contactos_whatsapp = ContactoSerializer(source="contactos", many=True, read_only=True)
    cuentas = CuentaSocialSerializer(many=True, read_only=True)

    class Meta:
        model = Cliente
        fields = [
            "id",
            "nombre",
            "contacto_nombre",
            "contacto_telefono",
            "contacto_email",
            "posts_por_semana",
            "contrato_inicio",
            "contrato_fin",
            "estrategia",
            "objetivo",
            "tiene_manual_marca",
            "manual_marca_nombre",
            "activo",
            "desactivado_at",
            "contactos_whatsapp",
            "cuentas",
            "created_at",
        ]
        read_only_fields = fields

    def get_tiene_manual_marca(self, obj) -> bool:
        return bool(obj.manual_marca)


class ClienteWriteSerializer(serializers.Serializer):
    nombre = serializers.CharField(max_length=200)
    contacto_nombre = serializers.CharField(required=False, allow_blank=True, default="")
    contacto_telefono = serializers.CharField(required=False, allow_blank=True, default="")
    contacto_email = serializers.EmailField(required=False, allow_blank=True, default="")
    posts_por_semana = serializers.IntegerField(min_value=1, max_value=14)
    contrato_inicio = serializers.DateField()
    contrato_fin = serializers.DateField(required=False, allow_null=True, default=None)
    estrategia = serializers.ChoiceField(choices=Estrategia.choices)
    objetivo = serializers.CharField(required=False, allow_blank=True, default="")
    manual_marca = serializers.FileField(required=False)

    def validate_manual_marca(self, value):
        if Path(value.name).suffix.lower() not in MANUAL_EXTENSIONS:
            raise serializers.ValidationError("Formato no permitido para el manual de marca.")
        if value.size > MANUAL_MAX_BYTES:
            raise serializers.ValidationError("El manual de marca no puede superar 25 MB.")
        return value

    def validate(self, attrs):
        start = attrs.get("contrato_inicio") or getattr(self.instance, "contrato_inicio", None)
        end = attrs.get("contrato_fin") or getattr(self.instance, "contrato_fin", None)
        if start and end and end < start:
            raise serializers.ValidationError(
                {"contrato_fin": "No puede ser anterior al inicio del contrato."}
            )
        return attrs
