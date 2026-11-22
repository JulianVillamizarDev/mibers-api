"""Consulta del registro de envios al cliente por WhatsApp (HU-19, RF18: "registrando si se entrego").

Solo lectura: los envios los crea `services.share_with_client`, los envia el worker y los estados
de entrega los actualiza el webhook (`notifications.services.apply_status_update`).
"""

from uuid import UUID

from django.db.models import QuerySet

from modules.content.models import Pieza
from modules.content.views import parse_day
from modules.core.exceptions import DomainError
from modules.core.timezone import bogota_day_bounds
from modules.notifications.models import EnvioWhatsApp

from .serializers import EnvioSerializer, mask_number  # noqa: F401 - reexportado

ENTITY_TYPE = "content.pieza"


def client_envios() -> QuerySet:
    """Solo los envios de contenido a clientes (no alertas al admin ni recordatorios al personal)."""
    return EnvioWhatsApp.objects.filter(proposito=EnvioWhatsApp.Proposito.CLIENTE, entity_type=ENTITY_TYPE)


def _parse_uuid(value: str, name: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise DomainError(f"'{name}' debe ser un UUID valido.", "invalid_id") from exc


def filter_envios(queryset: QuerySet, params) -> QuerySet:
    """Filtros opcionales: cliente, pieza, estado, desde y hasta (dia de Bogota, inclusivo)."""
    if params.get("estado"):
        if params["estado"] not in EnvioWhatsApp.Estado.values:
            raise DomainError(
                f"estado debe ser uno de: {', '.join(EnvioWhatsApp.Estado.values)}.", "invalid_state"
            )
        queryset = queryset.filter(estado=params["estado"])
    if params.get("pieza"):
        # entity_id es texto: se compara con str(pk)
        queryset = queryset.filter(entity_id=str(_parse_uuid(params["pieza"], "pieza")))
    if params.get("cliente"):
        cliente_id = _parse_uuid(params["cliente"], "cliente")
        piece_ids = Pieza.objects.filter(cliente_id=cliente_id).values_list("pk", flat=True)
        queryset = queryset.filter(entity_id__in=[str(pk) for pk in piece_ids])
    if params.get("desde"):
        queryset = queryset.filter(created_at__gte=bogota_day_bounds(parse_day(params["desde"]))[0])
    if params.get("hasta"):
        queryset = queryset.filter(created_at__lt=bogota_day_bounds(parse_day(params["hasta"]))[1])
    return queryset


def serialize_envios(envios) -> list[dict]:
    """Serializa una lista de envios resolviendo todas las piezas en UNA consulta (sin N+1)."""
    envios = list(envios)
    piece_ids = {e.entity_id for e in envios}
    pieces = {str(p.pk): p for p in Pieza.objects.filter(pk__in=piece_ids).select_related("cliente")}
    return EnvioSerializer(envios, many=True, context={"piezas": pieces}).data
