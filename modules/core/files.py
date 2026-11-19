"""Almacenamiento en /media. Los archivos NUNCA se publican por URL: se sirven con
un endpoint autenticado (ver `serve_file`)."""

import mimetypes
import uuid
from pathlib import Path

from django.http import FileResponse, Http404


def upload_path(*parts: str, filename: str) -> str:
    """Ruta relativa con nombre aleatorio; conserva solo la extension del original."""
    suffix = Path(filename).suffix.lower()
    return "/".join([*parts, f"{uuid.uuid4().hex}{suffix}"])


def serve_file(fieldfile, download_name: str | None = None) -> FileResponse:
    if not fieldfile:
        raise Http404("Archivo no encontrado")
    try:
        handle = fieldfile.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Archivo no encontrado") from exc
    name = download_name or Path(fieldfile.name).name
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    response = FileResponse(handle, content_type=content_type, as_attachment=False, filename=name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
