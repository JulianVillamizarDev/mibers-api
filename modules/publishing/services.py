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
def _block(pieza: Pieza, reason: str, now) -> None:
    """RF15: una pieza sin aprobacion (o sin condiciones) NO se publica: se bloquea y se alerta."""
    key = f"bloqueo:{pieza.pk}:{pieza.programada_para.isoformat()}"
    alerta = notifications.send_alert(
        Alerta.Tipo.PUBLICACION_BLOQUEADA,
        f"{pieza.cliente.nombre} - {pieza.get_tipo_display()} del {_fmt(pieza.programada_para)}: {reason}",
        entity_type="content.pieza",
        entity_id=pieza.pk,
        dedupe_key=key,
    )
    if alerta is not None:  # solo la primera vez
        audit.record(
            Action.PUBLICATION_BLOCKED,
            actor_username="sistema",
            entity=pieza,
            metadata={"estado": pieza.estado, "motivo": reason},
        )


def _generate_tasks(pieza: Pieza, now) -> list[TareaPublicacion]:
    accounts = {c.red: c for c in CuentaSocial.objects.filter(cliente_id=pieza.cliente_id)}
    tasks = []
    for red in pieza.network_list():
        account = accounts[red]
        assert account.cliente_id == pieza.cliente_id  # RNF09
        task, _ = TareaPublicacion.objects.update_or_create(
            pieza=pieza,
            red=red,
            defaults={
                "cuenta": account,
                "cuenta_handle": account.handle,
                "cuenta_url": account.url_perfil,
                "estado": Estado_T.PENDIENTE,
                "copy_final": pieza.copy_final(red),
                "programada_para": pieza.programada_para,
                "avisada_at": now,
                "atraso_alertado_at": None,
                "enlace": "",
                "motivo": "",
                "publicada_at": None,
                "modo": TareaPublicacion.Modo.MANUAL,
                "externo_contenedor_id": "",
                "externo_post_id": "",
                "externo_estado": "",
            },
        )
        tasks.append(task)
    return tasks


def process_due_piece(pieza: Pieza, now) -> None:
    pieza = content.load_piece(pieza.pk)
    if pieza.estado != Estado.PROGRAMADA:
        names = {
            Estado.APROBADA: "esta aprobada pero no fue programada",
            Estado.BORRADOR: "sigue en borrador",
            Estado.EN_REVISION: "no fue aprobada (sigue en revision)",
            Estado.CAMBIOS_SOLICITADOS: "no fue aprobada (tiene cambios solicitados)",
        }
        _block(pieza, f"No se publico: {names.get(pieza.estado, 'no esta lista')}.", now)
        return
    problems = content.readiness_problems(pieza) + _account_problems(pieza)
    if problems:
        _block(pieza, "No se puede publicar: " + " ".join(problems), now)
        return
    with transaction.atomic():
        tasks = _generate_tasks(pieza, now)
        audit.record(
            Action.PUBLICATION_DUE,
            actor_username="sistema",
            entity=pieza,
            metadata={
                "redes": [t.red for t in tasks],
                "retraso_s": int((now - pieza.programada_para).total_seconds()),
            },
        )
    for task in tasks:
        get_publisher(task.red, task.cuenta).dispatch(task)


def run_tick(now=None) -> dict:
    """Se ejecuta cada minuto por el worker. Crea las tareas de publicacion de las piezas
    programadas cuya hora llego, bloquea las no aprobadas y alerta de las tareas atrasadas."""
    now = now or timezone.now()
    active_tasks = TareaPublicacion.objects.filter(pieza=OuterRef("pk")).exclude(estado=Estado_T.CANCELADA)
    due = (
        Pieza.objects.filter(
            programada_para__lte=now, programada_para__gte=now - LOOKBACK, estado__in=NOT_YET_PUBLISHED
        )
        .annotate(tiene_tareas=Exists(active_tasks))
        .filter(tiene_tareas=False)
        .select_related("cliente")
    )
    processed = 0
    for pieza in due:
        try:
            process_due_piece(pieza, now)
            processed += 1
        except Exception:  # noqa: BLE001 - una pieza con problemas no frena a las demas
            logger.exception("Fallo el scheduler con la pieza %s", pieza.pk)
    return {"piezas": processed, "atrasadas": alert_overdue(now)}


def alert_overdue(now=None) -> int:
    """Tarea pendiente sin confirmar pasado el plazo: se avisa una sola vez al administrador."""
    now = now or timezone.now()
    limit = now - timedelta(minutes=settings.PUBLICATION_OVERDUE_MINUTES)
    overdue = TareaPublicacion.objects.filter(
        estado=Estado_T.PENDIENTE, programada_para__lte=limit, atraso_alertado_at__isnull=True
    ).select_related("pieza__cliente")
    count = 0
    for task in overdue:
        notifications.send_alert(
            Alerta.Tipo.PUBLICACION_ATRASADA,
            f"{task.pieza.cliente.nombre} - {task.get_red_display()} "
            f"(programada {_fmt(task.programada_para)}) sigue sin publicarse.",
            entity_type="publishing.tareapublicacion",
            entity_id=task.pk,
            dedupe_key=f"atraso:{task.pk}:{task.intentos}",
        )
        task.atraso_alertado_at = now
        task.save(update_fields=["atraso_alertado_at", "updated_at"])
        count += 1
    return count


# --- Resultado por red y reintento (RF17) ---










def cancel_pending_tasks(pieza: Pieza) -> int:
    """La pieza volvio a revision o se des-programo: sus tareas sin resultado quedan canceladas."""
    return pieza.tareas.filter(estado__in=[Estado_T.PENDIENTE, Estado_T.FALLIDA]).update(
        estado=Estado_T.CANCELADA, updated_at=timezone.now()
    )


# --- Envio al cliente por WhatsApp (RF18) ---




# --- Cumplimiento de horario (RNF06) ---
