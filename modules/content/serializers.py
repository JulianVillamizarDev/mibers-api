from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import serializers

from modules.core.networks import Network

from .models import ArchivoPieza, ComentarioRevision, Pieza, Tipo

User = get_user_model()


class ArchivoSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ArchivoPieza
        fields = [
            "id",
            "nombre_original",
            "orden",
            "kind",
            "formato",
            "tamano",
            "ancho",
            "alto",
            "duracion",
            "url",
        ]
        read_only_fields = fields

    def get_url(self, obj) -> str:
        return reverse("archivo-descarga", args=[obj.pk])
















class ArchivoUploadSerializer(serializers.Serializer):
    archivo = serializers.FileField()


class OrdenSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField())
