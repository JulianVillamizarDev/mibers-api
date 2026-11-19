from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from modules.audit.models import AuditLog
from modules.content import services
from modules.content.models import ArchivoPieza, ComentarioRevision, Estado, Pieza
from modules.content.signals import piece_state_changed

from .helpers import image_file, make_client, patch_video_probe, video_file

pytestmark = pytest.mark.django_db

PIECES = "/api/v1/piezas"


@pytest.fixture
def cliente():
    return make_client()


def future(days=3):
    return (timezone.now() + timedelta(days=days)).isoformat()


def new_piece(client, cliente, **extra):
    body = {
        "cliente": str(cliente.pk),
        "tipo": "POST",
        "titulo": "Promo de junio",
        "programada_para": future(),
        "copy_comun": "Ven por tu cafe",
        "redes": [{"red": "INSTAGRAM"}],
        **extra,
    }
    response = client.post(PIECES, body, format="json")
    assert response.status_code == 201, response.json()
    return response.json()


def upload(client, piece_id, file):
    return client.post(f"{PIECES}/{piece_id}/archivos", {"archivo": file}, format="multipart")


@pytest.fixture
def ready_piece(staff_client, cliente):
    """Pieza completa en borrador: 1 imagen valida para Instagram."""
    piece = new_piece(staff_client, cliente)
    assert upload(staff_client, piece["id"], image_file()).status_code == 201
    return piece


def state(piece_id):
    return Pieza.objects.get(pk=piece_id).estado


def actions():
    return list(AuditLog.objects.order_by("created_at").values_list("action", flat=True))


def approved(staff_client, admin_client, piece_id):
    staff_client.post(f"{PIECES}/{piece_id}/enviar-revision")
    assert admin_client.post(f"{PIECES}/{piece_id}/aprobar").status_code == 200


# --- RF9: ficha de la pieza -----------------------------------------------------------------------
def test_crea_pieza_en_borrador_con_ficha_completa(staff_client, staff_user, cliente):
    piece = new_piece(
        staff_client,
        cliente,
        hashtags=["cafe", "#cali"],
        enlaces=["https://cafedelvalle.co/promo"],
        campana="Junio",
        mensaje_cliente="Hola, aqui va el post de hoy",
    )
    assert piece["estado"] == "BORRADOR" and piece["tipo"] == "POST"
    assert piece["hashtags"] == ["#cafe", "#cali"]  # se normalizan
    assert piece["responsable"] == str(staff_user.pk)  # por defecto, quien la crea
    assert piece["campana"] == "Junio" and piece["enlaces"] == ["https://cafedelvalle.co/promo"]
    assert actions() == ["PIECE_CREATED"]


def test_la_hora_se_interpreta_y_se_devuelve_en_hora_de_colombia(staff_client, cliente):
    piece = new_piece(staff_client, cliente, programada_para="2026-08-10T09:00:00")
    assert piece["programada_para"] == "2026-08-10T09:00:00-05:00"
    assert Pieza.objects.get().programada_para.isoformat() == "2026-08-10T14:00:00+00:00"


def test_no_se_crean_piezas_para_clientes_desactivados(staff_client, cliente):
    cliente.activo = False
    cliente.save()
    response = staff_client.post(PIECES, {"cliente": str(cliente.pk), "tipo": "POST"}, format="json")
    assert response.status_code == 400 and response.json()["code"] == "client_inactive"


def test_red_y_tipo_deben_ser_validos(staff_client, cliente):
    bad_network = staff_client.post(
        PIECES, {"cliente": str(cliente.pk), "tipo": "POST", "redes": [{"red": "TIKTOK"}]}, format="json"
    )
    bad_type = staff_client.post(PIECES, {"cliente": str(cliente.pk), "tipo": "VIDEO"}, format="json")
    assert bad_network.status_code == 400 and bad_type.status_code == 400


