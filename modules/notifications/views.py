import hashlib
import hmac
import json

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.core.exceptions import DomainError
from modules.core.pagination import StandardPagination
from modules.core.permissions import IsAdmin

from . import services
from .models import Alerta








class WhatsAppWebhookView(APIView):
    """Webhook de la Cloud API: verificacion (GET) y estados de entrega (POST).
    Sin sesion: se autentica con el token de verificacion y la firma HMAC de Meta."""

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        expected = settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN
        params = request.query_params
        if (
            expected
            and params.get("hub.mode") == "subscribe"
            and hmac.compare_digest(params.get("hub.verify_token", ""), expected)
        ):
            return HttpResponse(params.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponse(status=403)

    def post(self, request):
        body = request.body
        secret = settings.WHATSAPP_APP_SECRET
        signature = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else None
        if not expected or not hmac.compare_digest(signature, expected):
            return HttpResponse(status=403)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise DomainError("JSON invalido.", "invalid_json") from exc
        updated = 0
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for status in change.get("value", {}).get("statuses", []):
                    errors = status.get("errors") or [{}]
                    detail = errors[0].get("title") or errors[0].get("message") or ""
                    if services.apply_status_update(status.get("id", ""), status.get("status", ""), detail):
                        updated += 1
        return Response({"updated": updated})
