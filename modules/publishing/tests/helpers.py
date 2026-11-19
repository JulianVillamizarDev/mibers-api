from datetime import timedelta

from django.utils import timezone

from modules.clients.models import Cliente, ContactoWhatsApp, CuentaSocial
from modules.content import services as content
from modules.content.models import Pieza
from modules.content.tests.helpers import image_file, make_client


def make_client_with_accounts(nombre="Cafe del Valle", networks=("INSTAGRAM", "FACEBOOK")) -> Cliente:
    cliente = make_client(nombre)
    for red in networks:
        CuentaSocial.objects.create(
            cliente=cliente,
            red=red,
            handle=f"@{nombre.split()[0].lower()}_{red.lower()}",
            url_perfil=f"https://{red.lower()}.com/x",
        )
    return cliente


def add_contact(cliente, numero="+573001112233", nombre="Laura", autorizado=True) -> ContactoWhatsApp:
    from modules.clients import services as clients

    return clients.add_contact(cliente, nombre=nombre, numero=numero, autorizado=autorizado, acting_user=None)


def build_piece(
    cliente,
    staff,
    admin,
    *,
    redes=("INSTAGRAM",),
    when=None,
    approve=True,
    n_files=1,
    tipo="POST",
    copies=None,
    **fields,
) -> Pieza:
    """Pieza completa: archivos validos, copy, fecha futura; aprobada si `approve`."""
    when = when or timezone.now() + timedelta(days=2)
    pieza = content.create_piece(
        cliente=cliente,
        tipo=tipo,
        acting_user=staff,
        redes=[{"red": r, "copy": (copies or {}).get(r, "")} for r in redes],
        programada_para=when,
        copy_comun=fields.pop("copy_comun", "Ven por tu cafe"),
        titulo=fields.pop("titulo", "Promo"),
        **fields,
    )
    for i in range(n_files):
        content.add_file(pieza, image_file(name=f"f{i}.png"), acting_user=staff)
    if approve:
        pieza = content.submit_for_review(pieza, acting_user=staff)
        pieza = content.approve(pieza, acting_user=admin)
    return pieza


def reload(pieza) -> Pieza:
    return Pieza.objects.get(pk=pieza.pk)
