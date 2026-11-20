from datetime import timedelta
from pathlib import Path

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.content.models import ArchivoPieza, Pieza
from modules.content.serializers import PiezaSerializer
from modules.content.views import get_piece, parse_day
from modules.core.files import serve_file
from modules.core.networks import Network
from modules.core.pagination import StandardPagination
from modules.core.permissions import IsAdmin, IsStaffOrAdmin
from modules.core.timezone import bogota_day_bounds

from . import envios, services
from .models import TareaPublicacion
from .public_media import should_convert_to_jpeg, signature_is_valid, to_jpeg
from .serializers import (
    ConfirmarSerializer,
    FallarSerializer,
    ProgramacionPreviewSerializer,
    ProgramarSerializer,
    TareaSerializer,
)

STAFF = [*APIView.permission_classes, IsStaffOrAdmin]
ADMIN = [*APIView.permission_classes, IsAdmin]






# --- Programacion (RF14, RNF09) ----------------------------------------------------------------------
class ProgramacionPreviewView(APIView):
    """Muestra cliente, cuentas destino y copy por red ANTES de confirmar (RNF09)."""

    permission_classes = STAFF

    def get(self, request, pk):
        return Response(ProgramacionPreviewSerializer(services.schedule_preview(get_piece(pk))).data)


class ProgramarView(APIView):
    permission_classes = STAFF

    def post(self, request, pk):
        data = ProgramarSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        pieza = services.schedule_piece(
            get_piece(pk),
            acting_user=request.user,
            confirmed=data.validated_data["confirmar"],
            request=request,
        )
        return Response(PiezaSerializer(pieza).data)


class CancelarProgramacionView(APIView):
    permission_classes = STAFF

    def post(self, request, pk):
        pieza = services.unschedule_piece(get_piece(pk), acting_user=request.user, request=request)
        return Response(PiezaSerializer(pieza).data)


# --- Archivos publicos firmados (Meta) ----------------------------------------------------------------
class PublicMediaView(APIView):
    """Meta descarga la pieza sin sesion: firma valida y una tarea pendiente o fallida."""

    authentication_classes = []
    permission_classes = []

    def get(self, request, archivo_id):
        if not signature_is_valid(
            archivo_id, request.query_params.get("exp"), request.query_params.get("sig")
        ):
            raise Http404
        archivo = ArchivoPieza.objects.select_related("pieza").filter(pk=archivo_id).first()
        if archivo is None:
            raise Http404
        redes = set(
            archivo.pieza.tareas.filter(
                estado__in=[TareaPublicacion.Estado.PENDIENTE, TareaPublicacion.Estado.FALLIDA]
            ).values_list("red", flat=True)
        )
        if not redes:
            raise Http404
        if Network.INSTAGRAM in redes and should_convert_to_jpeg(archivo):
            try:
                jpeg = to_jpeg(archivo)
            except ValueError as exc:
                raise Http404 from exc
            return serve_file(jpeg, f"{Path(archivo.nombre_original).stem}.jpg")
        return serve_file(archivo.archivo, archivo.nombre_original)


# --- Publicaciones por red (RF17) ---------------------------------------------------------------------






# --- Registro de envios al cliente (HU-19, RF18) -----------------------------------------------------










