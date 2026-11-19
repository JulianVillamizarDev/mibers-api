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








# --- Contactos de WhatsApp (RF6, RNF13) --------------------------------------








# --- Cuentas sociales (RF7): solo Administrador -------------------------------






# --- Verificacion de cuentas contra Meta (HU-08, ADR0008) -----------------------------------------






