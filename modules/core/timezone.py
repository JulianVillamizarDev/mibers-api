"""Utilidades de hora de Colombia (America/Bogota, UTC-5, sin horario de verano)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

BOGOTA = ZoneInfo("America/Bogota")


def now_bogota() -> datetime:
    return timezone.now().astimezone(BOGOTA)


def to_bogota(value: datetime) -> datetime:
    return value.astimezone(BOGOTA)


def bogota_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Inicio (inclusive) y fin (exclusive) de un dia calendario de Colombia."""
    start = datetime.combine(day, time.min, tzinfo=BOGOTA)
    return start, start + timedelta(days=1)
