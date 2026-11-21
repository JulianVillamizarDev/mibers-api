from pathlib import Path

from django.db import transaction

from modules.audit import services as audit
from modules.audit.models import Action
from modules.core.exceptions import DomainError
from modules.core.networks import Network
from modules.core.roles import Role

from . import media, media_rules, state_machine
from .models import ArchivoPieza, ComentarioRevision, Estado, Pieza, PiezaRed
from .signals import piece_state_changed

MAX_EDITABLE_BYTES = 500 * 1024 * 1024
EDITABLE_EXTENSIONS = {
    ".psd", ".ai", ".fig", ".svg", ".indd", ".xd", ".pdf", ".zip", ".eps", ".sketch",
    ".png", ".jpg", ".jpeg", ".mp4", ".mov", ".prproj", ".aep", ".pptx", ".docx",
}  # fmt: skip
MAX_FILES = 10

TEXT_FIELDS = ("titulo", "campana", "mensaje_cliente")


def load_piece(pk) -> Pieza:
    return Pieza.objects.select_related("cliente").prefetch_related("redes", "archivos").get(pk=pk)


# --- Estados -----------------------------------------------------------------
def change_state(pieza: Pieza, target: str, *, actor=None) -> Pieza:
    """Unica puerta para cambiar el estado. Valida contra el estado REAL en la base de datos
    (con bloqueo de fila), no contra la instancia recibida, que puede estar desactualizada."""
    with transaction.atomic():
        current = Pieza.objects.select_for_update().only("estado").get(pk=pieza.pk).estado
        state_machine.ensure_transition(current, target)
        pieza.estado = target
        pieza.save(update_fields=["estado", "updated_at"])
    piece_state_changed.send(sender=Pieza, pieza=pieza, old_state=current, new_state=target, actor=actor)
    return pieza


def _ensure_editable(pieza: Pieza) -> None:
    if pieza.estado in state_machine.LOCKED_STATES:
        raise DomainError("La pieza ya fue publicada y no puede editarse.", "piece_locked", 409)


# --- Snapshots para detectar cambios de contenido (RF12) --------------------------
def _snapshot(pieza: Pieza) -> dict:
    pieza = load_piece(pieza.pk)
    return {
        "copies": {red.red: pieza.copy_final(red.red) for red in pieza.redes.all()},
        "files": [str(a.pk) for a in pieza.archivos.all()],
    }


def _content_changed(before: dict, after: dict) -> bool:
    shared = before["copies"].keys() & after["copies"].keys()
    return before["files"] != after["files"] or any(before["copies"][r] != after["copies"][r] for r in shared)


def _return_to_review_if_needed(pieza: Pieza, before: dict, *, actor, request, reason: str) -> None:
    """Editar archivos o copy de una pieza aprobada la devuelve a revision (RF12)."""
    if pieza.estado not in state_machine.REVIEWED_STATES:
        return
    if not _content_changed(before, _snapshot(pieza)):
        return
    previous = pieza.estado
    change_state(pieza, Estado.EN_REVISION, actor=actor)
    ComentarioRevision.objects.create(
        pieza=pieza, autor=actor, decision=ComentarioRevision.Decision.DEVUELTA, comentario=reason
    )
    audit.record(
        Action.PIECE_RETURNED_TO_REVIEW,
        actor=actor,
        entity=pieza,
        metadata={"desde": previous, "motivo": reason},
        request=request,
    )


# --- Ficha (RF9, RF11) ---------------------------------------------------------------
def _set_networks(pieza: Pieza, redes: list[dict]) -> None:
    wanted = {}
    for item in redes:
        red = item["red"]
        if (red, pieza.tipo) not in media_rules.RULES:
            raise DomainError(
                f"{Network(red).label} no soporta piezas de tipo {pieza.tipo.lower()}.", "unsupported"
            )
        wanted[red] = item.get("copy", "")
    pieza.redes.exclude(red__in=wanted).delete()
    existing = {r.red: r for r in pieza.redes.all()}
    for red, text in wanted.items():
        if red in existing:
            if existing[red].copy != text:
                existing[red].copy = text
                existing[red].save(update_fields=["copy", "updated_at"])
        else:
            PiezaRed.objects.create(pieza=pieza, red=red, copy=text)


