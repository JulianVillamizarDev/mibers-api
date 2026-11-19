from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.content.models import Pieza
from modules.content.serializers import PiezaSerializer
from modules.content.views import get_piece, parse_day
from modules.core.networks import Network
from modules.core.pagination import StandardPagination
from modules.core.permissions import IsAdmin, IsStaffOrAdmin
from modules.core.timezone import bogota_day_bounds

from . import services
from .models import TareaPublicacion
from .serializers import (
    ConfirmarSerializer,
    FallarSerializer,
    ProgramacionPreviewSerializer,
    ProgramarSerializer,
    TareaSerializer,
)

STAFF = [*APIView.permission_classes, IsStaffOrAdmin]
ADMIN = [*APIView.permission_classes, IsAdmin]


def tasks_queryset():
    return TareaPublicacion.objects.select_related("pieza__cliente", "gestionada_por")


def get_task(pk) -> TareaPublicacion:
    return get_object_or_404(tasks_queryset(), pk=pk)


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


# --- Publicaciones por red (RF17) ---------------------------------------------------------------------
class TareaListView(APIView):
    """Bandeja de publicaciones. `?estado=PENDIENTE` es la lista de lo que hay que publicar."""

    permission_classes = STAFF

    def get(self, request):
        params = request.query_params
        queryset = tasks_queryset()
        if params.get("estado"):
            queryset = queryset.filter(estado__in=params["estado"].split(","))
        if params.get("cliente"):
            queryset = queryset.filter(pieza__cliente_id=params["cliente"])
        if params.get("red") in Network.values:
            queryset = queryset.filter(red=params["red"])
        if params.get("desde"):
            queryset = queryset.filter(programada_para__gte=bogota_day_bounds(parse_day(params["desde"]))[0])
        if params.get("hasta"):
            queryset = queryset.filter(programada_para__lt=bogota_day_bounds(parse_day(params["hasta"]))[1])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(TareaSerializer(page, many=True).data)


class TareaDetailView(APIView):
    permission_classes = STAFF

    def get(self, request, pk):
        return Response(TareaSerializer(get_task(pk)).data)


class PiezaPublicacionesView(APIView):
    """Resultado de la publicacion por red de una pieza."""

    permission_classes = STAFF

    def get(self, request, pk):
        pieza = get_object_or_404(Pieza, pk=pk)
        return Response(TareaSerializer(tasks_queryset().filter(pieza=pieza), many=True).data)


class ConfirmarView(APIView):
    permission_classes = STAFF

    def post(self, request, pk):
        data = ConfirmarSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        tarea = services.confirm_published(
            get_task(pk), enlace=data.validated_data["enlace"], acting_user=request.user, request=request
        )
        return Response(TareaSerializer(tarea).data)


class FallarView(APIView):
    permission_classes = STAFF

    def post(self, request, pk):
        data = FallarSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        tarea = services.report_failed(
            get_task(pk), motivo=data.validated_data["motivo"], acting_user=request.user, request=request
        )
        return Response(TareaSerializer(tarea).data)


class ReintentarView(APIView):
    permission_classes = STAFF

    def post(self, request, pk):
        tarea = services.retry(get_task(pk), acting_user=request.user, request=request)
        return Response(TareaSerializer(tarea).data)


class CumplimientoView(APIView):
    """RNF06: porcentaje de avisos dentro de +-5 min de la hora programada."""

    permission_classes = ADMIN

    def get(self, request):
        params = request.query_params
        until = bogota_day_bounds(parse_day(params["hasta"]))[1] if params.get("hasta") else timezone.now()
        since = (
            bogota_day_bounds(parse_day(params["desde"]))[0]
            if params.get("desde")
            else until - timedelta(days=30)
        )
        return Response(services.punctuality(since, until))
