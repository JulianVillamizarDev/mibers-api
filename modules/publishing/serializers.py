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