def test_el_cliente_de_una_pieza_no_cambia(staff_client, cliente):
    piece = new_piece(staff_client, cliente)
    other = make_client("Otro")
    response = staff_client.patch(f"{PIECES}/{piece['id']}", {"cliente": str(other.pk)}, format="json")
    assert response.status_code == 400 and response.json()["code"] == "client_locked"


# --- RF11: copy comun o por red --------------------------------------------------------------------
def test_copy_comun_y_copy_distinto_por_red(staff_client, cliente):
    piece = new_piece(
        staff_client,
        cliente,
        copy_comun="Copy para todos",
        hashtags=["cafe"],
        redes=[{"red": "INSTAGRAM"}, {"red": "FACEBOOK", "copy": "Copy con enlace https://cafe.co"}],
    )
    finals = {r["red"]: r["copy_final"] for r in piece["redes"]}
    assert finals["INSTAGRAM"] == "Copy para todos\n\n#cafe"
    assert finals["FACEBOOK"] == "Copy con enlace https://cafe.co\n\n#cafe"
    # editar el de Facebook no altera el de Instagram
    patched = staff_client.patch(
        f"{PIECES}/{piece['id']}",
        {"redes": [{"red": "INSTAGRAM"}, {"red": "FACEBOOK", "copy": "Otro copy FB"}]},
        format="json",
    ).json()
    finals = {r["red"]: r["copy_final"] for r in patched["redes"]}
    assert finals["INSTAGRAM"] == "Copy para todos\n\n#cafe" and finals["FACEBOOK"].startswith("Otro copy FB")


def test_no_se_repiten_redes(staff_client, cliente):
    response = staff_client.post(
        PIECES,
        {"cliente": str(cliente.pk), "tipo": "POST", "redes": [{"red": "INSTAGRAM"}, {"red": "INSTAGRAM"}]},
        format="json",
    )
    assert response.status_code == 400


def test_quitar_una_red_elimina_su_fila(staff_client, cliente):
    piece = new_piece(staff_client, cliente, redes=[{"red": "INSTAGRAM"}, {"red": "FACEBOOK"}])
    patched = staff_client.patch(
        f"{PIECES}/{piece['id']}", {"redes": [{"red": "FACEBOOK"}]}, format="json"
    ).json()
    assert [r["red"] for r in patched["redes"]] == ["FACEBOOK"]


# --- RF8 / RNF08: archivos validados antes de aceptarse -----------------------------------------------
def test_sube_imagen_valida_y_guarda_sus_metadatos(staff_client, cliente):
    piece = new_piece(staff_client, cliente)
    response = upload(staff_client, piece["id"], image_file(1080, 1350, "Foto Cafe.png"))
    assert response.status_code == 201
    body = response.json()
    assert (body["ancho"], body["alto"], body["kind"], body["formato"]) == (1080, 1350, "image", "png")
    assert body["nombre_original"] == "Foto Cafe.png"
    stored = ArchivoPieza.objects.get().archivo
    assert (
        stored.name.startswith(f"clientes/{cliente.pk}/piezas/{piece['id']}/") and "Foto" not in stored.name
    )


def test_rechaza_imagen_con_aspecto_invalido_y_no_la_guarda(staff_client, cliente):
    piece = new_piece(staff_client, cliente)  # Instagram POST: 0.8 a 1.91
    response = upload(staff_client, piece["id"], image_file(1080, 1920))
    assert response.status_code == 400 and response.json()["code"] == "invalid_media"
    assert any("aspecto" in e for e in response.json()["detail"])
    assert ArchivoPieza.objects.count() == 0
    assert not [p for p in (Path(settings.MEDIA_ROOT) / "clientes").rglob("*") if p.is_file()]


def test_valida_contra_todas_las_redes_destino(staff_client, cliente):
    piece = new_piece(
        staff_client, cliente, tipo="PORTADA", redes=[{"red": "INSTAGRAM"}, {"red": "FACEBOOK"}]
    )
    # 1:1 sirve para Instagram pero no para la portada de Facebook
    response = upload(staff_client, piece["id"], image_file(1080, 1080))
    assert response.status_code == 400
    assert any("Facebook" in e for e in response.json()["detail"])


