import calendar
import logging
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from modules.audit import services as audit
from modules.audit.models import Action
from modules.clients.meta import GraphError, get_graph_client, page_token
from modules.clients.models import CuentaSocial
from modules.core.exceptions import DomainError
from modules.core.networks import Network

from .models import MetricaMensual

FIELDS = ("alcance", "visualizaciones", "interacciones", "seguidores")
INSIGHTS_MAX_DAYS = 30
IG_INSIGHTS = {
    "visualizaciones": "views",
    "alcance": "reach",
    "interacciones": "total_interactions",
}
FB_INSIGHTS = {
    "visualizaciones": "page_media_view",
    "alcance": "page_total_media_view_unique",
    "interacciones": "page_post_engagements",
}
logger = logging.getLogger(__name__)


def upsert_metric(
    cliente, *, red: str, mes: date, acting_user, request=None, **values
) -> tuple[MetricaMensual, bool]:
    """Crea o actualiza una metrica MANUAL del mes. Devuelve (metrica, creada)."""
    mes = mes.replace(day=1)
    defaults = {
        **values,
        "registrado_por": acting_user,
        "origen": MetricaMensual.Origen.MANUAL,
        "sincronizado_at": None,
    }
    metrica, created = MetricaMensual.objects.get_or_create(
        cliente=cliente, red=red, mes=mes, defaults=defaults
    )
    if not created:
        for name, value in defaults.items():
            setattr(metrica, name, value)
        metrica.save()
    audit.record(
        Action.METRICS_CHANGED,
        actor=acting_user,
        entity=metrica,
        metadata={"cliente": str(cliente.pk), "red": red, "mes": f"{mes:%Y-%m}", "creada": created},
        request=request,
    )
    return metrica, created


def _month_start(value=None) -> date:
    if value is None:
        return timezone.localdate().replace(day=1)
    return value.replace(day=1)


def _month_end(mes: date) -> date:
    return mes.replace(day=calendar.monthrange(mes.year, mes.month)[1])


