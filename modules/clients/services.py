import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.audit import services as audit
from modules.audit.models import Action
from modules.core.exceptions import DomainError
from modules.notifications import services as notifications
from modules.notifications.models import Alerta

from .models import Cliente, ContactoWhatsApp, CuentaSocial

RETENTION_DAYS = 365  # RF5: historial de clientes desactivados durante un anio


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


def update_account(cuenta: CuentaSocial, *, acting_user, request=None, **fields) -> CuentaSocial:
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
            entity=cuenta,
            metadata={"cliente": str(cuenta.cliente_id), "de": previous_state, "a": cuenta.estado},
            request=request,
        )
        _on_account_status_changed(cuenta, previous_state)
    else:
        audit.record(
            Action.NETWORK_LINKED,
            actor=acting_user,
            entity=cuenta,
            metadata={"cliente": str(cuenta.cliente_id), "op": "updated", "fields": sorted(fields)},
            request=request,
        )
    return cuenta


def _on_account_status_changed(cuenta: CuentaSocial, previous_state: str) -> None:
    """Avisa al administrador cuando una cuenta expira o se desvincula (RF19)."""
    tipo = {
        CuentaSocial.Estado.EXPIRADA: Alerta.Tipo.CUENTA_EXPIRADA,
        CuentaSocial.Estado.DESVINCULADA: Alerta.Tipo.CUENTA_DESVINCULADA,
    }.get(cuenta.estado)
    if tipo is None:
        return
    notifications.send_alert(
        tipo,
        f"{cuenta.get_red_display()} de {cuenta.cliente.nombre} ({cuenta.handle}).",
        entity_type="clients.cuentasocial",
        entity_id=cuenta.pk,
        dedupe_key=f"cuenta:{cuenta.pk}:{cuenta.estado}:{cuenta.estado_actualizado_at.isoformat()}",
    )
