from datetime import date
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from modules.clients.models import Cliente
from modules.content import media
from modules.content.media_rules import MediaInfo


def make_client(nombre="Cafe del Valle", **extra) -> Cliente:
    return Cliente.objects.create(
        nombre=nombre, posts_por_semana=3, contrato_inicio=date(2026, 1, 1), estrategia="POSICIONAR", **extra
    )


def image_file(width=1080, height=1350, name="foto.png", fmt="PNG") -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format=fmt)
    content_type = "image/png" if fmt == "PNG" else "image/jpeg"
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


def video_file(name="reel.mp4") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"\x00\x00\x00 ftypmp42" + b"0" * 200, content_type="video/mp4")


def patch_video_probe(monkeypatch, width=1080, height=1920, duration=30.0):
    """ffprobe no esta disponible en todos los entornos de prueba: se simula su resultado."""
    monkeypatch.setattr(media, "probe_video", lambda path: (width, height, duration))


def info(kind="image", fmt="png", width=1080, height=1350, size=1_000_000, duration=None) -> MediaInfo:
    return MediaInfo(kind=kind, fmt=fmt, width=width, height=height, size=size, duration=duration)