def _month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows = []
    current = start
    while current <= end:
        window_end = min(current + timedelta(days=INSIGHTS_MAX_DAYS - 1), end)
        windows.append((current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def _int_value(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _insight_value(payload: dict) -> int | None:
    data = payload.get("data") or []
    if not data:
        return 0  # una ventana sin actividad o sin datos no invalida las demas
    node = data[0]
    total = node.get("total_value")
    if isinstance(total, dict) and "value" in total:
        return _int_value(total["value"])
    values = node.get("values") or []
    numbers = [_int_value(item.get("value")) for item in values if "value" in item]
    numbers = [number for number in numbers if number is not None]
    return sum(numbers) if numbers else None


def _safe_metric(summary: dict, label: str, function):
    try:
        return function()
    except GraphError as exc:
        summary["errores"] += 1
        logger.warning("Meta no entrego la metrica %s: %s", label, exc)
        return None


def _insights_sum(
    client,
    path: str,
    metric: str,
    start: date,
    end: date,
    *,
    token: str | None = None,
    total_value: bool = False,
) -> int | None:
    total = 0
    for window_start, window_end in _month_windows(start, end):
        params = {
            "metric": metric,
            "period": "day",
            "since": window_start.isoformat(),
            "until": window_end.isoformat(),
        }
        if total_value:
            params["metric_type"] = "total_value"
        payload = client.get(path, params, token=token)
        value = _insight_value(payload)
        if value is None:
            return None
        total += value
    return total


def _instagram_values(client, cuenta: CuentaSocial, start: date, end: date, summary: dict) -> dict:
    account_id = cuenta.id_externo
    values = {
        "seguidores": _safe_metric(
            summary,
            "Instagram followers_count",
            lambda: _int_value(
                client.get(f"/{account_id}", {"fields": "followers_count"}).get("followers_count")
            ),
        )
    }
    for field, metric in IG_INSIGHTS.items():
        values[field] = _safe_metric(
            summary,
            f"Instagram {metric}",
            lambda metric=metric: _insights_sum(
                client,
                f"/{account_id}/insights",
                metric,
                start,
                end,
                total_value=True,
            ),
        )
    return values


def _facebook_values(client, cuenta: CuentaSocial, start: date, end: date, summary: dict) -> dict:
    account_id = cuenta.id_externo
    token = _safe_metric(summary, "token de pagina", lambda: page_token(client, account_id))
    if token is None:
        return {field: None for field in FIELDS}
    values = {
        "seguidores": _safe_metric(
            summary,
            "Facebook followers_count",
            lambda: _int_value(
                client.get(f"/{account_id}", {"fields": "followers_count"}, token=token).get(
                    "followers_count"
                )
            ),
        )
    }
    for field, metric in FB_INSIGHTS.items():
        values[field] = _safe_metric(
            summary,
            f"Facebook {metric}",
            lambda metric=metric: _insights_sum(
                client,
                f"/{account_id}/insights",
                metric,
                start,
                end,
                token=token,
            ),
        )
    return values


def _save_api_metric(
    cuenta: CuentaSocial,
    *,
    mes: date,
    values: dict,
    created: bool,
    forzar: bool,
    acting_user,
) -> MetricaMensual:
    metrica, _ = MetricaMensual.objects.update_or_create(
        cliente_id=cuenta.cliente_id,
        red=cuenta.red,
        mes=mes,
        defaults={
            **values,
            "origen": MetricaMensual.Origen.API,
            "sincronizado_at": timezone.now(),
            "registrado_por": None,
        },
    )
    audit.record(
        Action.METRICS_SYNCED,
        actor=acting_user,
        actor_username="sistema" if acting_user is None else "",
        entity=metrica,
        metadata={
            "cliente": str(cuenta.cliente_id),
            "red": cuenta.red,
            "mes": f"{mes:%Y-%m}",
            "creada": created,
            "forzada": forzar,
        },
    )
    return metrica


def empty_sync_result(mes: date) -> dict:
    return {
        "omitida": "",
        "mes": f"{mes:%Y-%m}",
        "revisadas": 0,
        "creadas": 0,
        "actualizadas": 0,
        "omitidas_manual": 0,
        "errores": 0,
        "metricas": [],
    }


def sync_month(cliente=None, mes=None, forzar=False, *, acting_user=None) -> dict:
    """Sincroniza las metricas de un mes para uno o todos los clientes.

    No sobrescribe filas MANUAL salvo `forzar=True`. Una metrica rechazada por Meta
    deja su campo vacio sin abortar el resto de la sincronizacion. El alcance de
    Instagram se suma por ventanas de fechas y puede ser aproximado.
    """
    mes = _month_start(mes)
    today = timezone.localdate()
    if mes > today.replace(day=1):
        raise DomainError("No se pueden sincronizar metricas de meses futuros.", "future_month")
    result = empty_sync_result(mes)
    if settings.META_PUBLISHING == "manual":
        result["omitida"] = "META_PUBLISHING=manual"
        return result
    if not settings.META_SYSTEM_USER_TOKEN:
        result["omitida"] = "sin META_SYSTEM_USER_TOKEN"
        return result

    accounts = CuentaSocial.objects.exclude(id_externo="").select_related("cliente")
    if cliente is not None:
        accounts = accounts.filter(cliente=cliente)
    accounts = list(accounts.order_by("cliente__nombre", "red"))
    result["revisadas"] = len(accounts)
    if not accounts:
        return result
    try:
        client = get_graph_client()
    except ImproperlyConfigured as exc:
        result["omitida"] = str(exc)
        return result

    start = mes
    end = min(_month_end(mes), today)
    current_month = today.replace(day=1)
    try:
        for cuenta in accounts:
            existing = MetricaMensual.objects.filter(
                cliente_id=cuenta.cliente_id, red=cuenta.red, mes=mes
            ).first()
            if existing and existing.origen == MetricaMensual.Origen.MANUAL and not forzar:
                result["omitidas_manual"] += 1
                continue
            if cuenta.red == Network.INSTAGRAM:
                values = _instagram_values(client, cuenta, start, end, result)
            else:
                values = _facebook_values(client, cuenta, start, end, result)
            if mes < current_month and existing and existing.seguidores is not None:
                values["seguidores"] = existing.seguidores
            metrica = _save_api_metric(
                cuenta,
                mes=mes,
                values=values,
                created=existing is None,
                forzar=forzar,
                acting_user=acting_user,
            )
            result["creadas" if existing is None else "actualizadas"] += 1
            result["metricas"].append(metrica)
    finally:
        client.close()
    return result


def sync_recent_months() -> dict:
    """Tarea diaria: sincroniza el mes en curso y el anterior."""
    current = timezone.localdate().replace(day=1)
    previous = (current - timedelta(days=1)).replace(day=1)
    return {"meses": [sync_month(mes=previous), sync_month(mes=current)]}