def test_rechaza_archivo_que_finge_ser_imagen(staff_client, cliente):
    piece = new_piece(staff_client, cliente)
    fake = SimpleUploadedFile("falsa.png", b"esto no es una imagen", content_type="image/png")
    response = upload(staff_client, piece["id"], fake)
    assert response.status_code == 400 and ArchivoPieza.objects.count() == 0


@pytest.mark.parametrize("name", ["malware.exe", "script.sh", "pagina.html", "sin_extension"])
def test_rechaza_extensiones_no_permitidas(staff_client, cliente, name):
    piece = new_piece(staff_client, cliente)
    response = upload(
        staff_client, piece["id"], SimpleUploadedFile(name, b"MZ", content_type="application/octet-stream")
    )
    assert response.status_code == 400 and ArchivoPieza.objects.count() == 0


def test_reel_valida_duracion_con_la_info_del_video(staff_client, cliente, monkeypatch):
    piece = new_piece(staff_client, cliente, tipo="REEL")
    patch_video_probe(monkeypatch, 1080, 1920, duration=120)  # supera 90 s
    assert upload(staff_client, piece["id"], video_file()).status_code == 400
    patch_video_probe(monkeypatch, 1080, 1920, duration=30)
    ok = upload(staff_client, piece["id"], video_file())
    assert ok.status_code == 201 and ok.json()["duracion"] == 30 and ok.json()["kind"] == "video"


def test_un_post_no_admite_mas_de_un_archivo_en_revision(staff_client, ready_piece):
    upload(staff_client, ready_piece["id"], image_file(1080, 1080, "otra.png"))
    response = staff_client.post(f"{PIECES}/{ready_piece['id']}/enviar-revision")
    assert response.status_code == 400 and response.json()["code"] == "piece_incomplete"
    assert any("maximo" in e for e in response.json()["detail"])


def test_reordena_y_elimina_archivos(staff_client, cliente):
    piece = new_piece(staff_client, cliente, tipo="CARRUSEL")
    first = upload(staff_client, piece["id"], image_file(name="a.png")).json()
    second = upload(staff_client, piece["id"], image_file(name="b.png")).json()
    reordered = staff_client.put(
        f"{PIECES}/{piece['id']}/archivos/orden", {"ids": [second["id"], first["id"]]}, format="json"
    )
    assert [a["nombre_original"] for a in reordered.json()["archivos"]] == ["b.png", "a.png"]
    assert (
        staff_client.put(
            f"{PIECES}/{piece['id']}/archivos/orden", {"ids": [first["id"]]}, format="json"
        ).status_code
        == 400
    )
    assert staff_client.delete(f"{PIECES}/{piece['id']}/archivos/{first['id']}").status_code == 204
    assert ArchivoPieza.objects.count() == 1


def test_archivo_se_descarga_solo_con_sesion(staff_client, api_client, ready_piece):
    url = ready_piece and ArchivoPieza.objects.get().pk
    assert staff_client.get(f"/api/v1/archivos/{url}").status_code == 200
    assert api_client.get(f"/api/v1/archivos/{url}").status_code == 401


def test_adjunta_archivo_editable(staff_client, ready_piece):
    psd = SimpleUploadedFile("diseno.psd", b"8BPS" + b"0" * 50, content_type="application/octet-stream")
    response = staff_client.post(
        f"{PIECES}/{ready_piece['id']}/archivo-editable", {"archivo": psd}, format="multipart"
    )
    assert response.status_code == 200
    assert (
        response.json()["tiene_archivo_editable"]
        and response.json()["archivo_editable_nombre"] == "diseno.psd"
    )
    assert staff_client.get(f"{PIECES}/{ready_piece['id']}/archivo-editable").status_code == 200
    exe = SimpleUploadedFile("x.exe", b"MZ")
    assert (
        staff_client.post(
            f"{PIECES}/{ready_piece['id']}/archivo-editable", {"archivo": exe}, format="multipart"
        ).status_code
        == 400
    )


