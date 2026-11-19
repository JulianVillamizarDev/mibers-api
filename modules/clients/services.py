import logging
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.audit import services as audit
from modules.audit.models import Action
from modules.core.exceptions import DomainError
from modules.notifications import services as notifications
from modules.notifications.models import Alerta

from .meta import GraphError, get_graph_client
from .models import Cliente, ContactoWhatsApp, CuentaSocial

RETENTION_DAYS = 365  # RF5: historial de clientes desactivados durante un anio
META_VERIFY_INTERVAL_SECONDS = 6 * 3600
UNLINK_ERROR_CODES = {10, 100}
TOKEN_ERROR_CODE = 190
logger = logging.getLogger(__name__)


# --- Clientes (RF5) ---------------------------------------------------------
def create_client(*, acting_user, request=None, manual_marca=None, **fields) -> Cliente:
    cliente = Cliente(**fields)
    if manual_marca:
        cliente.manual_marca_nombre = getattr(manual_marca, "name", "")[:255]
        cliente.manual_marca = manual_marca
    cliente.save()
    audit.record(Action.CLIENT_CREATED, actor=acting_user, entity=cliente, request=request)
    return cliente


def update_client(cliente: Cliente, *, acting_user, request=None, manual_marca=None, **fields) -> Cliente:
    changed = sorted(fields)
    for name, value in fields.items():
        setattr(cliente, name, value)
    if manual_marca is not None:
        if cliente.manual_marca:
            cliente.manual_marca.delete(save=False)
        cliente.manual_marca_nombre = getattr(manual_marca, "name", "")[:255]
        cliente.manual_marca = manual_marca
        changed.append("manual_marca")
    cliente.save()
    audit.record(
        Action.CLIENT_UPDATED,
        actor=acting_user,
        entity=cliente,
        metadata={"fields": changed},
        request=request,
    )
    return cliente


def deactivate_client(cliente: Cliente, *, acting_user, request=None) -> Cliente:
    """Desactiva conservando el historial durante un anio (RF5)."""
    if not cliente.activo:
        raise DomainError("El cliente ya esta desactivado.", "already_inactive")
    cliente.activo = False
    cliente.desactivado_at = timezone.now()
    cliente.save(update_fields=["activo", "desactivado_at", "updated_at"])
    audit.record(Action.CLIENT_DEACTIVATED, actor=acting_user, entity=cliente, request=request)
    return cliente


def reactivate_client(cliente: Cliente, *, acting_user, request=None) -> Cliente:
    if cliente.activo:
        raise DomainError("El cliente ya esta activo.", "already_active")
    cliente.activo = True
    cliente.desactivado_at = None
    cliente.save(update_fields=["activo", "desactivado_at", "updated_at"])
    audit.record(Action.CLIENT_ACTIVATED, actor=acting_user, entity=cliente, request=request)
    return cliente


