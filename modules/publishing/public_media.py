"""URL publica firmada para que Meta descargue los archivos de una pieza.

La firma cubre el id del archivo y su vencimiento. La vista que la consume no exige sesion,
pero solo sirve archivos de piezas con una tarea de publicacion pendiente o fallida.
"""

import hashlib
import hmac
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

JPEG_EXTENSIONS = {".jpg", ".jpeg"}


def _expires_at(*, ttl: int | None, now) -> int:
    seconds = settings.PUBLIC_MEDIA_TTL_SECONDS if ttl is None else ttl
    return int((now or timezone.now()).timestamp()) + seconds


def _signature(archivo_id, expires: int) -> str:
    payload = f"{archivo_id}:{expires}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def build_public_media_url(archivo, *, ttl: int | None = None, now=None) -> str:
    """URL absoluta y firmada, valida durante `PUBLIC_MEDIA_TTL_SECONDS`."""
    base = settings.PUBLIC_BASE_URL.strip().rstrip("/")
    if not base:
        raise ImproperlyConfigured("PUBLIC_BASE_URL no puede estar vacia.")
    expires = _expires_at(ttl=ttl, now=now)
    route = reverse("public-media", kwargs={"archivo_id": archivo.pk})
    query = urlencode({"exp": expires, "sig": _signature(archivo.pk, expires)})
    return f"{base}{route}?{query}"


def signature_is_valid(archivo_id, expires, signature: str | None, *, now=None) -> bool:
    if not signature:
        return False
    try:
        expiration = int(expires)
    except (TypeError, ValueError):
        return False
    current = int((now or timezone.now()).timestamp())
    if expiration <= current:
        return False
    return hmac.compare_digest(signature, _signature(archivo_id, expiration))


def should_convert_to_jpeg(archivo) -> bool:
    if archivo.kind != "image":
        return False
    real_format = (archivo.formato or "").lower()
    if real_format:
        return real_format not in {"jpg", "jpeg"}
    name = archivo.nombre_original or archivo.archivo.name
    return Path(name).suffix.lower() not in JPEG_EXTENSIONS


def to_jpeg(archivo) -> ContentFile:
    """Convierte una imagen a JPEG en memoria; Instagram no acepta PNG."""
    try:
        archivo.archivo.open("rb")
        try:
            with Image.open(archivo.archivo) as image:
                image.load()
                if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    canvas = Image.new("RGB", rgba.size, (255, 255, 255))
                    canvas.paste(rgba, mask=rgba.getchannel("A"))
                    rgb = canvas
                else:
                    rgb = image.convert("RGB")
                buffer = BytesIO()
                rgb.save(buffer, format="JPEG", quality=90, optimize=True)
        finally:
            archivo.archivo.close()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("No se pudo convertir la imagen a JPEG.") from exc

    stem = Path(archivo.nombre_original or archivo.archivo.name).stem
    return ContentFile(buffer.getvalue(), name=f"{stem}.jpg")