# --- RF12: revisar y aprobar ------------------------------------------------------------------------------
def test_ficha_incompleta_no_puede_enviarse_a_revision(staff_client, cliente):
    piece = staff_client.post(PIECES, {"cliente": str(cliente.pk), "tipo": "POST"}, format="json").json()
    response = staff_client.post(f"{PIECES}/{piece['id']}/enviar-revision")
    assert response.status_code == 400 and response.json()["code"] == "piece_incomplete"
    detail = " ".join(response.json()["detail"])
    assert "red destino" in detail and "fecha" in detail
    assert state(piece["id"]) == "BORRADOR"


def test_falta_copy_o_archivos(staff_client, cliente):
    piece = new_piece(staff_client, cliente, copy_comun="")
    detail = " ".join(staff_client.post(f"{PIECES}/{piece['id']}/enviar-revision").json()["detail"])
    assert "falta el copy" in detail and "al menos 1 archivo" in detail


def test_personal_envia_a_revision(staff_client, ready_piece):
    response = staff_client.post(f"{PIECES}/{ready_piece['id']}/enviar-revision")
    assert response.status_code == 200 and response.json()["estado"] == "EN_REVISION"
    assert ComentarioRevision.objects.get().decision == "ENVIADA"
    assert actions()[-1] == "PIECE_SUBMITTED"


def test_solo_el_admin_aprueba_o_pide_cambios(staff_client, admin_client, ready_piece):
    piece_id = ready_piece["id"]
    staff_client.post(f"{PIECES}/{piece_id}/enviar-revision")
    assert staff_client.post(f"{PIECES}/{piece_id}/aprobar").status_code == 403
    assert (
        staff_client.post(
            f"{PIECES}/{piece_id}/solicitar-cambios", {"comentario": "x"}, format="json"
        ).status_code
        == 403
    )
    assert state(piece_id) == "EN_REVISION"
    assert (
        admin_client.post(
            f"{PIECES}/{piece_id}/aprobar", {"comentario": "Perfecta"}, format="json"
        ).status_code
        == 200
    )
    assert state(piece_id) == "APROBADA"
    assert actions()[-1] == "PIECE_APPROVED"


def test_pedir_cambios_exige_comentario_y_permite_reenviar(staff_client, admin_client, ready_piece):
    piece_id = ready_piece["id"]
    staff_client.post(f"{PIECES}/{piece_id}/enviar-revision")
    missing = admin_client.post(f"{PIECES}/{piece_id}/solicitar-cambios", {"comentario": "  "}, format="json")
    assert missing.status_code == 400 and missing.json()["code"] == "comment_required"
    ok = admin_client.post(
        f"{PIECES}/{piece_id}/solicitar-cambios", {"comentario": "Cambia el logo"}, format="json"
    )
    assert ok.json()["estado"] == "CAMBIOS_SOLICITADOS"
    reviews = staff_client.get(f"{PIECES}/{piece_id}/revisiones").json()
    assert [r["decision"] for r in reviews] == ["ENVIADA", "CAMBIOS"] and reviews[1][
        "comentario"
    ] == "Cambia el logo"
    assert staff_client.post(f"{PIECES}/{piece_id}/enviar-revision").json()["estado"] == "EN_REVISION"


def test_no_se_aprueba_lo_que_no_esta_en_revision(admin_client, ready_piece):
    response = admin_client.post(f"{PIECES}/{ready_piece['id']}/aprobar")
    assert response.status_code == 409 and response.json()["code"] == "invalid_transition"


def test_servicio_de_aprobacion_tambien_exige_admin(staff_client, staff_user, ready_piece):
    staff_client.post(f"{PIECES}/{ready_piece['id']}/enviar-revision")
    from modules.core.exceptions import DomainError

    with pytest.raises(DomainError) as error:
        services.approve(Pieza.objects.get(pk=ready_piece["id"]), acting_user=staff_user)
    assert error.value.code == "admin_required"


