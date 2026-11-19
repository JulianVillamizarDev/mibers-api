"""Cifrado en reposo AES-256-GCM (RNF02) para datos de contacto y autorizaciones."""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

PREFIX = "enc1:"
NONCE_SIZE = 12


def _aesgcm() -> AESGCM:
    try:
        key = base64.urlsafe_b64decode(settings.FIELD_ENCRYPTION_KEY)
    except Exception as exc:  # noqa: BLE001
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY no es base64 valido") from exc
    if len(key) != 32:
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY debe decodificar a 32 bytes (AES-256)")
    return AESGCM(key)


def encrypt(plaintext: str) -> str:
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = _aesgcm().encrypt(nonce, plaintext.encode("utf-8"), None)
    return PREFIX + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt(token: str) -> str:
    if not token.startswith(PREFIX):
        raise ValueError("valor no cifrado")
    raw = base64.urlsafe_b64decode(token[len(PREFIX) :])
    nonce, ciphertext = raw[:NONCE_SIZE], raw[NONCE_SIZE:]
    try:
        return _aesgcm().decrypt(nonce, ciphertext, None).decode("utf-8")
    except InvalidTag as exc:
        raise ValueError("no se pudo descifrar: clave incorrecta o dato alterado") from exc
