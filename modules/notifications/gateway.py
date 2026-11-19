"""Puerto de WhatsApp. Solo se usa la WhatsApp Cloud API oficial de Meta (RNF10), con el numero
de la agencia: no requiere ser Tech Provider, pero si plantillas aprobadas en Meta Business.

Contrato de las plantillas (body con parametros de texto):
- pieza_cliente_imagen / _video / _documento: cabecera multimedia + body con 1 parametro ({{1}} = mensaje).
- alerta_director y recordatorio_publicacion: solo body con 1 parametro ({{1}} = texto).
"""

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

MB = 1024 * 1024
IMAGE_MAX_BYTES = 5 * MB
VIDEO_MAX_BYTES = 16 * MB
DOCUMENT_MAX_BYTES = 100 * MB
PARAM_MAX_CHARS = 1000

# Errores de la Cloud API que conviene reintentar (limites de tasa y fallos transitorios).
RETRYABLE_CODES = {1, 2, 4, 17, 80007, 130429, 131056, 133004, 133016}


class WhatsAppError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code


@dataclass(frozen=True)
class HeaderMedia:
    path: str
    kind: str  # image | video | document
    filename: str
    mime: str
    size: int


@dataclass(frozen=True)
class SendResult:
    message_id: str


def sanitize_param(text: str) -> str:
    """WhatsApp no admite saltos de linea, tabulaciones ni 4+ espacios seguidos en parametros."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) > PARAM_MAX_CHARS:
        text = text[: PARAM_MAX_CHARS - 1].rstrip() + "…"
    return text or "-"


def build_header(path: str, filename: str, kind: str) -> HeaderMedia:
    """Elige como enviar el archivo segun los limites de WhatsApp: una imagen de mas de 5 MB o un
    video de mas de 16 MB viajan como documento (hasta 100 MB)."""
    size = Path(path).stat().st_size
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if kind == "image" and size <= IMAGE_MAX_BYTES:
        header_kind = "image"
    elif kind == "video" and size <= VIDEO_MAX_BYTES:
        header_kind = "video"
    elif size <= DOCUMENT_MAX_BYTES:
        header_kind = "document"
    else:
        raise WhatsAppError(
            f"El archivo pesa {size // MB} MB y supera el limite de WhatsApp (100 MB).", retryable=False
        )
    return HeaderMedia(path=path, kind=header_kind, filename=filename, mime=mime, size=size)


def template_for_header(kind: str) -> str:
    return {
        "image": settings.WHATSAPP_TEMPLATE_CLIENT_IMAGE,
        "video": settings.WHATSAPP_TEMPLATE_CLIENT_VIDEO,
        "document": settings.WHATSAPP_TEMPLATE_CLIENT_DOCUMENT,
    }[kind]


class FakeGateway:
    """Adaptador simulado para desarrollo y pruebas: registra el envio y devuelve un id."""

    sent: list[dict] = []
    fail_with: WhatsAppError | None = None

    @classmethod
    def reset(cls):
        cls.sent = []
        cls.fail_with = None

    def send_template(self, *, to, template, body_params, header=None) -> SendResult:
        if FakeGateway.fail_with is not None:
            error, FakeGateway.fail_with = FakeGateway.fail_with, None
            raise error
        message_id = f"wamid.FAKE{len(FakeGateway.sent) + 1:05d}"
        FakeGateway.sent.append(
            {"to": to, "template": template, "body_params": body_params, "header": header, "id": message_id}
        )
        return SendResult(message_id=message_id)


class CloudApiGateway:
    """WhatsApp Cloud API: sube el archivo (si hay) y envia una plantilla."""

    def __init__(
        self,
        *,
        token: str,
        phone_number_id: str,
        version: str = "v21.0",
        language: str = "es",
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://graph.facebook.com",
    ):
        self.phone_number_id = phone_number_id
        self.language = language
        self.base = f"{base_url}/{version}/{phone_number_id}"
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"}, timeout=60, transport=transport
        )

    # --- helpers ---
    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = self.client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise WhatsAppError(f"Error de red con WhatsApp: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise self._error(response)
        return response.json()

    @staticmethod
    def _error(response: httpx.Response) -> WhatsAppError:
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        code = error.get("code")
        details = (error.get("error_data") or {}).get("details", "")
        message = (
            f"WhatsApp {response.status_code}: {error.get('message', response.text[:200])} {details}".strip()
        )
        retryable = response.status_code >= 500 or response.status_code == 429 or code in RETRYABLE_CODES
        return WhatsAppError(message, retryable=retryable, code=code)

    def _upload(self, header: HeaderMedia) -> str:
        with open(header.path, "rb") as handle:
            data = self._request(
                "POST",
                f"{self.base}/media",
                data={"messaging_product": "whatsapp", "type": header.mime},
                files={"file": (header.filename, handle, header.mime)},
            )
        return data["id"]

    # --- API del puerto ---
    def send_template(self, *, to, template, body_params, header=None) -> SendResult:
        components = []
        if header is not None:
            media = {"id": self._upload(header)}
            if header.kind == "document":
                media["filename"] = header.filename
            components.append({"type": "header", "parameters": [{"type": header.kind, header.kind: media}]})
        components.append(
            {"type": "body", "parameters": [{"type": "text", "text": sanitize_param(p)} for p in body_params]}
        )
        payload = {
            "messaging_product": "whatsapp",
            "to": to.lstrip("+"),
            "type": "template",
            "template": {"name": template, "language": {"code": self.language}, "components": components},
        }
        data = self._request("POST", f"{self.base}/messages", json=payload)
        try:
            return SendResult(message_id=data["messages"][0]["id"])
        except (KeyError, IndexError) as exc:
            raise WhatsAppError("Respuesta inesperada de WhatsApp.", retryable=False) from exc


def get_gateway():
    backend = settings.WHATSAPP_BACKEND
    if backend == "fake":
        return FakeGateway()
    if backend == "cloud":
        if not settings.WHATSAPP_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
            raise ImproperlyConfigured("Faltan WHATSAPP_TOKEN y WHATSAPP_PHONE_NUMBER_ID.")
        return CloudApiGateway(
            token=settings.WHATSAPP_TOKEN,
            phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
            version=settings.WHATSAPP_GRAPH_VERSION,
            language=settings.WHATSAPP_LANGUAGE,
        )
    raise ImproperlyConfigured(f"WHATSAPP_BACKEND invalido: {backend!r} (usa 'fake' o 'cloud').")
