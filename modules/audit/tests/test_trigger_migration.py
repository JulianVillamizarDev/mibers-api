"""Regresion: la migracion del trigger fallaba en PostgreSQL con
`psycopg.ProgrammingError: incomplete placeholder: '%'` (el '%' de RAISE EXCEPTION).

El SQL debe ejecutarse con params=None para que Django no lo componga en el cliente.
Estas pruebas no necesitan PostgreSQL."""

import importlib
from types import SimpleNamespace

import pytest

migration = importlib.import_module("modules.audit.migrations.0002_immutable_trigger")


class FakeSchemaEditor:
    def __init__(self, vendor):
        self.connection = SimpleNamespace(vendor=vendor)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))


def test_crea_el_trigger_enviando_el_sql_sin_parametros():
    editor = FakeSchemaEditor("postgresql")
    migration.create_trigger(None, editor)
    assert editor.calls == [(migration.CREATE_TRIGGER, None)]


def test_elimina_el_trigger_enviando_el_sql_sin_parametros():
    editor = FakeSchemaEditor("postgresql")
    migration.drop_trigger(None, editor)
    assert editor.calls == [(migration.DROP_TRIGGER, None)]


@pytest.mark.parametrize("function", ["create_trigger", "drop_trigger"])
def test_en_otros_motores_no_hace_nada(function):
    editor = FakeSchemaEditor("sqlite")
    getattr(migration, function)(None, editor)
    assert editor.calls == []


def test_el_sql_del_trigger_conserva_el_marcador_de_plpgsql():
    """Si alguien quita el '%' del mensaje este test avisa de que ya no hace falta el params=None."""
    assert "%" in migration.CREATE_TRIGGER
