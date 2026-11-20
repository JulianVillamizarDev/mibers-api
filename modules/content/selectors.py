from collections import defaultdict
from datetime import date, timedelta

from django.db.models import Count
from django.utils import timezone

from modules.clients.models import Cliente
from modules.core.timezone import bogota_day_bounds, now_bogota, to_bogota

from .models import Estado, Pieza

PUBLISHED_STATES = (Estado.PUBLICADA, Estado.COMPARTIDA)


def pieces_queryset():
    return Pieza.objects.select_related("cliente", "responsable").prefetch_related("redes", "archivos")


def week_bounds(day: date) -> tuple[date, date]:
    """Lunes (inclusive) a lunes siguiente (exclusive)."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=7)


def month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end


def client_schedule(cliente: Cliente, *, view: str, day: date, red=None, estado=None) -> dict:
    """Cronograma semanal o mensual de un cliente, agrupado por dia de Colombia (RF20)."""
    start, end = week_bounds(day) if view == "semana" else month_bounds(day)
    low, high = bogota_day_bounds(start)[0], bogota_day_bounds(end)[0]
    queryset = pieces_queryset().filter(cliente=cliente, programada_para__gte=low, programada_para__lt=high)
    if red:
        queryset = queryset.filter(redes__red=red).distinct()
    if estado:
        queryset = queryset.filter(estado=estado)
    by_day: dict[date, list[Pieza]] = defaultdict(list)
    for pieza in queryset.order_by("programada_para"):
        by_day[to_bogota(pieza.programada_para).date()].append(pieza)
    days = []
    cursor = start
    while cursor < end:
        days.append({"fecha": cursor, "piezas": by_day.get(cursor, [])})
        cursor += timedelta(days=1)
    return {"vista": view, "desde": start, "hasta": end - timedelta(days=1), "dias": days}


def current_companies(*, upcoming_limit: int = 5, now=None) -> list[dict]:
    """ "Empresas actuales" (RF5): clientes activos con proximas publicaciones y pendientes."""
    now = now or timezone.now()
    clients = list(Cliente.objects.filter(activo=True).order_by("nombre"))
    ids = [c.pk for c in clients]

    counts: dict = defaultdict(dict)
    for row in (
        Pieza.objects.filter(cliente_id__in=ids)
        .values("cliente_id", "estado")
        .annotate(n=Count("id"))
        .order_by()
    ):
        counts[row["cliente_id"]][row["estado"]] = row["n"]

    upcoming: dict = defaultdict(list)
    future = (
        pieces_queryset()
        .filter(cliente_id__in=ids, programada_para__gte=now)
        .exclude(estado__in=PUBLISHED_STATES)
        .order_by("programada_para")
    )
    for pieza in future:
        if len(upcoming[pieza.cliente_id]) < upcoming_limit:
            upcoming[pieza.cliente_id].append(pieza)

    week_start, week_end = week_bounds(now_bogota().date())
    low, high = bogota_day_bounds(week_start)[0], bogota_day_bounds(week_end)[0]
    scheduled_states = (Estado.PROGRAMADA, Estado.PUBLICADA, Estado.COMPARTIDA, Estado.FALLIDA)
    this_week = dict(
        Pieza.objects.filter(
            cliente_id__in=ids,
            programada_para__gte=low,
            programada_para__lt=high,
            estado__in=scheduled_states,
        )
        .values_list("cliente_id")
        .annotate(n=Count("id"))
        .order_by()
    )

    result = []
    for cliente in clients:
        c = counts[cliente.pk]
        result.append(
            {
                "cliente": cliente,
                "proximas_publicaciones": upcoming[cliente.pk],
                "pendientes": {
                    "borradores": c.get(Estado.BORRADOR, 0),
                    "en_revision": c.get(Estado.EN_REVISION, 0),
                    "cambios_solicitados": c.get(Estado.CAMBIOS_SOLICITADOS, 0),
                    "aprobadas_sin_programar": c.get(Estado.APROBADA, 0),
                    "fallidas": c.get(Estado.FALLIDA, 0),
                },
                "semana_actual": {
                    "plan": cliente.posts_por_semana,
                    "programadas": this_week.get(cliente.pk, 0),
                },
            }
        )
    return result
