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


def get_piece(pk) -> Pieza:
    return get_object_or_404(selectors.pieces_queryset(), pk=pk)




# --- Piezas ------------------------------------------------------------------------------------




# --- Archivos --------------------------------------------------------------------------------------
class ArchivoCreateView(APIView):
    permission_classes = STAFF
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        pieza = get_piece(pk)
        data = ArchivoUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        archivo = services.add_file(
            pieza, data.validated_data["archivo"], acting_user=request.user, request=request
        )
        return Response(ArchivoSerializer(archivo).data, status=status.HTTP_201_CREATED)


class ArchivoOrdenView(APIView):
    permission_classes = STAFF
    parser_classes = [JSONParser]

    def put(self, request, pk):
        data = OrdenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        pieza = services.reorder_files(
            get_piece(pk), data.validated_data["ids"], acting_user=request.user, request=request
        )
        return Response(PiezaSerializer(pieza).data)


class ArchivoDetailView(APIView):
    permission_classes = STAFF

    def delete(self, request, pk, archivo_id):
        archivo = get_object_or_404(ArchivoPieza, pk=archivo_id, pieza_id=pk)
        services.remove_file(archivo, acting_user=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ArchivoDownloadView(APIView):
    """Los archivos nunca se exponen por URL publica: solo con sesion valida."""

    permission_classes = STAFF

    def get(self, request, archivo_id):
        archivo = get_object_or_404(ArchivoPieza, pk=archivo_id)
        return serve_file(archivo.archivo, archivo.nombre_original)


class ArchivoEditableView(APIView):
    permission_classes = STAFF
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        data = ArchivoUploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        pieza = services.set_editable_file(
            get_piece(pk), data.validated_data["archivo"], acting_user=request.user, request=request
        )
        return Response(PiezaSerializer(pieza).data)

    def get(self, request, pk):
        pieza = get_object_or_404(Pieza, pk=pk)
        return serve_file(pieza.archivo_editable, pieza.archivo_editable_nombre or None)


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
