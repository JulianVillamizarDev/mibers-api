"""Formato uniforme de errores para el SPA: {"code": ..., "detail": ...}."""

from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler


class DomainError(Exception):
    """Error de regla de negocio. Se responde como 400 con un codigo estable."""

    status_code = 400

    def __init__(self, detail: str, code: str = "domain_error", status_code: int | None = None):
        super().__init__(detail)
        self.detail = detail
        self.code = code
        if status_code is not None:
            self.status_code = status_code


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        return Response({"code": exc.code, "detail": exc.detail}, status=exc.status_code)
    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(detail=exc.messages)
    if isinstance(exc, PermissionDenied):
        exc = exceptions.PermissionDenied()
    response = exception_handler(exc, context)
    if response is not None and isinstance(exc, exceptions.APIException):
        code = exc.get_codes()
        if isinstance(response.data, dict) and "detail" in response.data and len(response.data) == 1:
            code = code if isinstance(code, str) else "error"
            response.data = {"code": code, "detail": response.data["detail"]}
        else:
            response.data = {"code": "validation_error", "detail": response.data}
    return response
