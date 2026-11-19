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


class RedSerializer(serializers.Serializer):
    red = serializers.CharField()
    copy = serializers.CharField(allow_blank=True)
    copy_final = serializers.CharField()




class PiezaSerializer(serializers.ModelSerializer):
    """Ficha completa (RF9)."""

    cliente_nombre = serializers.CharField(source="cliente.nombre", read_only=True)
    redes = serializers.SerializerMethodField()
    archivos = ArchivoSerializer(many=True, read_only=True)
    tiene_archivo_editable = serializers.SerializerMethodField()
    responsable_username = serializers.CharField(source="responsable.username", default="", read_only=True)

    class Meta:
        model = Pieza
        fields = [
            "id",
            "cliente",
            "cliente_nombre",
            "titulo",
            "tipo",
            "estado",
            "programada_para",
            "redes",
            "copy_comun",
            "hashtags",
            "enlaces",
            "campana",
            "responsable",
            "responsable_username",
            "mensaje_cliente",
            "archivos",
            "tiene_archivo_editable",
            "archivo_editable_nombre",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_redes(self, obj) -> list[dict]:
        return [{"red": r.red, "copy": r.copy, "copy_final": obj.copy_final(r.red)} for r in obj.redes.all()]

    def get_tiene_archivo_editable(self, obj) -> bool:
        return bool(obj.archivo_editable)


class PiezaListSerializer(serializers.ModelSerializer):
    """Version liviana para listas y cronograma."""

    cliente_nombre = serializers.CharField(source="cliente.nombre", read_only=True)
    redes = serializers.SerializerMethodField()
    archivos_count = serializers.SerializerMethodField()
    responsable_username = serializers.CharField(source="responsable.username", default="", read_only=True)

    class Meta:
        model = Pieza
        fields = [
            "id",
            "cliente",
            "cliente_nombre",
            "titulo",
            "tipo",
            "estado",
            "programada_para",
            "redes",
            "archivos_count",
            "responsable",
            "responsable_username",
        ]
        read_only_fields = fields

    def get_redes(self, obj) -> list[str]:
        return obj.network_list()

    def get_archivos_count(self, obj) -> int:
        return len(obj.archivos.all())








class ArchivoUploadSerializer(serializers.Serializer):
    archivo = serializers.FileField()


class OrdenSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.UUIDField())
