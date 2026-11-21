import django_filters
from rest_framework import mixins, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from modules.core.permissions import IsAdmin
from modules.core.timezone import bogota_day_bounds

from .models import Action, AuditLog
from .serializers import AuditLogSerializer


class AuditLogFilter(django_filters.FilterSet):
    # Fechas como dia calendario de Colombia (YYYY-MM-DD), ambas inclusivas.
    desde = django_filters.DateFilter(method="filter_desde")
    hasta = django_filters.DateFilter(method="filter_hasta")

    class Meta:
        model = AuditLog
        fields = {"actor": ["exact"], "action": ["exact"], "entity_type": ["exact"], "entity_id": ["exact"]}

    def filter_desde(self, queryset, name, value):
        return queryset.filter(created_at__gte=bogota_day_bounds(value)[0])

    def filter_hasta(self, queryset, name, value):
        return queryset.filter(created_at__lt=bogota_day_bounds(value)[1])


class AuditLogViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Solo lectura y solo Administrador (RF4). No expone ninguna escritura (RNF11)."""

    serializer_class = AuditLogSerializer
    queryset = AuditLog.objects.all()
    permission_classes = [*viewsets.GenericViewSet.permission_classes, IsAdmin]
    filterset_class = AuditLogFilter


@api_view(["GET"])
@permission_classes([IsAdmin])
def audit_actions(request):
    """Catalogo de acciones para poblar filtros en el SPA."""
    return Response([{"value": value, "label": label} for value, label in Action.choices])
