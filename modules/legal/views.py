from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.core.exceptions import DomainError
from modules.core.permissions import IsAdmin

from .models import PoliticaTratamiento


class PoliticaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PoliticaTratamiento
        fields = ["id", "version", "titulo", "contenido", "vigente_desde"]
        read_only_fields = ["id"]


class PoliticaVigenteView(APIView):
    """Publica: la politica debe poder leerse sin iniciar sesion (RNF13)."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        politica = PoliticaTratamiento.objects.first()
        if politica is None:
            raise DomainError("Aun no se ha publicado la politica.", "policy_not_published", 404)
        return Response(PoliticaSerializer(politica).data)


class PoliticaListCreateView(APIView):
    """Solo el Administrador publica nuevas versiones."""

    permission_classes = [*APIView.permission_classes, IsAdmin]

    def get(self, request):
        return Response(PoliticaSerializer(PoliticaTratamiento.objects.all(), many=True).data)

    def post(self, request):
        data = PoliticaSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        politica = data.save()
        return Response(PoliticaSerializer(politica).data, status=status.HTTP_201_CREATED)
