"""RF14 / RNF09: programar con confirmacion previa y sin salirse de las cuentas del cliente."""

from datetime import timedelta

import pytest
from django.utils import timezone

from modules.audit.models import AuditLog
from modules.clients.models import CuentaSocial
from modules.content.models import Pieza
from modules.publishing.models import TareaPublicacion

from .helpers import build_piece, make_client_with_accounts, reload

pytestmark = pytest.mark.django_db


@pytest.fixture
def cliente():
    return make_client_with_accounts()


@pytest.fixture
def piece(cliente, staff_user, admin_user):
    return build_piece(cliente, staff_user, admin_user, redes=("INSTAGRAM", "FACEBOOK"))


def preview_url(piece):
    return f"/api/v1/piezas/{piece.pk}/programacion"


def schedule(client, piece, **body):
    return client.post(f"/api/v1/piezas/{piece.pk}/programar", body, format="json")


# --- RNF09: la programacion muestra cliente, cuentas y copy por red antes de confirmar -----------------
def test_preview_muestra_cliente_cuentas_destino_y_copy_por_red(
    staff_client, cliente, staff_user, admin_user
):
    piece = build_piece(
        cliente,
        staff_user,
        admin_user,
        redes=("INSTAGRAM", "FACEBOOK"),
        hashtags=["#cafe"],
        copy_comun="Hola",
        copies={"FACEBOOK": "Con enlace"},
    )

    body = staff_client.get(preview_url(piece)).json()
    assert body["cliente"] == "Cafe del Valle" and body["puede_confirmar"] is True and body["problemas"] == []
    by_network = {r["red"]: r for r in body["redes"]}
    assert by_network["INSTAGRAM"]["cuenta"]["handle"] == "@cafe_instagram"
    assert by_network["INSTAGRAM"]["copy_final"] == "Hola\n\n#cafe"
    assert by_network["FACEBOOK"]["cuenta"]["handle"] == "@cafe_facebook"
    assert by_network["FACEBOOK"]["copy_final"] == "Con enlace\n\n#cafe"
    assert body["archivos"] == ["f0.png"]


def test_no_se_programa_sin_confirmar(staff_client, piece):
    response = schedule(staff_client, piece)
    assert response.status_code == 400 and response.json()["code"] == "confirmation_required"
    response = schedule(staff_client, piece, confirmar=False)
    assert response.json()["code"] == "confirmation_required"
    assert reload(piece).estado == "APROBADA"


def test_programar_confirmando_deja_la_pieza_programada_y_audita(staff_client, piece):
    response = schedule(staff_client, piece, confirmar=True)
    assert response.status_code == 200 and response.json()["estado"] == "PROGRAMADA"
    entry = AuditLog.objects.get(action="PIECE_SCHEDULED")
    assert entry.actor_username == "copy1" and sorted(entry.metadata["redes"]) == ["FACEBOOK", "INSTAGRAM"]


# --- Condiciones para programar ----------------------------------------------------------------------------
def test_solo_se_programan_piezas_aprobadas(staff_client, cliente, staff_user, admin_user):
    piece = build_piece(cliente, staff_user, admin_user, approve=False)
    response = schedule(staff_client, piece, confirmar=True)
    assert response.status_code == 409 and response.json()["code"] == "not_approved"
    assert not staff_client.get(preview_url(piece)).json()["puede_confirmar"]


def test_la_fecha_debe_ser_futura(staff_client, cliente, staff_user, admin_user):
    piece = build_piece(cliente, staff_user, admin_user)
    Pieza.objects.filter(pk=piece.pk).update(programada_para=timezone.now() - timedelta(minutes=5))
    response = schedule(staff_client, piece, confirmar=True)
    assert response.status_code == 400 and "ya paso" in " ".join(response.json()["detail"])


@pytest.mark.parametrize(
    ("estado", "texto"),
    [("EXPIRADA", "expirada"), ("DESVINCULADA", "desvinculada")],
)
def test_la_cuenta_destino_debe_estar_vinculada(staff_client, cliente, piece, estado, texto):
    CuentaSocial.objects.filter(cliente=cliente, red="FACEBOOK").update(estado=estado)
    response = schedule(staff_client, piece, confirmar=True)
    assert response.status_code == 400 and response.json()["code"] == "cannot_schedule"
    assert any("Facebook" in p and texto in p for p in response.json()["detail"])
    assert reload(piece).estado == "APROBADA"


def test_el_cliente_debe_tener_cuenta_en_cada_red_destino(staff_client, staff_user, admin_user):
    cliente = make_client_with_accounts("Solo Insta", networks=("INSTAGRAM",))
    piece = build_piece(cliente, staff_user, admin_user, redes=("INSTAGRAM", "FACEBOOK"))
    response = schedule(staff_client, piece, confirmar=True)
    assert any("Facebook" in p and "no tiene una cuenta" in p for p in response.json()["detail"])


def test_no_se_usan_cuentas_de_otro_cliente(staff_client, staff_user, admin_user):
    """RNF09: 0 publicaciones fuera de las cuentas del cliente."""
    make_client_with_accounts("Otro Cliente")  # tiene cuentas, pero no son de la pieza
    sin_cuentas = make_client_with_accounts("Sin Cuentas", networks=())
    piece = build_piece(sin_cuentas, staff_user, admin_user)
    response = schedule(staff_client, piece, confirmar=True)
    assert response.status_code == 400 and "no tiene una cuenta" in " ".join(response.json()["detail"])
    assert TareaPublicacion.objects.count() == 0


def test_programar_revalida_archivos_y_copy(staff_client, piece):
    Pieza.objects.filter(pk=piece.pk).update(copy_comun="")
    response = schedule(staff_client, piece, confirmar=True)
    assert response.status_code == 400 and any("falta el copy" in p for p in response.json()["detail"])


def test_exige_sesion(api_client, piece):
    assert api_client.get(preview_url(piece)).status_code == 401
    assert (
        api_client.post(
            f"/api/v1/piezas/{piece.pk}/programar", {"confirmar": True}, format="json"
        ).status_code
        == 401
    )


# --- Cancelar la programacion ---------------------------------------------------------------------------------
def test_cancelar_programacion_devuelve_a_aprobada(staff_client, piece):
    schedule(staff_client, piece, confirmar=True)
    response = staff_client.post(f"/api/v1/piezas/{piece.pk}/cancelar-programacion")
    assert response.status_code == 200 and response.json()["estado"] == "APROBADA"
    assert AuditLog.objects.filter(action="PIECE_UNSCHEDULED").count() == 1


def test_no_se_cancela_lo_que_no_esta_programado(staff_client, piece):
    response = staff_client.post(f"/api/v1/piezas/{piece.pk}/cancelar-programacion")
    assert response.status_code == 409 and response.json()["code"] == "not_scheduled"
