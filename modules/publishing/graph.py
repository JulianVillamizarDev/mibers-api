"""Cliente HTTP de la Graph API de Meta (ADR0007).

El token vive solo en variables de entorno. Cuando hay `META_APP_SECRET` se envia
`appsecret_proof`, como recomienda Meta para llamadas de servidor.
"""

import hashlib
import hmac

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

GRAPH_BASE_URL = "https://graph.facebook.com"
RETRYABLE_CODES = {1, 2, 4, 17, 32, 613, 9007, 80007, 130429, 131056, 133004, 133016}
RETRYABLE_SUBCODES = {2207027}  # media no lista para publicar


class GraphError(Exception):
    """Error devuelto por Meta o por la red, con datos utiles para decidir un reintento."""

    def __init__(self, message: str, *, code=None, subcode=None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.retryable = retryable


class GraphClient:
    """Cliente minimo de Graph API: GET y POST con token y `appsecret_proof`."""

    def __init__(
        self,
        *,
        token: str,
        app_secret: str = "",
        version: str = "v25.0",
        transport: httpx.BaseTransport | None = None,
        base_url: str = GRAPH_BASE_URL,
    ):
        self.token = token
        self.app_secret = app_secret
        self.base = f"{base_url.rstrip('/')}/{version}"
        self.client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"}, timeout=60, transport=transport
        )

    def _with_auth(self, values: dict | None, token: str | None = None) -> dict:
        params = dict(values or {})
        access_token = token or self.token
        if self.app_secret:
            params["appsecret_proof"] = hmac.new(
                self.app_secret.encode(), access_token.encode(), hashlib.sha256
            ).hexdigest()
        return params

    def close(self) -> None:
        self.client.close()

    def get(self, path: str, params: dict | None = None, *, token: str | None = None) -> dict:
        return self._request("GET", path, params=self._with_auth(params, token), token=token)

    def post(self, path: str, data: dict | None = None, *, token: str | None = None) -> dict:
        return self._request("POST", path, data=self._with_auth(data, token), token=token)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        token: str | None = None,
    ) -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        try:
            response = self.client.request(
                method, f"{self.base}/{path.lstrip('/')}", params=params, data=data, headers=headers
            )
        except httpx.TransportError as exc:
            raise GraphError(f"Error de red con Meta: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            raise self._error(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise GraphError("Meta devolvio una respuesta que no es JSON.", retryable=False) from exc
        if not isinstance(payload, dict):
            raise GraphError("Meta devolvio una respuesta inesperada.", retryable=False)
        return payload

    @staticmethod
    def _error(response: httpx.Response) -> GraphError:
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        code = error.get("code")
        subcode = error.get("error_subcode")
        details = (error.get("error_data") or {}).get("details", "")
        message = (
            f"Meta {response.status_code}: {error.get('message', 'error desconocido')} {details}".strip()
        )
        retryable = (
            response.status_code >= 500
            or response.status_code == 429
            or (isinstance(code, int) and code in RETRYABLE_CODES)
            or (isinstance(subcode, int) and subcode in RETRYABLE_SUBCODES)
        )
        return GraphError(
            message,
            code=code,
            subcode=subcode,
            retryable=retryable,
        )


class FakeGraphClient:
    """Adaptador de pruebas: registra las llamadas y devuelve respuestas en orden."""

    def __init__(self, responses: list | None = None):
        self.responses = list(responses or [])
        self.calls: list[dict] = []

    def close(self) -> None:
        return None

    def get(self, path: str, params: dict | None = None, *, token: str | None = None) -> dict:
        return self._respond("GET", path, params=params, token=token)

    def post(self, path: str, data: dict | None = None, *, token: str | None = None) -> dict:
        return self._respond("POST", path, data=data, token=token)

    def _respond(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        data: dict | None = None,
        token: str | None = None,
    ) -> dict:
        call = {
            "method": method,
            "path": path,
            "params": dict(params or {}),
            "data": dict(data or {}),
        }
        if token is not None:
            call["token"] = token
        self.calls.append(call)
        if not self.responses:
            return {"id": f"fake-{len(self.calls)}"}
        result = self.responses.pop(0)
        if callable(result):
            result = result(method, path, params or {}, data or {})
        if isinstance(result, Exception):
            raise result
        return result


def get_graph_client():
    backend = settings.META_PUBLISHING
    if backend == "fake":
        return FakeGraphClient()
    if backend != "graph":
        raise ImproperlyConfigured(f"META_PUBLISHING={backend!r}: usa 'manual', 'graph' o 'fake'.")
    if not settings.META_SYSTEM_USER_TOKEN:
        raise ImproperlyConfigured("Falta META_SYSTEM_USER_TOKEN para publicar con la Graph API.")
    return GraphClient(
        token=settings.META_SYSTEM_USER_TOKEN,
        app_secret=settings.META_APP_SECRET,
        version=settings.META_GRAPH_VERSION,
    )
