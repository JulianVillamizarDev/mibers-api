import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from modules.audit.models import AuditLog
from modules.core import jobs
from modules.core.models import Job
from modules.notifications import services
from modules.notifications.gateway import FakeGateway, WhatsAppError
from modules.notifications.models import Alerta, EnvioWhatsApp

pytestmark = pytest.mark.django_db

P = EnvioWhatsApp.Proposito


@pytest.fixture(autouse=True)
def fake_gateway(settings):
    settings.WHATSAPP_BACKEND = "fake"
    FakeGateway.reset()
    yield FakeGateway
    FakeGateway.reset()


@pytest.fixture
def admin_with_whatsapp(admin_user):
    admin_user.whatsapp_number = "+573001110000"
    admin_user.save()
    return admin_user


def run_jobs(now=None):
    return jobs.process_once(now=now)


def actions():
    return list(AuditLog.objects.order_by("created_at").values_list("action", flat=True))


def write_media(settings, name="a.png", size=1000):
    path = Path(settings.MEDIA_ROOT) / "clientes" / "x" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)
    return f"clientes/x/{name}"


# --- Envio por el gateway (RF18) ----------------------------------------------------------------------
def test_el_worker_envia_el_mensaje_y_registra_el_id(fake_gateway):
    envio = services.queue_message(
        proposito=P.CLIENTE,
        destino="+573001112233",
        destino_nombre="Laura",
        plantilla="t",
        parametros=["Hola"],
    )
    assert envio.estado == "PENDIENTE"
    run_jobs()
    envio.refresh_from_db()
    assert envio.estado == "ENVIADO" and envio.wa_message_id == "wamid.FAKE00001" and envio.enviado_at
    assert fake_gateway.sent[0]["to"] == "+573001112233" and fake_gateway.sent[0]["body_params"] == ["Hola"]
    assert "WHATSAPP_SENT" in actions()


def test_el_envio_respeta_run_at(fake_gateway):
    services.queue_message(
        proposito=P.CLIENTE,
        destino="+573001112233",
        plantilla="t",
        run_at=timezone.now() + timedelta(hours=1),
    )
    run_jobs()
    assert fake_gateway.sent == []
    run_jobs(now=timezone.now() + timedelta(hours=2))
    assert len(fake_gateway.sent) == 1


def test_adjunta_el_archivo_y_elige_plantilla_segun_el_tipo(fake_gateway, settings):
    services.queue_message(
        proposito=P.CLIENTE,
        destino="+573001112233",
        parametros=["Mira"],
        media_ruta=write_media(settings, "a.png", 1000),
        media_nombre="a.png",
        media_kind="image",
    )
    run_jobs()
    sent = fake_gateway.sent[0]
    assert sent["header"].kind == "image" and sent["template"] == settings.WHATSAPP_TEMPLATE_CLIENT_IMAGE


def test_archivo_grande_viaja_como_documento(fake_gateway, settings):
    services.queue_message(
        proposito=P.CLIENTE,
        destino="+573001112233",
        media_ruta=write_media(settings, "reel.mp4", 17 * 1024 * 1024),
        media_nombre="reel.mp4",
        media_kind="video",
    )
    run_jobs()
    assert fake_gateway.sent[0]["header"].kind == "document"
    assert fake_gateway.sent[0]["template"] == settings.WHATSAPP_TEMPLATE_CLIENT_DOCUMENT


def test_archivo_mayor_a_100mb_falla_sin_reintentos(fake_gateway, settings, admin_with_whatsapp):
    path = Path(settings.MEDIA_ROOT) / "clientes" / "x" / "gigante.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(101 * 1024 * 1024)  # archivo disperso: no ocupa 101 MB reales
    envio = services.queue_message(
        proposito=P.CLIENTE,
        destino="+573001112233",
        media_ruta="clientes/x/gigante.mp4",
        media_nombre="gigante.mp4",
        media_kind="video",
    )
    run_jobs()
    envio.refresh_from_db()
    assert envio.estado == "FALLIDO" and "100 MB" in envio.error and fake_gateway.sent == []