@transaction.atomic
def create_piece(*, cliente, tipo, acting_user, request=None, redes=None, **fields) -> Pieza:
    if not cliente.activo:
        raise DomainError("El cliente esta desactivado.", "client_inactive")
    fields.setdefault("responsable", acting_user)
    pieza = Pieza.objects.create(cliente=cliente, tipo=tipo, creado_por=acting_user, **fields)
    _set_networks(pieza, redes or [])
    audit.record(
        Action.PIECE_CREATED, actor=acting_user, entity=pieza, metadata={"tipo": tipo}, request=request
    )
    return load_piece(pieza.pk)


@transaction.atomic
def update_piece(pieza: Pieza, *, acting_user, request=None, redes=None, **fields) -> Pieza:
    pieza = load_piece(pieza.pk)
    _ensure_editable(pieza)
    if "tipo" in fields and fields["tipo"] != pieza.tipo:
        if pieza.estado not in (Estado.BORRADOR, Estado.CAMBIOS_SOLICITADOS):
            raise DomainError("El tipo solo puede cambiarse en borrador.", "type_locked", 409)
    before = _snapshot(pieza)
    changed = sorted(name for name, value in fields.items() if getattr(pieza, name) != value)
    for name, value in fields.items():
        setattr(pieza, name, value)
    pieza.save()
    if redes is not None:
        _set_networks(pieza, redes)
        changed.append("redes")
    # Si cambia el tipo, los archivos ya subidos deben seguir siendo validos para el nuevo tipo.
    _return_to_review_if_needed(
        pieza, before, actor=acting_user, request=request, reason="Se edito el contenido de la pieza."
    )
    audit.record(
        Action.PIECE_UPDATED, actor=acting_user, entity=pieza, metadata={"fields": changed}, request=request
    )
    return load_piece(pieza.pk)


@transaction.atomic
def delete_piece(pieza: Pieza, *, acting_user, request=None) -> None:
    """Solo se eliminan borradores; el resto conserva su historial."""
    pieza = load_piece(pieza.pk)
    if pieza.estado != Estado.BORRADOR:
        raise DomainError("Solo se pueden eliminar piezas en borrador.", "not_draft", 409)
    piece_id = str(pieza.pk)
    for archivo in pieza.archivos.all():
        archivo.archivo.delete(save=False)
    if pieza.archivo_editable:
        pieza.archivo_editable.delete(save=False)
    pieza.delete()
    audit.record(
        Action.PIECE_UPDATED,
        actor=acting_user,
        entity_type="content.pieza",
        entity_id=piece_id,
        metadata={"op": "deleted"},
        request=request,
    )


# --- Archivos (RF8, RNF08) ---------------------------------------------------------------
def _validate_single(info, pieza: Pieza) -> list[str]:
    errors: list[str] = []
    for red in pieza.network_list():
        errors.extend(media_rules.validate_file(info, red, pieza.tipo))
    return errors


