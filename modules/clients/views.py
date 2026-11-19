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


class ClienteStateView(APIView):
    """Desactivar / reactivar (RF5)."""

    permission_classes = BASE_PERMISSIONS
    activate = False

    def post(self, request, pk):
        cliente = get_client(pk)
        action = services.reactivate_client if self.activate else services.deactivate_client
        action(cliente, acting_user=request.user, request=request)
        return Response(ClienteSerializer(get_client(pk)).data)


class ManualMarcaView(APIView):
    permission_classes = BASE_PERMISSIONS

    def get(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        return serve_file(cliente.manual_marca, cliente.manual_marca_nombre or None)


# --- Contactos de WhatsApp ----------------------------------------------------
class ContactoListCreateView(APIView):
    permission_classes = BASE_PERMISSIONS

    def get(self, request, pk):
        cliente = get_client(pk)
        return Response(ContactoSerializer(cliente.contactos.all(), many=True).data)

    def post(self, request, pk):
        cliente = get_client(pk)
        data = ContactoCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        contacto = services.add_contact(
            cliente, acting_user=request.user, request=request, **data.validated_data
        )
        return Response(ContactoSerializer(contacto).data, status=status.HTTP_201_CREATED)


class ContactoDetailView(APIView):
    permission_classes = BASE_PERMISSIONS

    def _get(self, pk, contacto_id):
        return get_object_or_404(
            ContactoWhatsApp.objects.select_related("cliente"), pk=contacto_id, cliente_id=pk
        )

    def patch(self, request, pk, contacto_id):
        contacto = self._get(pk, contacto_id)
        data = ContactoUpdateSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        services.update_contact(contacto, acting_user=request.user, request=request, **data.validated_data)
        return Response(ContactoSerializer(contacto).data)

    def delete(self, request, pk, contacto_id):
        services.remove_contact(self._get(pk, contacto_id), acting_user=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- Cuentas sociales ----------------------------------------------------------
class CuentaListCreateView(APIView):
    def get_permissions(self):
        # Leer: cualquier usuario interno. Registrar: solo Administrador (RF3, RF7).
        classes = ADMIN_PERMISSIONS if self.request.method == "POST" else BASE_PERMISSIONS
        return [permission() for permission in classes]

    def get(self, request, pk):
        cliente = get_client(pk)
        return Response(CuentaSocialSerializer(cliente.cuentas.all(), many=True).data)

    def post(self, request, pk):
        cliente = get_client(pk)
        data = CuentaSocialCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        cuenta = services.link_account(
            cliente, acting_user=request.user, request=request, **data.validated_data
        )
        return Response(CuentaSocialSerializer(cuenta).data, status=status.HTTP_201_CREATED)


class CuentaDetailView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    def patch(self, request, pk, cuenta_id):
        cuenta = get_object_or_404(CuentaSocial, pk=cuenta_id, cliente_id=pk)
        data = CuentaSocialUpdateSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        services.update_account(cuenta, acting_user=request.user, request=request, **data.validated_data)
        return Response(CuentaSocialSerializer(cuenta).data)
