from datetime import UTC, datetime, timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import RequestFactory
from django.utils import timezone

from modules.audit import services
from modules.audit.models import Action, AuditImmutableError, AuditLog

URL = "/api/v1/auditoria"

pytestmark = pytest.mark.django_db


# --- Registro (RF4) -----------------------------------------------------------
def test_record_guarda_usuario_accion_fecha_y_hora(staff_user):
    entry = services.record(Action.PIECE_CREATED, actor=staff_user, entity=staff_user, metadata={"k": 1})
    entry.refresh_from_db()
    assert entry.actor == staff_user
    assert entry.actor_username == "copy1"
    assert entry.action == "PIECE_CREATED"
    assert entry.entity_type == "accounts.user" and entry.entity_id == str(staff_user.pk)
    assert entry.metadata == {"k": 1}
    assert abs(entry.created_at - timezone.now()) < timedelta(seconds=5)


def test_record_sin_actor_conserva_el_usuario_intentado():
    entry = services.record(Action.LOGIN_FAILED, actor_username="fantasma")
    assert entry.actor is None and entry.actor_username == "fantasma"


def test_record_toma_la_ip_de_x_forwarded_for():
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="190.1.2.3, 10.0.0.1", REMOTE_ADDR="10.0.0.1")
    assert services.record(Action.LOGIN, request=request).ip_address == "190.1.2.3"


# --- Inmutabilidad (RNF11) ----------------------------------------------------
def test_no_se_puede_editar_un_registro():
    entry = services.record(Action.LOGIN, actor_username="x")
    entry.action = Action.LOGOUT
    with pytest.raises(AuditImmutableError):
        entry.save()


def test_no_se_puede_borrar_un_registro_ni_en_bloque():
    entry = services.record(Action.LOGIN, actor_username="x")
    with pytest.raises(AuditImmutableError):
        entry.delete()
    with pytest.raises(AuditImmutableError):
        AuditLog.objects.all().delete()
    with pytest.raises(AuditImmutableError):
        AuditLog.objects.all().update(actor_username="otro")
    assert AuditLog.objects.count() == 1


@pytest.mark.postgres
def test_trigger_de_postgres_bloquea_update_y_delete_por_sql():
    entry = services.record(Action.LOGIN, actor_username="x")
    table = AuditLog._meta.db_table
    for sql in (f"UPDATE {table} SET actor_username = 'otro'", f"DELETE FROM {table}"):
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql)
    assert AuditLog.objects.filter(pk=entry.pk, actor_username="x").exists()


# --- Retencion de 12 meses ------------------------------------------------------
def test_purge_borra_solo_registros_con_mas_de_12_meses():
    old = services.record(Action.LOGIN, actor_username="viejo")
    ancient = AuditLog.objects.create(
        action=Action.LOGIN, actor_username="antiguo", created_at=timezone.now() - timedelta(days=400)
    )
    recent = AuditLog.objects.create(
        action=Action.LOGIN, actor_username="reciente", created_at=timezone.now() - timedelta(days=300)
    )
    assert services.purge_expired() == 1
    remaining = set(AuditLog.objects.values_list("pk", flat=True))
    assert ancient.pk not in remaining
    assert {recent.pk, old.pk} <= remaining


# --- Consulta solo para el administrador (RF4) -------------------------------------
def test_personal_no_puede_consultar_el_historial(staff_client):
    assert staff_client.get(URL).status_code == 403


def test_sin_sesion_no_puede_consultar(api_client):
    assert api_client.get(URL).status_code == 401


def test_admin_consulta_y_filtra(admin_client, admin_user, staff_user):
    services.record(Action.PIECE_APPROVED, actor=admin_user)
    services.record(Action.PIECE_CREATED, actor=staff_user)
    services.record(Action.PIECE_CREATED, actor=staff_user)

    everything = admin_client.get(URL).json()
    assert everything["count"] == 3

    by_action = admin_client.get(URL, {"action": "PIECE_CREATED"}).json()
    assert by_action["count"] == 2

    by_actor = admin_client.get(URL, {"actor": str(admin_user.pk)}).json()
    assert [r["action"] for r in by_actor["results"]] == ["PIECE_APPROVED"]
    assert by_actor["results"][0]["action_label"] == "Pieza aprobada"


def test_filtro_por_fechas_usa_dia_de_colombia(admin_client):
    # 2026-03-10 02:00 UTC = 2026-03-09 21:00 en Colombia
    AuditLog.objects.create(
        action=Action.LOGIN, actor_username="a", created_at=datetime(2026, 3, 10, 2, 0, tzinfo=UTC)
    )
    assert admin_client.get(URL, {"desde": "2026-03-09", "hasta": "2026-03-09"}).json()["count"] == 1
    assert admin_client.get(URL, {"desde": "2026-03-10", "hasta": "2026-03-10"}).json()["count"] == 0


def test_la_api_no_expone_escrituras(admin_client):
    entry = services.record(Action.LOGIN, actor_username="x")
    assert admin_client.post(URL, {"action": "LOGIN"}, format="json").status_code == 405
    assert admin_client.patch(f"{URL}/{entry.pk}", {"action": "LOGOUT"}, format="json").status_code == 405
    assert admin_client.delete(f"{URL}/{entry.pk}").status_code == 405


def test_catalogo_de_acciones(admin_client):
    body = admin_client.get(f"{URL}/acciones").json()
    assert {"value": "LOGIN", "label": "Inicio de sesion"} in body