def test_archivo_inexistente_se_marca_fallido(fake_gateway):
    envio = services.queue_message(
        proposito=P.CLIENTE,
        destino="+573001112233",
        media_ruta="clientes/x/no-existe.png",
        media_kind="image",
    )
    run_jobs()
    envio.refresh_from_db()
    assert envio.estado == "FALLIDO" and "ya no existe" in envio.error


def test_error_transitorio_se_reintenta_y_luego_se_envia(fake_gateway):
    envio = services.queue_message(proposito=P.CLIENTE, destino="+573001112233", plantilla="t")
    fake_gateway.fail_with = WhatsAppError("timeout", retryable=True)
    now = timezone.now()
    run_jobs(now=now)
    envio.refresh_from_db()
    assert envio.estado == "PENDIENTE"  # sigue pendiente, el job reintentara
    run_jobs(now=now + timedelta(seconds=31))
    envio.refresh_from_db()
    assert envio.estado == "ENVIADO" and len(fake_gateway.sent) == 1


def test_error_definitivo_marca_fallido_audita_y_alerta_al_admin(fake_gateway, admin_with_whatsapp):
    envio = services.queue_message(
        proposito=P.CLIENTE,
        destino="+573001112233",
        destino_nombre="Laura",
        plantilla="t",
        entity_type="content.pieza",
        entity_id="p1",
    )
    fake_gateway.fail_with = WhatsAppError("numero sin WhatsApp", retryable=False, code=131026)
    run_jobs()
    envio.refresh_from_db()
    assert envio.estado == "FALLIDO" and "sin WhatsApp" in envio.error
    assert "WHATSAPP_FAILED" in actions()
    alerta = Alerta.objects.get()
    assert alerta.tipo == "ENVIO_CLIENTE_FALLIDO" and alerta.entity_id == "p1" and "Laura" in alerta.mensaje
    # la alerta llega al WhatsApp del administrador en el siguiente ciclo
    run_jobs()
    assert fake_gateway.sent[0]["to"] == "+573001110000"


def test_error_transitorio_en_el_ultimo_intento_queda_fallido(fake_gateway):
    envio = services.queue_message(proposito=P.RECORDATORIO, destino="+573001112233", plantilla="t")
    Job.objects.update(max_attempts=1)
    fake_gateway.fail_with = WhatsAppError("sigue caido", retryable=True)
    run_jobs()
    envio.refresh_from_db()
    assert envio.estado == "FALLIDO"


def test_un_envio_ya_procesado_no_se_repite(fake_gateway):
    envio = services.queue_message(proposito=P.CLIENTE, destino="+573001112233", plantilla="t")
    run_jobs()
    services.deliver(envio.pk, last_attempt=False)
    assert len(fake_gateway.sent) == 1


def test_destino_y_nombre_se_guardan_cifrados():
    services.queue_message(
        proposito=P.CLIENTE, destino="+573001112233", destino_nombre="Laura Gomez", plantilla="t"
    )
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT destino, destino_nombre FROM notifications_enviowhatsapp")
        raw = cursor.fetchone()
    assert all(v.startswith("enc1:") for v in raw)


# --- RF19 / RNF12: alertas al administrador ---------------------------------------------------------------
def test_alerta_llega_por_whatsapp_a_cada_admin_activo_con_numero(
    fake_gateway, admin_with_whatsapp, staff_user
):
    from django.contrib.auth import get_user_model

    from modules.conftest import PASSWORD

    User = get_user_model()
    User.objects.create_user("otro_admin", password=PASSWORD, role="ADMIN", whatsapp_number="+573002220000")
    User.objects.create_user("sin_numero", password=PASSWORD, role="ADMIN")
    User.objects.create_user(
        "inactivo", password=PASSWORD, role="ADMIN", whatsapp_number="+573003330000", is_active=False
    )
    staff_user.whatsapp_number = "+573004440000"  # el personal NO recibe alertas
    staff_user.save()

    alerta = services.send_alert("PUBLICACION_FALLIDA", "Instagram fallo para Cafe del Valle")
    assert alerta is not None and Alerta.objects.count() == 1
    run_jobs()
    assert sorted(m["to"] for m in fake_gateway.sent) == ["+573001110000", "+573002220000"]
    assert all("Publicacion fallida: Instagram fallo" in m["body_params"][0] for m in fake_gateway.sent)
    assert fake_gateway.sent[0]["template"] == "alerta_director"
    assert "ALERT_SENT" in actions()