# --- RF12: editar contenido aprobado la devuelve a revision ----------------------------------------------------
def test_editar_el_copy_de_una_pieza_aprobada_la_devuelve_a_revision(staff_client, admin_client, ready_piece):
    approved(staff_client, admin_client, ready_piece["id"])
    response = staff_client.patch(
        f"{PIECES}/{ready_piece['id']}", {"copy_comun": "Copy nuevo"}, format="json"
    )
    assert response.json()["estado"] == "EN_REVISION"
    assert ComentarioRevision.objects.filter(decision="DEVUELTA").count() == 1
    assert "PIECE_RETURNED_TO_REVIEW" in actions()


def test_editar_hashtags_o_copy_por_red_tambien_la_devuelve(staff_client, admin_client, ready_piece):
    approved(staff_client, admin_client, ready_piece["id"])
    staff_client.patch(f"{PIECES}/{ready_piece['id']}", {"hashtags": ["nuevo"]}, format="json")
    assert state(ready_piece["id"]) == "EN_REVISION"
    # y se puede volver a aprobar
    staff_client.post(f"{PIECES}/{ready_piece['id']}/enviar-revision")
    admin_client.post(f"{PIECES}/{ready_piece['id']}/aprobar")
    staff_client.patch(
        f"{PIECES}/{ready_piece['id']}", {"redes": [{"red": "INSTAGRAM", "copy": "Propio"}]}, format="json"
    )
    assert state(ready_piece["id"]) == "EN_REVISION"


def test_agregar_quitar_o_reordenar_archivos_de_una_aprobada_la_devuelve(staff_client, admin_client, cliente):
    piece = new_piece(staff_client, cliente, tipo="CARRUSEL")
    a = upload(staff_client, piece["id"], image_file(name="a.png")).json()
    upload(staff_client, piece["id"], image_file(name="b.png"))
    for operation in ("add", "remove", "reorder"):
        approved(staff_client, admin_client, piece["id"])
        if operation == "add":
            assert upload(staff_client, piece["id"], image_file(name="c.png")).status_code == 201
        elif operation == "remove":
            assert staff_client.delete(f"{PIECES}/{piece['id']}/archivos/{a['id']}").status_code == 204
        else:
            ids = [str(x.pk) for x in ArchivoPieza.objects.order_by("orden")]
            staff_client.put(f"{PIECES}/{piece['id']}/archivos/orden", {"ids": ids[::-1]}, format="json")
        assert state(piece["id"]) == "EN_REVISION", operation
        if operation == "remove":  # el carrusel quedo con 2 archivos: sigue siendo valido
            assert ArchivoPieza.objects.count() == 2


def test_cambios_que_no_son_de_contenido_no_devuelven_a_revision(staff_client, admin_client, ready_piece):
    approved(staff_client, admin_client, ready_piece["id"])
    staff_client.patch(
        f"{PIECES}/{ready_piece['id']}",
        {"titulo": "Otro titulo", "programada_para": future(5), "campana": "X", "mensaje_cliente": "Hola"},
        format="json",
    )
    assert state(ready_piece["id"]) == "APROBADA"
    psd = SimpleUploadedFile("d.psd", b"8BPS")
    staff_client.post(f"{PIECES}/{ready_piece['id']}/archivo-editable", {"archivo": psd}, format="multipart")
    assert state(ready_piece["id"]) == "APROBADA"


def test_guardar_el_mismo_copy_no_cuenta_como_edicion(staff_client, admin_client, ready_piece):
    approved(staff_client, admin_client, ready_piece["id"])
    staff_client.patch(f"{PIECES}/{ready_piece['id']}", {"copy_comun": "Ven por tu cafe"}, format="json")
    assert state(ready_piece["id"]) == "APROBADA"


