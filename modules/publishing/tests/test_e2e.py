"""Recorrido completo de punta a punta, solo por la API HTTP y con inicio de sesion real:
director crea al personal y al cliente -> se prepara, aprueba y programa una pieza -> llega la hora ->
el equipo publica y confirma -> el cliente recibe el contenido por WhatsApp."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from modules.audit.models import AuditLog
from modules.content.tests.helpers import image_file
from modules.core import jobs
from modules.notifications.gateway import FakeGateway
from modules.publishing import services

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Clave-Definitiva-2026"


@pytest.fixture(autouse=True)
def fake_whatsapp(settings):
    settings.WHATSAPP_BACKEND = "fake"
    FakeGateway.reset()
    yield
    FakeGateway.reset()


def login(username, password) -> APIClient:
    client = APIClient()
    response = client.post("/api/v1/auth/login", {"username": username, "password": password}, format="json")
    assert response.status_code == 200, response.json()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
    return client


def test_flujo_completo(settings):
    User = get_user_model()
    User.objects.create_user(
        "director", password="Clave-Inicial-123", role="ADMIN", whatsapp_number="+573001110000"
    )
    admin = login("director", "Clave-Inicial-123")

    # 1. el director da de alta a una colaboradora (sin correo, con contrasena temporal)
    created = admin.post(
        "/api/v1/usuarios",
        {
            "username": "maria",
            "first_name": "Maria",
            "cargo": "Copywriter",
            "whatsapp_number": "+573004440000",
        },
        format="json",
    ).json()
    staff = login("maria", created["temporary_password"])
    assert staff.get("/api/v1/clientes").status_code == 403  # debe cambiar la temporal primero
    staff.post(
        "/api/v1/auth/change-password",
        {"current_password": created["temporary_password"], "new_password": NEW_PASSWORD},
        format="json",
    )
    staff = login("maria", NEW_PASSWORD)

    # 2. cliente con contrato, cuentas (solo el admin las vincula) y contacto autorizado
    cliente = staff.post(
        "/api/v1/clientes",
        {
            "nombre": "Cafe del Valle",
            "posts_por_semana": 3,
            "contrato_inicio": "2026-01-15",
            "estrategia": "VENDER",
            "objetivo": "Aumentar ventas en temporada",
        },
        format="json",
    ).json()
    cid = cliente["id"]
    for red, handle, url in (
        ("INSTAGRAM", "@cafedelvalle", "https://instagram.com/cafedelvalle"),
        ("FACEBOOK", "Cafe del Valle", "https://facebook.com/cafedelvalle"),
    ):
        assert (
            staff.post(
                f"/api/v1/clientes/{cid}/cuentas",
                {"red": red, "handle": handle, "url_perfil": url},
                format="json",
            ).status_code
            == 403
        )
        assert (
            admin.post(
                f"/api/v1/clientes/{cid}/cuentas",
                {"red": red, "handle": handle, "url_perfil": url},
                format="json",
            ).status_code
            == 201
        )
    staff.post(
        f"/api/v1/clientes/{cid}/contactos",
        {"nombre": "Laura", "numero": "+573001112233", "autorizado": True, "evidencia": "Correo 2026-01-10"},
        format="json",
    )

    # 3. la pieza: copy distinto en Facebook, archivo valido, a publicar manana a las 9:00 (Colombia)
    when = timezone.now() + timedelta(days=1)
    pieza = staff.post(
        "/api/v1/piezas",
        {
            "cliente": cid,
            "tipo": "POST",
            "titulo": "Promo del dia",
            "programada_para": when.isoformat(),
            "copy_comun": "Hoy 2x1 en cafe de origen",
            "hashtags": ["cafe", "cali"],
            "mensaje_cliente": "Hola Laura, este es el post de hoy",
            "redes": [
                {"red": "INSTAGRAM"},
                {"red": "FACEBOOK", "copy": "2x1 hoy. Mas info: https://cafedelvalle.co"},
            ],
        },
        format="json",
    ).json()
    pid = pieza["id"]
    bad = staff.post(
        f"/api/v1/piezas/{pid}/archivos", {"archivo": image_file(1080, 1920)}, format="multipart"
    )
    assert bad.status_code == 400  # 9:16 no es valido para un post
    assert (
        staff.post(
            f"/api/v1/piezas/{pid}/archivos", {"archivo": image_file(1080, 1350)}, format="multipart"
        ).status_code
        == 201
    )
    staff.post(
        f"/api/v1/piezas/{pid}/archivo-editable",
        {"archivo": SimpleUploadedFile("diseno.psd", b"8BPS")},
        format="multipart",
    )

    # 4. revision: el personal envia, el admin aprueba; nada se programa sin aprobar
    assert (
        staff.post(f"/api/v1/piezas/{pid}/programar", {"confirmar": True}, format="json").status_code == 409
    )
    assert staff.post(f"/api/v1/piezas/{pid}/enviar-revision").json()["estado"] == "EN_REVISION"
    assert staff.post(f"/api/v1/piezas/{pid}/aprobar").status_code == 403
    assert admin.post(f"/api/v1/piezas/{pid}/aprobar").json()["estado"] == "APROBADA"

    # 5. programar: se revisa la vista previa y se confirma
    preview = staff.get(f"/api/v1/piezas/{pid}/programacion").json()
    assert preview["puede_confirmar"] and {r["red"] for r in preview["redes"]} == {"INSTAGRAM", "FACEBOOK"}
    assert (
        staff.post(f"/api/v1/piezas/{pid}/programar", {"confirmar": True}, format="json").json()["estado"]
        == "PROGRAMADA"
    )
    cronograma = staff.get(
        f"/api/v1/clientes/{cid}/cronograma", {"vista": "semana", "fecha": when.date().isoformat()}
    ).json()
    assert any(p["estado"] == "PROGRAMADA" for d in cronograma["dias"] for p in d["piezas"])
    assert staff.get("/api/v1/clientes/actuales").json()[0]["proximas_publicaciones"][0]["id"] == pid

    # 6. llega la hora: el worker crea las tareas y avisa a Maria por WhatsApp
    services.run_tick(now=when + timedelta(seconds=20))
    jobs.process_once(now=when + timedelta(seconds=30))
    assert [m["to"] for m in FakeGateway.sent] == ["+573004440000", "+573004440000"]
    pendientes = staff.get("/api/v1/publicaciones", {"estado": "PENDIENTE"}).json()["results"]
    by_red = {t["red"]: t for t in pendientes}
    assert by_red["FACEBOOK"]["copy_final"].startswith("2x1 hoy. Mas info:") and by_red["INSTAGRAM"][
        "copy_final"
    ].startswith("Hoy 2x1")
    assert by_red["INSTAGRAM"]["cuenta_handle"] == "@cafedelvalle"

    # 7. Instagram se publica; Facebook falla y el director recibe la alerta
    ig = staff.post(
        f"/api/v1/publicaciones/{by_red['INSTAGRAM']['id']}/confirmar",
        {"enlace": "https://www.instagram.com/p/XYZ/"},
        format="json",
    )
    assert ig.status_code == 200
    assert (
        staff.post(
            f"/api/v1/publicaciones/{by_red['FACEBOOK']['id']}/fallar",
            {"motivo": "Facebook pidio reautenticar"},
            format="json",
        ).status_code
        == 200
    )
    assert staff.get(f"/api/v1/piezas/{pid}").json()["estado"] == "PUBLICADA"

    # 8. el worker envia el contenido al cliente y la alerta al director
    FakeGateway.reset()
    jobs.process_once(now=when + timedelta(minutes=2))
    sent = {m["to"]: m for m in FakeGateway.sent}
    assert sent["+573001112233"]["template"] == "pieza_cliente_imagen"
    assert sent["+573001112233"]["body_params"] == ["Hola Laura, este es el post de hoy"]
    assert "Facebook pidio reautenticar" in sent["+573001110000"]["body_params"][0]
    assert staff.get(f"/api/v1/piezas/{pid}").json()["estado"] == "COMPARTIDA"

    # 9. reintento de Facebook y publicacion exitosa
    fb = by_red["FACEBOOK"]["id"]
    assert staff.post(f"/api/v1/publicaciones/{fb}/reintentar").json()["estado"] == "PENDIENTE"
    done = staff.post(
        f"/api/v1/publicaciones/{fb}/confirmar",
        {"enlace": "https://www.facebook.com/cafedelvalle/posts/9"},
        format="json",
    )
    assert done.json()["estado"] == "PUBLICADA"
    assert {t["estado"] for t in staff.get(f"/api/v1/piezas/{pid}/publicaciones").json()} == {"PUBLICADA"}

    # 10. el historial (solo admin) cuenta toda la historia
    assert staff.get("/api/v1/auditoria").status_code == 403
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {
        "LOGIN",
        "USER_CREATED",
        "PASSWORD_CHANGED",
        "CLIENT_CREATED",
        "NETWORK_LINKED",
        "PIECE_CREATED",
        "PIECE_SUBMITTED",
        "PIECE_APPROVED",
        "PIECE_SCHEDULED",
        "PUBLICATION_DUE",
        "PUBLICATION_PUBLISHED",
        "PUBLICATION_FAILED",
        "PUBLICATION_RETRIED",
        "WHATSAPP_SENT",
        "ALERT_SENT",
    } <= actions
    assert admin.get("/api/v1/auditoria", {"action": "PUBLICATION_FAILED"}).json()["count"] == 1
