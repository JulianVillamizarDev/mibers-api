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