@transaction.atomic
def add_file(pieza: Pieza, uploaded, *, acting_user, request=None) -> ArchivoPieza:
    """Sube un archivo y lo valida contra las redes destino ANTES de aceptarlo (RNF08)."""
    _ensure_editable(pieza)
    pieza = load_piece(pieza.pk)
    if pieza.archivos.count() >= MAX_FILES:
        raise DomainError(f"Una pieza admite como maximo {MAX_FILES} archivos.", "too_many_files")
    before = _snapshot(pieza)
    last = pieza.archivos.order_by("-orden").first()
    archivo = ArchivoPieza(
        pieza=pieza, nombre_original=Path(uploaded.name).name[:255], orden=(last.orden + 1) if last else 0
    )
    archivo.archivo = uploaded
    archivo.save()  # escribe en /media para poder inspeccionar el archivo real
    try:
        info = media.inspect_file(archivo.archivo.path, uploaded.name)
        errors = _validate_single(info, pieza)
        # Sin redes elegidas aun no hay reglas que aplicar: se valida al enviar a revision.
        if errors:
            raise media.InvalidMedia(errors)
    except Exception:
        archivo.archivo.delete(save=False)
        raise
    archivo.kind, archivo.formato, archivo.tamano = info.kind, info.fmt, info.size
    archivo.ancho, archivo.alto, archivo.duracion = info.width, info.height, info.duration
    archivo.save()
    _return_to_review_if_needed(
        pieza, before, actor=acting_user, request=request, reason="Se agrego un archivo a la pieza."
    )
    audit.record(
        Action.PIECE_UPDATED,
        actor=acting_user,
        entity=pieza,
        metadata={"op": "file_added", "archivo": archivo.nombre_original},
        request=request,
    )
    return archivo


@transaction.atomic
def remove_file(archivo: ArchivoPieza, *, acting_user, request=None) -> None:
    pieza = load_piece(archivo.pieza_id)
    _ensure_editable(pieza)
    before = _snapshot(pieza)
    name = archivo.nombre_original
    archivo.archivo.delete(save=False)
    archivo.delete()
    _return_to_review_if_needed(
        pieza, before, actor=acting_user, request=request, reason="Se elimino un archivo de la pieza."
    )
    audit.record(
        Action.PIECE_UPDATED,
        actor=acting_user,
        entity=pieza,
        metadata={"op": "file_removed", "archivo": name},
        request=request,
    )


@transaction.atomic
def reorder_files(pieza: Pieza, ordered_ids: list, *, acting_user, request=None) -> Pieza:
    _ensure_editable(pieza)
    pieza = load_piece(pieza.pk)
    current = {str(a.pk): a for a in pieza.archivos.all()}
    if sorted(map(str, ordered_ids)) != sorted(current):
        raise DomainError("La lista debe incluir exactamente los archivos de la pieza.", "invalid_order")
    before = _snapshot(pieza)
    for position, pk in enumerate(map(str, ordered_ids)):
        ArchivoPieza.objects.filter(pk=pk).update(orden=position)
    _return_to_review_if_needed(
        pieza, before, actor=acting_user, request=request, reason="Se reordenaron los archivos de la pieza."
    )
    audit.record(
        Action.PIECE_UPDATED,
        actor=acting_user,
        entity=pieza,
        metadata={"op": "files_reordered"},
        request=request,
    )
    return load_piece(pieza.pk)


def set_editable_file(pieza: Pieza, uploaded, *, acting_user, request=None) -> Pieza:
    """Archivo editable de la pieza (PSD, AI, Figma...). No altera el contenido aprobado (RF8)."""
    pieza = load_piece(pieza.pk)
    _ensure_editable(pieza)
    if Path(uploaded.name).suffix.lower() not in EDITABLE_EXTENSIONS:
        raise DomainError("Formato no permitido para el archivo editable.", "invalid_editable")
    if uploaded.size > MAX_EDITABLE_BYTES:
        raise DomainError("El archivo editable no puede superar 500 MB.", "editable_too_large")
    if pieza.archivo_editable:
        pieza.archivo_editable.delete(save=False)
    pieza.archivo_editable_nombre = Path(uploaded.name).name[:255]
    pieza.archivo_editable = uploaded
    pieza.save(update_fields=["archivo_editable", "archivo_editable_nombre", "updated_at"])
    audit.record(
        Action.PIECE_UPDATED,
        actor=acting_user,
        entity=pieza,
        metadata={"op": "editable_attached"},
        request=request,
    )
    return load_piece(pieza.pk)