def test_alerta_sin_whatsapp_configurado_igual_queda_registrada(fake_gateway, admin_user):
    assert services.send_alert("CUENTA_EXPIRADA", "Se vencio el token") is not None
    run_jobs()
    assert Alerta.objects.count() == 1 and fake_gateway.sent == []


def test_alerta_se_entrega_dentro_de_5_minutos(fake_gateway, admin_with_whatsapp):
    """RNF12: con el worker ciclando cada 10 s la alerta sale en el primer ciclo tras el evento."""
    services.send_alert("PUBLICACION_FALLIDA", "x")
    run_jobs(now=timezone.now() + timedelta(seconds=10))
    assert len(fake_gateway.sent) == 1


def test_alerta_con_la_misma_clave_no_se_repite(fake_gateway, admin_with_whatsapp):
    assert services.send_alert("PUBLICACION_BLOQUEADA", "x", dedupe_key="bloq:1") is not None
    assert services.send_alert("PUBLICACION_BLOQUEADA", "x", dedupe_key="bloq:1") is None
    run_jobs()
    assert Alerta.objects.count() == 1 and len(fake_gateway.sent) == 1


def test_un_fallo_de_alerta_no_genera_otra_alerta(fake_gateway, admin_with_whatsapp):
    services.send_alert("PUBLICACION_FALLIDA", "x")
    fake_gateway.fail_with = WhatsAppError("rechazado", retryable=False)
    run_jobs()
    assert Alerta.objects.count() == 1  # sin bucles de alertas


def test_recordatorio_solo_si_el_responsable_tiene_numero(fake_gateway, staff_user):
    assert services.send_staff_reminder(staff_user, "Publica ahora") is None
    staff_user.whatsapp_number = "+573004440000"
    staff_user.save()
    assert services.send_staff_reminder(staff_user, "Publica ahora") is not None
    run_jobs()
    assert fake_gateway.sent[0]["template"] == "recordatorio_publicacion"


# --- Estados de entrega (RF18) -------------------------------------------------------------------------------
@pytest.fixture
def sent_envio(fake_gateway):
    envio = services.queue_message(
        proposito=P.CLIENTE, destino="+573001112233", plantilla="t", entity_id="p1"
    )
    run_jobs()
    envio.refresh_from_db()
    return envio


def test_estados_de_entrega_solo_avanzan(sent_envio):
    assert services.apply_status_update(sent_envio.wa_message_id, "delivered")
    sent_envio.refresh_from_db()
    assert sent_envio.estado == "ENTREGADO" and sent_envio.entregado_at
    assert not services.apply_status_update(sent_envio.wa_message_id, "sent")  # no retrocede
    assert services.apply_status_update(sent_envio.wa_message_id, "read")
    sent_envio.refresh_from_db()
    assert sent_envio.estado == "LEIDO"


def test_fallo_de_entrega_marca_fallido_y_alerta(sent_envio, admin_with_whatsapp):
    assert services.apply_status_update(sent_envio.wa_message_id, "failed", "Numero no registrado")
    sent_envio.refresh_from_db()
    assert sent_envio.estado == "FALLIDO" and "no registrado" in sent_envio.error
    assert Alerta.objects.get().tipo == "ENVIO_CLIENTE_FALLIDO"


def test_un_mensaje_ya_entregado_no_pasa_a_fallido(sent_envio):
    services.apply_status_update(sent_envio.wa_message_id, "delivered")
    assert not services.apply_status_update(sent_envio.wa_message_id, "failed")


def test_estados_desconocidos_o_ids_ajenos_se_ignoran(sent_envio):
    assert not services.apply_status_update("wamid.OTRO", "delivered")
    assert not services.apply_status_update(sent_envio.wa_message_id, "inventado")


