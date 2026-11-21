from datetime import date

from modules.audit import services as audit
from modules.audit.models import Action

from .models import MetricaMensual

FIELDS = ("alcance", "visualizaciones", "interacciones", "seguidores")


def upsert_metric(
    cliente, *, red: str, mes: date, acting_user, request=None, **values
) -> tuple[MetricaMensual, bool]:
    """Crea o actualiza la metrica del mes. Devuelve (metrica, creada)."""
    mes = mes.replace(day=1)
    metrica, created = MetricaMensual.objects.get_or_create(
        cliente=cliente, red=red, mes=mes, defaults={**values, "registrado_por": acting_user}
    )
    if not created:
        for name, value in values.items():
            setattr(metrica, name, value)
        metrica.registrado_por = acting_user
        metrica.save()
    audit.record(
        Action.METRICS_CHANGED,
        actor=acting_user,
        entity=metrica,
        metadata={"cliente": str(cliente.pk), "red": red, "mes": f"{mes:%Y-%m}", "creada": created},
        request=request,
    )
    return metrica, created
