import pytest
from django.core.exceptions import ImproperlyConfigured

from modules.core import crypto
from modules.core.fields import EncryptedTextField


def test_cifra_y_descifra_ida_y_vuelta():
    token = crypto.encrypt("+573001112233")
    assert token.startswith("enc1:")
    assert "3001112233" not in token
    assert crypto.decrypt(token) == "+573001112233"


def test_nonce_aleatorio_produce_cifrados_distintos():
    assert crypto.encrypt("igual") != crypto.encrypt("igual")


def test_dato_alterado_no_se_descifra():
    token = crypto.encrypt("secreto")
    alterado = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    with pytest.raises(ValueError):
        crypto.decrypt(alterado)


def test_clave_con_longitud_invalida_falla(settings):
    settings.FIELD_ENCRYPTION_KEY = "Y29ydGE="
    with pytest.raises(ImproperlyConfigured):
        crypto.encrypt("x")


def test_campo_cifra_al_guardar_y_descifra_al_leer():
    field = EncryptedTextField()
    stored = field.get_prep_value("Maria Perez")
    assert stored != "Maria Perez"
    assert field.from_db_value(stored, None, None) == "Maria Perez"


def test_campo_respeta_nulos_y_vacios():
    field = EncryptedTextField(null=True, blank=True)
    assert field.get_prep_value(None) is None
    assert field.get_prep_value("") == ""
    assert field.from_db_value(None, None, None) is None
