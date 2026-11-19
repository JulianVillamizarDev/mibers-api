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
