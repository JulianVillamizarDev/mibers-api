import re
from datetime import date

from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.clients.models import Cliente
from modules.core.exceptions import DomainError
from modules.core.networks import Network
from modules.core.pagination import StandardPagination
from modules.core.permissions import IsAdmin, IsStaffOrAdmin

from . import services
from .models import MetricaMensual

try:
    from drf_spectacular.utils import extend_schema
except ImportError:  # drf-spectacular es opcional en local

    def extend_schema(*args, **kwargs):
        return lambda view: view


STAFF = [*APIView.permission_classes, IsStaffOrAdmin]
ADMIN = [*APIView.permission_classes, IsAdmin]
MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class MonthField(serializers.Field):
    """Mes como 'AAAA-MM' (se guarda el primer dia del mes)."""

    def to_internal_value(self, data):
        match = MONTH_RE.match(str(data))
        if not match:
            raise serializers.ValidationError("Usa el formato AAAA-MM, por ejemplo 2026-03.")
        return date(int(match.group(1)), int(match.group(2)), 1)

    def to_representation(self, value):
        return f"{value:%Y-%m}"


class MetricaSerializer(serializers.ModelSerializer):
    mes = MonthField()
    cliente_nombre = serializers.CharField(source="cliente.nombre", read_only=True)
    registrado_por_username = serializers.CharField(
        source="registrado_por.username", default="", read_only=True
    )

    class Meta:
        model = MetricaMensual
        fields = [
            "id",
            "cliente",
            "cliente_nombre",
            "red",
            "mes",
            "alcance",
            "visualizaciones",
            "interacciones",
            "seguidores",
            "origen",
            "sincronizado_at",
            "registrado_por_username",
            "updated_at",
        ]
        read_only_fields = fields


class SyncMetricasSerializer(serializers.Serializer):
    mes = MonthField(required=False)


class MetricaInputSerializer(serializers.Serializer):
    red = serializers.ChoiceField(choices=Network.choices)
    mes = MonthField()
    alcance = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    visualizaciones = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    interacciones = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    seguidores = serializers.IntegerField(min_value=0, required=False, allow_null=True)

    def validate_mes(self, value):
        today = date.today()
        if value > today.replace(day=1):
            raise serializers.ValidationError("No se pueden registrar metricas de meses futuros.")
        return value


def parse_month(value: str) -> date:
    try:
        return MonthField().to_internal_value(value)
    except serializers.ValidationError as exc:
        raise DomainError("Usa el formato AAAA-MM.", "invalid_month") from exc


class MetricaListView(APIView):
    """Metricas por cliente, red y mes (RF23)."""

    permission_classes = STAFF

    def get(self, request):
        params = request.query_params
        queryset = MetricaMensual.objects.select_related("cliente", "registrado_por")
        if params.get("cliente"):
            queryset = queryset.filter(cliente_id=params["cliente"])
        if params.get("red") in Network.values:
            queryset = queryset.filter(red=params["red"])
        if params.get("desde"):
            queryset = queryset.filter(mes__gte=parse_month(params["desde"]))
        if params.get("hasta"):
            queryset = queryset.filter(mes__lte=parse_month(params["hasta"]))
        paginator = StandardPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(MetricaSerializer(page, many=True).data)


class ClienteMetricasView(APIView):
    permission_classes = STAFF

    def put(self, request, pk):
        """Registra (o corrige) las metricas de un mes para una red del cliente."""
        cliente = get_object_or_404(Cliente, pk=pk)
        data = MetricaInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        metrica, created = services.upsert_metric(
            cliente,
            red=values.pop("red"),
            mes=values.pop("mes"),
            acting_user=request.user,
            request=request,
            **values,
        )
        return Response(
            MetricaSerializer(metrica).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )


class ClienteMetricasSyncView(APIView):
    """Sincroniza desde Meta las metricas del cliente (solo Administrador)."""

    permission_classes = ADMIN

    @extend_schema(request=SyncMetricasSerializer, responses=MetricaSerializer(many=True))
    def post(self, request, pk):
        cliente = get_object_or_404(Cliente, pk=pk)
        data = SyncMetricasSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        result = services.sync_month(
            cliente=cliente,
            mes=data.validated_data.get("mes"),
            acting_user=request.user,
        )
        return Response(MetricaSerializer(result["metricas"], many=True).data)