def test_pieza_programada_o_fallida_tambien_vuelve_a_revision(
    staff_client, admin_client, ready_piece, admin_user
):
    approved(staff_client, admin_client, ready_piece["id"])
    pieza = Pieza.objects.get(pk=ready_piece["id"])
    services.change_state(pieza, Estado.PROGRAMADA, actor=admin_user)
    staff_client.patch(f"{PIECES}/{ready_piece['id']}", {"copy_comun": "Cambio"}, format="json")
    assert state(ready_piece["id"]) == "EN_REVISION"


def test_el_cambio_de_estado_emite_senal(admin_user, ready_piece):
    received = []
    piece_state_changed.connect(lambda sender, **kw: received.append(kw), weak=False, dispatch_uid="t")
    try:
        services.change_state(Pieza.objects.get(pk=ready_piece["id"]), Estado.EN_REVISION, actor=admin_user)
    finally:
        piece_state_changed.disconnect(dispatch_uid="t")
    assert received[0]["old_state"] == "BORRADOR" and received[0]["new_state"] == "EN_REVISION"


def test_una_pieza_publicada_no_se_edita(staff_client, ready_piece):
    Pieza.objects.filter(pk=ready_piece["id"]).update(estado="PUBLICADA")
    response = staff_client.patch(f"{PIECES}/{ready_piece['id']}", {"copy_comun": "x"}, format="json")
    assert response.status_code == 409 and response.json()["code"] == "piece_locked"
    assert upload(staff_client, ready_piece["id"], image_file()).status_code == 409


def test_el_tipo_solo_cambia_en_borrador(staff_client, admin_client, ready_piece):
    assert (
        staff_client.patch(f"{PIECES}/{ready_piece['id']}", {"tipo": "CARRUSEL"}, format="json").status_code
        == 200
    )
    staff_client.patch(f"{PIECES}/{ready_piece['id']}", {"tipo": "POST"}, format="json")
    approved(staff_client, admin_client, ready_piece["id"])
    assert (
        staff_client.patch(f"{PIECES}/{ready_piece['id']}", {"tipo": "CARRUSEL"}, format="json").status_code
        == 409
    )


# --- Consultas y filtros (RF10) ------------------------------------------------------------------------------------
def test_filtra_por_estado_cliente_red_y_tipo(staff_client, admin_client, cliente):
    other = make_client("Zapateria")
    a = new_piece(staff_client, cliente, titulo="A")
    new_piece(staff_client, other, titulo="B", tipo="REEL", redes=[{"red": "FACEBOOK"}])
    Pieza.objects.filter(pk=a["id"]).update(estado="APROBADA")

    def titles(**params):
        return sorted(p["titulo"] for p in staff_client.get(PIECES, params).json()["results"])

    assert titles() == ["A", "B"]
    assert titles(estado="APROBADA") == ["A"]
    assert titles(estado="APROBADA,BORRADOR") == ["A", "B"]
    assert titles(cliente=str(other.pk)) == ["B"]
    assert titles(red="FACEBOOK") == ["B"]
    assert titles(tipo="REEL") == ["B"]
    assert titles(search="zzz") == []


def test_la_lista_es_paginada_y_exige_sesion(api_client, staff_client, cliente):
    for i in range(3):
        new_piece(staff_client, cliente, titulo=f"P{i}")
    body = staff_client.get(PIECES, {"page_size": 2}).json()
    assert body["count"] == 3 and len(body["results"]) == 2 and body["next"]
    assert api_client.get(PIECES).status_code == 401


def test_solo_se_eliminan_borradores_y_se_borran_sus_archivos(staff_client, admin_client, ready_piece):
    stored = Path(ArchivoPieza.objects.get().archivo.path)
    assert stored.exists()
    approved(staff_client, admin_client, ready_piece["id"])
    assert staff_client.delete(f"{PIECES}/{ready_piece['id']}").status_code == 409
    Pieza.objects.filter(pk=ready_piece["id"]).update(estado="BORRADOR")
    assert staff_client.delete(f"{PIECES}/{ready_piece['id']}").status_code == 204
    assert not stored.exists() and Pieza.objects.count() == 0
