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


def parse_day(value: str | None) -> date:
    if not value:
        return now_bogota().date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DomainError("La fecha debe tener formato AAAA-MM-DD.", "invalid_date") from exc


# --- Piezas ------------------------------------------------------------------------------------
class PiezaListCreateView(APIView):
    permission_classes = STAFF

    def get(self, request):
        params = request.query_params
        queryset = selectors.pieces_queryset()
        if params.get("cliente"):
            queryset = queryset.filter(cliente_id=params["cliente"])
        if params.get("estado"):
            queryset = queryset.filter(estado__in=params["estado"].split(","))
        if params.get("tipo") in Tipo.values:
            queryset = queryset.filter(tipo=params["tipo"])
        if params.get("red") in Network.values:
            queryset = queryset.filter(redes__red=params["red"]).distinct()
        if params.get("desde"):
            queryset = queryset.filter(programada_para__gte=bogota_day_bounds(parse_day(params["desde"]))[0])
        if params.get("hasta"):
            queryset = queryset.filter(programada_para__lt=bogota_day_bounds(parse_day(params["hasta"]))[1])
        if params.get("search"):
            queryset = queryset.filter(titulo__icontains=params["search"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(PiezaListSerializer(page, many=True).data)

    def post(self, request):
        data = PiezaWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        fields = dict(data.validated_data)
        if "cliente" not in fields or "tipo" not in fields:
            raise DomainError("cliente y tipo son obligatorios.", "validation_error")
        cliente = get_object_or_404(Cliente, pk=fields.pop("cliente"))
        pieza = services.create_piece(cliente=cliente, acting_user=request.user, request=request, **fields)
        return Response(PiezaSerializer(pieza).data, status=status.HTTP_201_CREATED)


class PiezaDetailView(APIView):
    permission_classes = STAFF

    def get(self, request, pk):
        return Response(PiezaSerializer(get_piece(pk)).data)

    def patch(self, request, pk):
        pieza = get_piece(pk)
        data = PiezaWriteSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        fields = dict(data.validated_data)
        if "cliente" in fields:
            raise DomainError("El cliente de una pieza no puede cambiarse.", "client_locked")
        pieza = services.update_piece(pieza, acting_user=request.user, request=request, **fields)
        return Response(PiezaSerializer(pieza).data)

    def delete(self, request, pk):
        services.delete_piece(get_piece(pk), acting_user=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


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
class EnviarRevisionView(APIView):
    permission_classes = STAFF

    def post(self, request, pk):
        pieza = services.submit_for_review(get_piece(pk), acting_user=request.user, request=request)
        return Response(PiezaSerializer(pieza).data)


class AprobarView(APIView):
    permission_classes = ADMIN

    def post(self, request, pk):
        data = ComentarioInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        pieza = services.approve(
            get_piece(pk),
            acting_user=request.user,
            comentario=data.validated_data["comentario"],
            request=request,
        )
        return Response(PiezaSerializer(pieza).data)


class SolicitarCambiosView(APIView):
    permission_classes = ADMIN

    def post(self, request, pk):
        data = ComentarioInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        pieza = services.request_changes(
            get_piece(pk),
            acting_user=request.user,
            comentario=data.validated_data["comentario"],
            request=request,
        )
        return Response(PiezaSerializer(pieza).data)


class RevisionesView(APIView):
    permission_classes = STAFF

    def get(self, request, pk):
        pieza = get_object_or_404(Pieza, pk=pk)
        return Response(ComentarioSerializer(pieza.revisiones.select_related("autor"), many=True).data)


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
