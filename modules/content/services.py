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


# --- Revision (RF12) -------------------------------------------------------------------------






