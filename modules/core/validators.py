import re

from django.core.exceptions import ValidationError

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def validate_e164(value: str) -> None:
    """Numero en formato E.164, p. ej. +573001112233 (requerido por WhatsApp Cloud API)."""
    if not E164_RE.match(value or ""):
        raise ValidationError(
            "Usa el formato internacional, por ejemplo +573001112233.", code="invalid_phone"
        )