def test_cambio_de_estado_emite_senal(sent_envio):
    seen = []
    services.envio_actualizado.connect(
        lambda sender, **kw: seen.append(kw["envio"].estado), weak=False, dispatch_uid="t"
    )
    try:
        services.apply_status_update(sent_envio.wa_message_id, "delivered")
    finally:
        services.envio_actualizado.disconnect(dispatch_uid="t")
    assert seen == ["ENTREGADO"]


# --- Webhook de Meta --------------------------------------------------------------------------------------------
WEBHOOK = "/api/v1/webhooks/whatsapp"
SECRET = "app-secret-de-prueba"


def signed(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        WEBHOOK, data=body, content_type="application/json", headers={"X-Hub-Signature-256": signature}
    )


def status_payload(message_id, status, title=""):
    entry = {"id": message_id, "status": status}
    if title:
        entry["errors"] = [{"title": title}]
    return {"entry": [{"changes": [{"value": {"statuses": [entry]}}]}]}


@pytest.fixture
def webhook_settings(settings):
    settings.WHATSAPP_APP_SECRET = SECRET
    settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN = "verify-me"


def test_verificacion_del_webhook(client, webhook_settings):
    ok = client.get(
        WEBHOOK, {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"}
    )
    assert ok.status_code == 200 and ok.content == b"12345"
    assert (
        client.get(
            WEBHOOK, {"hub.mode": "subscribe", "hub.verify_token": "mal", "hub.challenge": "1"}
        ).status_code
        == 403
    )


def test_verificacion_rechazada_si_no_hay_token_configurado(client, settings):
    settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN = ""
    assert (
        client.get(
            WEBHOOK, {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}
        ).status_code
        == 403
    )


def test_webhook_actualiza_estados_con_firma_valida(client, webhook_settings, sent_envio):
    response = signed(client, status_payload(sent_envio.wa_message_id, "delivered"))
    assert response.status_code == 200 and response.json() == {"updated": 1}
    sent_envio.refresh_from_db()
    assert sent_envio.estado == "ENTREGADO"


def test_webhook_registra_el_motivo_del_fallo(client, webhook_settings, sent_envio):
    signed(client, status_payload(sent_envio.wa_message_id, "failed", "Re-engagement message"))
    sent_envio.refresh_from_db()
    assert sent_envio.estado == "FALLIDO" and "Re-engagement" in sent_envio.error


def test_webhook_rechaza_firma_invalida(client, webhook_settings, sent_envio):
    assert (
        signed(client, status_payload(sent_envio.wa_message_id, "delivered"), secret="otro").status_code
        == 403
    )
    sent_envio.refresh_from_db()
    assert sent_envio.estado == "ENVIADO"


def test_webhook_rechaza_si_no_hay_firma_ni_secreto(client, settings, sent_envio):
    settings.WHATSAPP_APP_SECRET = ""
    body = json.dumps(status_payload(sent_envio.wa_message_id, "delivered"))
    assert client.post(WEBHOOK, data=body, content_type="application/json").status_code == 403
    settings.WHATSAPP_APP_SECRET = SECRET
    assert client.post(WEBHOOK, data=body, content_type="application/json").status_code == 403  # sin cabecera


def test_webhook_ignora_eventos_sin_estados(client, webhook_settings):
    assert signed(client, {"entry": [{"changes": [{"value": {"messages": []}}]}]}).json() == {"updated": 0}


# --- API de alertas (solo admin) ------------------------------------------------------------------------------------
def test_admin_lista_y_marca_alertas_como_leidas(admin_client, staff_client, admin_user):
    first = services.send_alert("CUENTA_EXPIRADA", "uno")
    services.send_alert("CUENTA_DESVINCULADA", "dos")
    assert staff_client.get("/api/v1/alertas").status_code == 403
    assert admin_client.get("/api/v1/alertas").json()["count"] == 2
    read = admin_client.post(f"/api/v1/alertas/{first.pk}/leer")
    assert read.status_code == 200 and read.json()["leida_at"]
    assert admin_client.get("/api/v1/alertas", {"leida": "false"}).json()["count"] == 1
    assert (
        admin_client.get("/api/v1/alertas", {"leida": "true"}).json()["results"][0]["tipo_label"]
        == "Cuenta de red expirada"
    )
