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
        get_publisher(task.red).dispatch(task)


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
def _validate_link(red: str, url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = ALLOWED_LINK_HOSTS[red]
    if parsed.scheme != "https" or not any(host == d or host.endswith("." + d) for d in allowed):
        raise DomainError(
            f"El enlace debe ser una URL https de {Network(red).label} ({', '.join(allowed)}).",
            "invalid_link",
        )


def _refresh_piece_state(pieza_id, actor) -> Pieza:
    pieza = content.load_piece(pieza_id)
    tasks = list(pieza.tareas.exclude(estado=Estado_T.CANCELADA))
    published = any(t.estado == Estado_T.PUBLICADA for t in tasks)
    pending = any(t.estado == Estado_T.PENDIENTE for t in tasks)
    if published:
        if pieza.estado in (Estado.PROGRAMADA, Estado.FALLIDA):
            content.change_state(pieza, Estado.PUBLICADA, actor=actor)
            share_with_client(pieza)
    elif tasks and not pending and all(t.estado == Estado_T.FALLIDA for t in tasks):
        if pieza.estado == Estado.PROGRAMADA:
            content.change_state(pieza, Estado.FALLIDA, actor=actor)
    elif pending and pieza.estado == Estado.FALLIDA:
        content.change_state(pieza, Estado.PROGRAMADA, actor=actor)
    return pieza


def confirm_published(tarea: TareaPublicacion, *, enlace: str, acting_user, request=None) -> TareaPublicacion:
    if tarea.estado not in (Estado_T.PENDIENTE, Estado_T.FALLIDA):
        raise DomainError("Esta publicacion no esta pendiente.", "invalid_task_state", 409)
    _validate_link(tarea.red, enlace)
    with transaction.atomic():
        tarea.estado = Estado_T.PUBLICADA
        tarea.enlace = enlace
        tarea.motivo = ""
        tarea.publicada_at = timezone.now()
        tarea.gestionada_por = acting_user
        tarea.save()
        audit.record(
            Action.PUBLICATION_PUBLISHED,
            actor=acting_user,
            entity=tarea,
            metadata={"pieza": str(tarea.pieza_id), "red": tarea.red, "enlace": enlace},
            request=request,
        )
        _refresh_piece_state(tarea.pieza_id, acting_user)
    return tarea


def report_failed(tarea: TareaPublicacion, *, motivo: str, acting_user, request=None) -> TareaPublicacion:
    if tarea.estado != Estado_T.PENDIENTE:
        raise DomainError(
            "Solo se puede reportar el fallo de una publicacion pendiente.", "invalid_task_state", 409
        )
    if not motivo or not motivo.strip():
        raise DomainError("Indica el motivo del fallo.", "reason_required")
    with transaction.atomic():
        tarea.estado = Estado_T.FALLIDA
        tarea.motivo = motivo.strip()
        tarea.gestionada_por = acting_user
        tarea.save()
        audit.record(
            Action.PUBLICATION_FAILED,
            actor=acting_user,
            entity=tarea,
            metadata={"pieza": str(tarea.pieza_id), "red": tarea.red, "motivo": tarea.motivo},
            request=request,
        )
        _refresh_piece_state(tarea.pieza_id, acting_user)
    pieza = tarea.pieza
    notifications.send_alert(
        Alerta.Tipo.PUBLICACION_FALLIDA,
        f"{pieza.cliente.nombre} - {tarea.get_red_display()}: {tarea.motivo}",
        entity_type="publishing.tareapublicacion",
        entity_id=tarea.pk,
        dedupe_key=f"fallo:{tarea.pk}:{tarea.intentos}",
    )
    return tarea


def retry(tarea: TareaPublicacion, *, acting_user, request=None) -> TareaPublicacion:
    if tarea.estado != Estado_T.FALLIDA:
        raise DomainError("Solo se pueden reintentar las publicaciones fallidas.", "invalid_task_state", 409)
    account = tarea.cuenta
    if account.estado != CuentaSocial.Estado.VINCULADA:
        raise DomainError(
            f"La cuenta {account.handle} esta {account.get_estado_display().lower()}: "
            "corrige su estado primero.",
            "account_not_linked",
        )
    with transaction.atomic():
        tarea.estado = Estado_T.PENDIENTE
        tarea.intentos += 1
        tarea.motivo = ""
        tarea.atraso_alertado_at = None
        tarea.gestionada_por = acting_user
        tarea.save()
        audit.record(
            Action.PUBLICATION_RETRIED,
            actor=acting_user,
            entity=tarea,
            metadata={"pieza": str(tarea.pieza_id), "red": tarea.red, "intento": tarea.intentos},
            request=request,
        )
        _refresh_piece_state(tarea.pieza_id, acting_user)
    get_publisher(tarea.red).dispatch(tarea)
    return tarea


def cancel_pending_tasks(pieza: Pieza) -> int:
    """La pieza volvio a revision o se des-programo: sus tareas sin resultado quedan canceladas."""
    return pieza.tareas.filter(estado__in=[Estado_T.PENDIENTE, Estado_T.FALLIDA]).update(
        estado=Estado_T.CANCELADA, updated_at=timezone.now()
    )


# --- Envio al cliente por WhatsApp (RF18) ---
def share_with_client(pieza: Pieza) -> list[EnvioWhatsApp]:
    """Encola el archivo y el mensaje para los contactos AUTORIZADOS del cliente. Se llama al
    confirmarse la publicacion en al menos una red; sale el dia de la publicacion, a la hora
    programada (o de inmediato si ya paso)."""
    already = EnvioWhatsApp.objects.filter(
        entity_type="content.pieza", entity_id=str(pieza.pk), proposito=EnvioWhatsApp.Proposito.CLIENTE
    ).exists()
    if already:
        return []
    contacts = list(pieza.cliente.contactos.all())
    authorized = [c for c in contacts if c.autorizado]  # RNF13: sin autorizacion no se envia nada
    if not authorized:
        notifications.send_alert(
            Alerta.Tipo.ENVIO_CLIENTE_FALLIDO,
            f"{pieza.cliente.nombre}: no hay contactos de WhatsApp autorizados para enviarle "
            "la pieza publicada.",
            entity_type="content.pieza",
            entity_id=pieza.pk,
            dedupe_key=f"sin_contactos:{pieza.pk}",
        )
        return []
    files = list(pieza.archivos.all())
    message = (
        pieza.mensaje_cliente.strip() or f"Tu {pieza.get_tipo_display().lower()} de hoy ya esta publicado."
    )
    run_at = max(pieza.programada_para, timezone.now())
    queued = []
    for contact in authorized:
        for index, archivo in enumerate(files):
            queued.append(
                notifications.queue_message(
                    proposito=EnvioWhatsApp.Proposito.CLIENTE,
                    destino=contact.numero,
                    destino_nombre=contact.nombre,
                    parametros=[message if index == 0 else f"Archivo {index + 1} de {len(files)}"],
                    media_ruta=archivo.archivo.name,
                    media_nombre=archivo.nombre_original,
                    media_kind=archivo.kind,
                    entity_type="content.pieza",
                    entity_id=pieza.pk,
                    contexto={"contacto": str(contact.pk), "archivo": str(archivo.pk)},
                    run_at=run_at,
                )
            )
    return queued


def refresh_shared_state(envio: EnvioWhatsApp) -> None:
    """Cuando todos los envios al cliente salieron, la pieza pasa a Compartida (RF10, RF18)."""
    if envio.proposito != EnvioWhatsApp.Proposito.CLIENTE or envio.entity_type != "content.pieza":
        return
    envios = EnvioWhatsApp.objects.filter(
        entity_type="content.pieza", entity_id=envio.entity_id, proposito=EnvioWhatsApp.Proposito.CLIENTE
    )
    sent_states = (EnvioWhatsApp.Estado.ENVIADO, EnvioWhatsApp.Estado.ENTREGADO, EnvioWhatsApp.Estado.LEIDO)
    if envios.exclude(estado__in=sent_states).exists():
        return
    pieza = Pieza.objects.filter(pk=envio.entity_id).first()
    if pieza is not None and pieza.estado == Estado.PUBLICADA:
        content.change_state(pieza, Estado.COMPARTIDA)


# --- Cumplimiento de horario (RNF06) ---
def punctuality(since, until) -> dict:
    """Porcentaje de tareas avisadas dentro de +-5 min de la hora programada (meta: 95 %)."""
    tasks = TareaPublicacion.objects.filter(programada_para__gte=since, programada_para__lt=until).exclude(
        avisada_at__isnull=True
    )
    total = within = 0
    for task in tasks:
        total += 1
        if abs(task.avisada_at - task.programada_para) <= timedelta(minutes=5):
            within += 1
    return {
        "total": total,
        "dentro_de_5_min": within,
        "porcentaje": round(100 * within / total, 1) if total else None,
        "meta": 95,
    }
