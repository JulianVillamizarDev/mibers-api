from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.core.files import serve_file
from modules.core.pagination import StandardPagination
from modules.core.permissions import IsAdmin, IsStaffOrAdmin

from . import services
from .models import Cliente, ContactoWhatsApp, CuentaSocial
from .serializers import (
    ClienteSerializer,
    ClienteWriteSerializer,
    ContactoCreateSerializer,
    ContactoSerializer,
    ContactoUpdateSerializer,
    CuentaSocialCreateSerializer,
    CuentaSocialSerializer,
    CuentaSocialUpdateSerializer,
)

BASE_PERMISSIONS = [*APIView.permission_classes, IsStaffOrAdmin]
ADMIN_PERMISSIONS = [*APIView.permission_classes, IsAdmin]


def clients_queryset():
    return Cliente.objects.prefetch_related("contactos", "cuentas")


def get_client(pk) -> Cliente:
    return get_object_or_404(clients_queryset(), pk=pk)


class ClienteListCreateView(APIView):
    permission_classes = BASE_PERMISSIONS
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        queryset = clients_queryset()
        params = request.query_params
        if params.get("activo") in ("true", "false"):
            queryset = queryset.filter(activo=params["activo"] == "true")
        if params.get("estrategia"):
            queryset = queryset.filter(estrategia=params["estrategia"])
        if params.get("search"):
            queryset = queryset.filter(nombre__icontains=params["search"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(ClienteSerializer(page, many=True).data)

    def post(self, request):
        data = ClienteWriteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        cliente = services.create_client(acting_user=request.user, request=request, **data.validated_data)
        return Response(ClienteSerializer(get_client(cliente.pk)).data, status=status.HTTP_201_CREATED)


class ClienteDetailView(APIView):
    permission_classes = BASE_PERMISSIONS
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request, pk):
        return Response(ClienteSerializer(get_client(pk)).data)

    def patch(self, request, pk):
        cliente = get_client(pk)
        data = ClienteWriteSerializer(cliente, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        services.update_client(cliente, acting_user=request.user, request=request, **data.validated_data)
        return Response(ClienteSerializer(get_client(pk)).data)




class ManualMarcaView(APIView):
    permission_classes = BASE_PERMISSIONS

    def get(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        return serve_file(cliente.manual_marca, cliente.manual_marca_nombre or None)


# --- Contactos de WhatsApp ----------------------------------------------------




# --- Cuentas sociales ----------------------------------------------------------


