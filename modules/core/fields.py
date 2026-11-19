from django.db import models

from . import crypto


class EncryptedTextField(models.TextField):
    """TextField cifrado con AES-256-GCM. Transparente para el codigo: guarda str, lee str.

    No se puede filtrar ni ordenar por este campo (el nonce es aleatorio por valor).
    """

    description = "Texto cifrado en reposo (AES-256-GCM)"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return crypto.encrypt(str(value))

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        return crypto.decrypt(value)
