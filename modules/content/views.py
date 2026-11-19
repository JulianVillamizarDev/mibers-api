from datetime import date

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.clients.models import Cliente
from modules.clients.serializers import ClienteSerializer
from modules.core.exceptions import DomainError
from modules.core.files import serve_file
from modules.core.networks import Network
from modules.core.pagination import StandardPagination
from modules.core.permissions import IsAdmin, IsStaffOrAdmin
from modules.core.timezone import bogota_day_bounds, now_bogota

from . import selectors, services
from .models import ArchivoPieza, Estado, Pieza, Tipo
from .serializers import (
    ArchivoSerializer,
    ArchivoUploadSerializer,
    ComentarioInputSerializer,
    ComentarioSerializer,
    OrdenSerializer,
    PiezaListSerializer,
    PiezaSerializer,
    PiezaWriteSerializer,
)

STAFF = [*APIView.permission_classes, IsStaffOrAdmin]
ADMIN = [*APIView.permission_classes, IsAdmin]






# --- Piezas ------------------------------------------------------------------------------------




# --- Archivos --------------------------------------------------------------------------------------










# --- Revision (RF12) ---------------------------------------------------------------------------------








# --- Cronograma y empresas actuales (RF20, RF5) -----------------------------------------------------------


class EmpresasActualesView(APIView):
    permission_classes = STAFF

    def get(self, request):
        rows = selectors.current_companies()
        return Response(
            [
                {
                    "cliente": ClienteSerializer(row["cliente"]).data,
                    "proximas_publicaciones": PiezaListSerializer(
                        row["proximas_publicaciones"], many=True
                    ).data,
                    "pendientes": row["pendientes"],
                    "semana_actual": row["semana_actual"],
                }
                for row in rows
            ]
        )
