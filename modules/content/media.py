"""Inspeccion de archivos subidos: formato real, dimensiones y duracion (RF8)."""

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from modules.core.exceptions import DomainError

from .media_rules import ALLOWED_EXTENSIONS, MediaInfo

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}


class InvalidMedia(DomainError):
    def __init__(self, errors: list[str]):
        super().__init__(errors, "invalid_media", 400)


def probe_video(path: str) -> tuple[int, int, float]:
    """(ancho, alto, duracion en segundos) con ffprobe."""
    if shutil.which("ffprobe") is None:
        raise DomainError("ffprobe no esta instalado en el servidor.", "ffprobe_missing", 503)
    result = subprocess.run(  # noqa: S603
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise InvalidMedia(["El archivo de video esta danado o no es un video valido."])
    data = json.loads(result.stdout or "{}")
    try:
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"]), float(data["format"]["duration"])
    except (KeyError, IndexError, ValueError) as exc:
        raise InvalidMedia(["No se pudo leer la informacion del video."]) from exc


def inspect_file(path: str, filename: str) -> MediaInfo:
    """Lee el archivo real (no confia en la extension ni en el content-type del cliente)."""
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise InvalidMedia(
            [f"Extension no permitida ({extension or 'sin extension'}). Usa JPG, PNG, MP4 o MOV."]
        )
    size = Path(path).stat().st_size
    if extension in IMAGE_EXTENSIONS:
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:  # verify() invalida el objeto: se reabre
                fmt = (image.format or "").lower()
                width, height = image.size
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise InvalidMedia(["El archivo no es una imagen valida."]) from exc
        return MediaInfo(kind="image", fmt=fmt, width=width, height=height, size=size)
    width, height, duration = probe_video(path)
    return MediaInfo(
        kind="video", fmt=extension.lstrip("."), width=width, height=height, size=size, duration=duration
    )