def purge_expired_clients(*, now=None) -> int:
    """Elimina clientes desactivados hace mas de un anio, con sus piezas y archivos.
    La auditoria se conserva (solo guarda ids y textos, sin claves foraneas)."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=RETENTION_DAYS)
    expired = list(Cliente.objects.filter(activo=False, desactivado_at__lt=cutoff))
    for cliente in expired:
        client_id, name = str(cliente.pk), cliente.nombre
        with transaction.atomic():
            cliente.delete()
        shutil.rmtree(Path(settings.MEDIA_ROOT) / "clientes" / client_id, ignore_errors=True)
        audit.record(
            Action.CLIENT_UPDATED,
            entity_type="clients.cliente",
            entity_id=client_id,
            metadata={"purged": True, "nombre": name},
        )
    return len(expired)


# --- Contactos de WhatsApp (RF6, RNF13) --------------------------------------
def _has_number(cliente: Cliente, numero: str, exclude_pk=None) -> bool:
    # El numero esta cifrado con nonce aleatorio: se compara descifrando (pocos contactos por cliente).
    return any(c.numero == numero for c in cliente.contactos.exclude(pk=exclude_pk))


def add_contact(
    cliente: Cliente, *, nombre, numero, autorizado, evidencia="", acting_user, request=None
) -> ContactoWhatsApp:
    if _has_number(cliente, numero):
        raise DomainError("Ese numero ya esta registrado para el cliente.", "duplicate_contact")
    contacto = ContactoWhatsApp.objects.create(
        cliente=cliente,
        nombre=nombre,
        numero=numero,
        autorizacion_at=timezone.now() if autorizado else None,
        autorizacion_evidencia=evidencia if autorizado else "",
    )
    audit.record(
        Action.CONTACT_CHANGED,
        actor=acting_user,
        entity=contacto,
        metadata={"op": "created", "cliente": str(cliente.pk), "autorizado": autorizado},
        request=request,
    )
    return contacto


def update_contact(contacto: ContactoWhatsApp, *, acting_user, request=None, **fields) -> ContactoWhatsApp:
    numero = fields.get("numero")
    if numero and _has_number(contacto.cliente, numero, exclude_pk=contacto.pk):
        raise DomainError("Ese numero ya esta registrado para el cliente.", "duplicate_contact")
    autorizado = fields.pop("autorizado", None)
    evidencia = fields.pop("evidencia", None)
    for name, value in fields.items():
        setattr(contacto, name, value)
    if autorizado is True and contacto.autorizacion_at is None:
        contacto.autorizacion_at = timezone.now()
    elif autorizado is False:
        contacto.autorizacion_at = None
        contacto.autorizacion_evidencia = ""
    if evidencia is not None and contacto.autorizado:
        contacto.autorizacion_evidencia = evidencia
    contacto.save()
    audit.record(
        Action.CONTACT_CHANGED,
        actor=acting_user,
        entity=contacto,
        metadata={"op": "updated", "cliente": str(contacto.cliente_id), "autorizado": contacto.autorizado},
        request=request,
    )
    return contacto


def remove_contact(contacto: ContactoWhatsApp, *, acting_user, request=None) -> None:
    contact_id, client_id = str(contacto.pk), str(contacto.cliente_id)
    contacto.delete()
    audit.record(
        Action.CONTACT_CHANGED,
        actor=acting_user,
        entity_type="clients.contactowhatsapp",
        entity_id=contact_id,
        metadata={"op": "removed", "cliente": client_id},
        request=request,
    )


# --- Cuentas sociales (RF7): solo Administrador -------------------------------
def link_account(
    cliente: Cliente, *, red, handle, url_perfil, id_externo="", acting_user, request=None
) -> CuentaSocial:
    try:
        with transaction.atomic():
            cuenta = CuentaSocial.objects.create(
                cliente=cliente, red=red, handle=handle, url_perfil=url_perfil, id_externo=id_externo
            )
    except IntegrityError as exc:
        raise DomainError("El cliente ya tiene una cuenta en esa red.", "duplicate_account") from exc
    audit.record(
        Action.NETWORK_LINKED,
        actor=acting_user,
        entity=cuenta,
        metadata={"cliente": str(cliente.pk), "red": red, "handle": handle},
        request=request,
    )
    return cuenta


def update_account(
    cuenta: CuentaSocial,
    *,
    acting_user,
    request=None,
    actor_username: str = "",
    **fields,
) -> CuentaSocial:
    previous_state = cuenta.estado
    for name, value in fields.items():
        setattr(cuenta, name, value)
    if cuenta.estado != previous_state:
        cuenta.estado_actualizado_at = timezone.now()
    cuenta.save()
    if cuenta.estado != previous_state:
        audit.record(
            Action.NETWORK_STATUS_CHANGED,
            actor=acting_user,
            actor_username=actor_username,
            entity=cuenta,
            metadata={"cliente": str(cuenta.cliente_id), "de": previous_state, "a": cuenta.estado},
            request=request,
        )
        _on_account_status_changed(cuenta, previous_state)
    else:
        audit.record(
            Action.NETWORK_LINKED,
            actor=acting_user,
            actor_username=actor_username,
            entity=cuenta,
            metadata={"cliente": str(cuenta.cliente_id), "op": "updated", "fields": sorted(fields)},
            request=request,
        )
    return cuenta




# --- Verificacion de cuentas contra Meta (HU-08, ADR0008) -----------------------------------------
def _meta_error_code(error: GraphError) -> int | None:
    try:
        return int(error.code) if error.code is not None else None
    except (TypeError, ValueError):
        return None


def _set_account_state(cuenta: CuentaSocial, estado: str) -> bool:
    if cuenta.estado == estado:
        return False
    update_account(
        cuenta,
        estado=estado,
        acting_user=None,
        actor_username="sistema",
    )
    return True


def _expire_all(accounts: list[CuentaSocial], result: dict) -> None:
    for cuenta in accounts:
        if _set_account_state(cuenta, CuentaSocial.Estado.EXPIRADA):
            result["expiradas"] += 1


def verify_meta_accounts() -> dict:
    """Verifica las cuentas con id_externo contra Meta y actualiza su estado (HU-08).

    Un token invalido expira todas las cuentas. Los codigos de cuenta sin permiso o
    inexistente desvinculan solo esa cuenta. Los fallos de red o 5xx no cambian nada.
    """
    result = {
        "omitida": "",
        "revisadas": 0,
        "vinculadas": 0,
        "expiradas": 0,
        "desvinculadas": 0,
        "sin_cambios": 0,
    }
    if settings.META_PUBLISHING == "manual":
        result["omitida"] = "META_PUBLISHING=manual"
        return result
    if not settings.META_SYSTEM_USER_TOKEN:
        result["omitida"] = "sin META_SYSTEM_USER_TOKEN"
        return result

    accounts = list(CuentaSocial.objects.exclude(id_externo="").select_related("cliente").order_by("red"))
    result["revisadas"] = len(accounts)
    if not accounts:
        return result
    try:
        client = get_graph_client()
    except ImproperlyConfigured as exc:
        result["omitida"] = str(exc)
        return result

    try:
        for cuenta in accounts:
            fields = "username" if cuenta.red == "INSTAGRAM" else "name,access_token"
            try:
                client.get(f"/{cuenta.id_externo}", {"fields": fields})
            except GraphError as exc:
                code = _meta_error_code(exc)
                if code == TOKEN_ERROR_CODE:
                    _expire_all(accounts, result)
                    break
                if code in UNLINK_ERROR_CODES or (code is not None and 200 <= code <= 299):
                    if _set_account_state(cuenta, CuentaSocial.Estado.DESVINCULADA):
                        result["desvinculadas"] += 1
                else:
                    result["sin_cambios"] += 1
                    logger.warning("No se pudo verificar la cuenta %s: %s", cuenta.pk, exc)
            else:
                if _set_account_state(cuenta, CuentaSocial.Estado.VINCULADA):
                    result["vinculadas"] += 1
                else:
                    result["sin_cambios"] += 1
    finally:
        client.close()
    return result
