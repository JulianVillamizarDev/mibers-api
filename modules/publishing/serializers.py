import re

from rest_framework import serializers

from modules.clients.serializers import CuentaSocialSerializer
from modules.content.serializers import PiezaListSerializer

from .models import TareaPublicacion


class TareaSerializer(serializers.ModelSerializer):
    pieza_titulo = serializers.CharField(source="pieza.titulo", read_only=True)
    pieza_tipo = serializers.CharField(source="pieza.tipo", read_only=True)
    cliente = serializers.UUIDField(source="pieza.cliente_id", read_only=True)
    cliente_nombre = serializers.CharField(source="pieza.cliente.nombre", read_only=True)
    gestionada_por_username = serializers.CharField(
        source="gestionada_por.username", default="", read_only=True
    )

    class Meta:
        model = TareaPublicacion
        fields = [
            "id",
            "pieza",
            "pieza_titulo",
            "pieza_tipo",
            "cliente",
            "cliente_nombre",
            "red",
            "cuenta_handle",
            "cuenta_url",
            "estado",
            "copy_final",
            "programada_para",
            "avisada_at",
            "enlace",
            "motivo",
            "publicada_at",
            "gestionada_por",
            "gestionada_por_username",
            "intentos",
            "modo",
            "externo_contenedor_id",
            "externo_post_id",
            "externo_estado",
        ]
        read_only_fields = fields


class RedPreviewSerializer(serializers.Serializer):
    red = serializers.CharField()
    cuenta = CuentaSocialSerializer(allow_null=True)
    copy_final = serializers.CharField()


class ProgramacionPreviewSerializer(serializers.Serializer):
    pieza = PiezaListSerializer()
    cliente = serializers.CharField()
    programada_para = serializers.DateTimeField(allow_null=True)
    redes = RedPreviewSerializer(many=True)
    archivos = serializers.ListField(child=serializers.CharField())
    problemas = serializers.ListField(child=serializers.CharField())
    puede_confirmar = serializers.BooleanField()


class ProgramarSerializer(serializers.Serializer):
    confirmar = serializers.BooleanField(default=False)


class ConfirmarSerializer(serializers.Serializer):
    enlace = serializers.URLField(max_length=500)


class FallarSerializer(serializers.Serializer):
    motivo = serializers.CharField(allow_blank=True, default="")


def mask_number(numero: str) -> str:
    """'+573001112233' -> '•••• 2233'. Nunca devuelve el numero completo (RNF02)."""
    digits = re.sub(r"\D", "", numero or "")
    return f"•••• {digits[-4:]}" if len(digits) >= 4 else "••••"


class EnvioSerializer(serializers.Serializer):
    """Envio de contenido a un contacto del cliente (HU-19, RF18).

    Requiere `context["piezas"]`: dict {str(pk): Pieza con cliente} para no hacer una consulta por envio.
    El numero va enmascarado (RNF02)."""

    id = serializers.UUIDField(read_only=True)
    pieza = serializers.SerializerMethodField()
    cliente = serializers.SerializerMethodField()
    contacto = serializers.SerializerMethodField()
    numero = serializers.SerializerMethodField()
    archivo = serializers.SerializerMethodField()
    estado = serializers.CharField(read_only=True)
    error = serializers.CharField(read_only=True)
    enviado_at = serializers.DateTimeField(read_only=True, allow_null=True)
    entregado_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)

    def _pieza(self, obj):
        return self.context["piezas"].get(obj.entity_id)

    def get_pieza(self, obj) -> dict | None:
        pieza = self._pieza(obj)
        # None si la pieza ya no existe (p. ej. purga por retencion del cliente)
        return {"id": str(pieza.pk), "titulo": pieza.titulo, "tipo": pieza.tipo} if pieza else None

    def get_cliente(self, obj) -> dict | None:
        pieza = self._pieza(obj)
        return {"id": str(pieza.cliente_id), "nombre": pieza.cliente.nombre} if pieza else None

    def get_contacto(self, obj) -> dict:
        return {"id": (obj.contexto or {}).get("contacto"), "nombre": obj.destino_nombre}

    def get_numero(self, obj) -> str:
        return mask_number(obj.destino)

    def get_archivo(self, obj) -> dict:
        return {
            "id": (obj.contexto or {}).get("archivo"),
            "nombre": obj.media_nombre,
            "kind": obj.media_kind,
        }
