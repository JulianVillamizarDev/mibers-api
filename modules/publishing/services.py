import logging
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from modules.audit import services as audit
from modules.audit.models import Action
from modules.clients.models import CuentaSocial
from modules.content import services as content
from modules.content.models import Estado, Pieza
from modules.core.exceptions import DomainError
from modules.core.networks import Network
from modules.core.timezone import to_bogota
from modules.notifications import services as notifications
from modules.notifications.models import Alerta, EnvioWhatsApp

from .models import TareaPublicacion
from .publishers import get_publisher

logger = logging.getLogger(__name__)

Estado_T = TareaPublicacion.Estado
LOOKBACK = timedelta(days=7)  # el scheduler solo atiende piezas cuya hora vencio hace menos de 7 dias
NOT_YET_PUBLISHED = (
    Estado.BORRADOR,
    Estado.EN_REVISION,
    Estado.CAMBIOS_SOLICITADOS,
    Estado.APROBADA,
    Estado.PROGRAMADA,
)
ALLOWED_LINK_HOSTS = {
    Network.INSTAGRAM: ("instagram.com",),
    Network.FACEBOOK: ("facebook.com", "fb.com", "fb.watch"),
}


def _fmt(moment) -> str:
    return to_bogota(moment).strftime("%d/%m/%Y %H:%M")


# --- Programacion con confirmacion (RF14, RNF09) ---
def _account_problems(pieza: Pieza) -> list[str]:
    """Cada red destino debe tener una cuenta VINCULADA del MISMO cliente (RNF09)."""
    problems = []
    accounts = {c.red: c for c in CuentaSocial.objects.filter(cliente_id=pieza.cliente_id)}
    for red in pieza.network_list():
        account = accounts.get(red)
        label = Network(red).label
        if account is None:
            problems.append(f"{label}: el cliente no tiene una cuenta registrada.")
        elif account.estado != CuentaSocial.Estado.VINCULADA:
            problems.append(
                f"{label}: la cuenta {account.handle} esta {account.get_estado_display().lower()}."
            )
    return problems


def schedule_preview(pieza: Pieza) -> dict:
    """Lo que se muestra antes de confirmar: cliente, cuentas destino y copy por red (RNF09)."""
    pieza = content.load_piece(pieza.pk)
    accounts = {c.red: c for c in CuentaSocial.objects.filter(cliente_id=pieza.cliente_id)}
    problems = content.readiness_problems(pieza) + _account_problems(pieza)
    if pieza.programada_para and pieza.programada_para <= timezone.now():
        problems.append("La fecha y hora de publicacion ya paso.")
    if pieza.estado not in (Estado.APROBADA, Estado.PROGRAMADA):
        problems.append("Solo se pueden programar piezas aprobadas.")
    return {
        "pieza": pieza,
        "cliente": pieza.cliente.nombre,
        "programada_para": pieza.programada_para,
        "redes": [
            {
                "red": red,
                "cuenta": accounts.get(red),
                "copy_final": pieza.copy_final(red),
            }
            for red in pieza.network_list()
        ],
        "archivos": [a.nombre_original for a in pieza.archivos.all()],
        "problemas": problems,
        "puede_confirmar": not problems,
    }


def schedule_piece(pieza: Pieza, *, acting_user, confirmed: bool, request=None) -> Pieza:
    if not confirmed:
        raise DomainError(
            "Revisa cliente, cuentas y copy por red y confirma la programacion.", "confirmation_required"
        )
    pieza = content.load_piece(pieza.pk)
    if pieza.estado != Estado.APROBADA:
        raise DomainError("Solo se pueden programar piezas aprobadas.", "not_approved", 409)
    problems = schedule_preview(pieza)["problemas"]
    if problems:
        raise DomainError(problems, "cannot_schedule")
    with transaction.atomic():
        content.change_state(pieza, Estado.PROGRAMADA, actor=acting_user)
        audit.record(
            Action.PIECE_SCHEDULED,
            actor=acting_user,
            entity=pieza,
            metadata={"programada_para": pieza.programada_para.isoformat(), "redes": pieza.network_list()},
            request=request,
        )
    return content.load_piece(pieza.pk)


def unschedule_piece(pieza: Pieza, *, acting_user, request=None) -> Pieza:
    pieza = content.load_piece(pieza.pk)
    if pieza.estado != Estado.PROGRAMADA:
        raise DomainError("La pieza no esta programada.", "not_scheduled", 409)
    if pieza.tareas.filter(estado=Estado_T.PUBLICADA).exists():
        raise DomainError("La pieza ya se publico en al menos una red.", "already_published", 409)
    with transaction.atomic():
        content.change_state(pieza, Estado.APROBADA, actor=acting_user)  # la senal cancela las tareas
        audit.record(Action.PIECE_UNSCHEDULED, actor=acting_user, entity=pieza, request=request)
    return content.load_piece(pieza.pk)


# --- Scheduler (RF15, RNF06) ---










# --- Resultado por red y reintento (RF17) ---










def cancel_pending_tasks(pieza: Pieza) -> int:
    """La pieza volvio a revision o se des-programo: sus tareas sin resultado quedan canceladas."""
    return pieza.tareas.filter(estado__in=[Estado_T.PENDIENTE, Estado_T.FALLIDA]).update(
        estado=Estado_T.CANCELADA, updated_at=timezone.now()
    )


# --- Envio al cliente por WhatsApp (RF18) ---




# --- Cumplimiento de horario (RNF06) ---