# --- Preparacion para revisar/programar/publicar (RF9, RNF08) ---------------------------------
def readiness_problems(pieza: Pieza) -> list[str]:
    """Todo lo que impide revisar, programar o publicar la pieza. Lista vacia = lista."""
    pieza = load_piece(pieza.pk)
    problems: list[str] = []
    if not pieza.cliente.activo:
        problems.append("El cliente esta desactivado.")
    networks = pieza.network_list()
    if not networks:
        problems.append("Selecciona al menos una red destino.")
    if pieza.programada_para is None:
        problems.append("Falta la fecha y hora de publicacion.")
    infos = [
        media_rules.MediaInfo(
            kind=a.kind, fmt=a.formato, width=a.ancho, height=a.alto, size=a.tamano, duration=a.duracion
        )
        for a in pieza.archivos.all()
    ]
    for red in networks:
        if not pieza.copy_final(red).strip():
            problems.append(f"{Network(red).label}: falta el copy.")
        if (red, pieza.tipo) not in media_rules.RULES:
            problems.append(f"{Network(red).label} no soporta piezas de tipo {pieza.tipo.lower()}.")
            continue
        problems.extend(media_rules.validate_set(infos, red, pieza.tipo))
    return problems


# --- Revision (RF12) -------------------------------------------------------------------------
def _require_admin(actor) -> None:
    if actor is None or actor.role != Role.ADMIN:
        raise DomainError("Solo el administrador puede aprobar o solicitar cambios.", "admin_required", 403)


def submit_for_review(pieza: Pieza, *, acting_user, request=None) -> Pieza:
    pieza = load_piece(pieza.pk)
    if pieza.estado not in (Estado.BORRADOR, Estado.CAMBIOS_SOLICITADOS):
        state_machine.ensure_transition(pieza.estado, Estado.EN_REVISION)  # lanza el error adecuado
    problems = readiness_problems(pieza)
    if problems:
        raise DomainError(problems, "piece_incomplete")
    with transaction.atomic():
        change_state(pieza, Estado.EN_REVISION, actor=acting_user)
        ComentarioRevision.objects.create(
            pieza=pieza, autor=acting_user, decision=ComentarioRevision.Decision.ENVIADA
        )
        audit.record(Action.PIECE_SUBMITTED, actor=acting_user, entity=pieza, request=request)
    return load_piece(pieza.pk)


def approve(pieza: Pieza, *, acting_user, comentario="", request=None) -> Pieza:
    _require_admin(acting_user)
    pieza = load_piece(pieza.pk)
    state_machine.ensure_transition(pieza.estado, Estado.APROBADA)
    problems = readiness_problems(pieza)
    if problems:
        raise DomainError(problems, "piece_incomplete")
    with transaction.atomic():
        change_state(pieza, Estado.APROBADA, actor=acting_user)
        ComentarioRevision.objects.create(
            pieza=pieza,
            autor=acting_user,
            decision=ComentarioRevision.Decision.APROBADA,
            comentario=comentario,
        )
        audit.record(Action.PIECE_APPROVED, actor=acting_user, entity=pieza, request=request)
    return load_piece(pieza.pk)


def request_changes(pieza: Pieza, *, acting_user, comentario: str, request=None) -> Pieza:
    _require_admin(acting_user)
    pieza = load_piece(pieza.pk)
    if not comentario or not comentario.strip():
        raise DomainError("Indica que cambios se necesitan.", "comment_required")
    with transaction.atomic():
        change_state(pieza, Estado.CAMBIOS_SOLICITADOS, actor=acting_user)
        ComentarioRevision.objects.create(
            pieza=pieza,
            autor=acting_user,
            decision=ComentarioRevision.Decision.CAMBIOS,
            comentario=comentario.strip(),
        )
        audit.record(
            Action.PIECE_CHANGES_REQUESTED,
            actor=acting_user,
            entity=pieza,
            metadata={"comentario": comentario.strip()},
            request=request,
        )
    return load_piece(pieza.pk)
