from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import UserSession

# No se escribe last_seen_at en cada peticion, solo cuando pasa al menos este tiempo.
TOUCH_INTERVAL = timedelta(seconds=60)


def idle_timeout() -> timedelta:
    return timedelta(minutes=settings.SESSION_IDLE_TIMEOUT_MINUTES)


def validate_session(session_id, user_id, *, touch: bool) -> UserSession:
    """Verifica que la sesion exista, no este revocada y no lleve mas de 30 min inactiva (RNF01)."""
    session = UserSession.objects.filter(pk=session_id, user_id=user_id).first()
    if session is None or session.revoked_at is not None:
        raise AuthenticationFailed("Sesion cerrada. Inicia sesion de nuevo.", code="session_revoked")
    now = timezone.now()
    if now - session.last_seen_at > idle_timeout():
        session.revoked_at = now
        session.save(update_fields=["revoked_at", "updated_at"])
        raise AuthenticationFailed("Sesion expirada por inactividad.", code="session_expired")
    if touch and now - session.last_seen_at >= TOUCH_INTERVAL:
        session.last_seen_at = now
        session.save(update_fields=["last_seen_at", "updated_at"])
    return session


class SessionJWTAuthentication(JWTAuthentication):
    """JWT + control de sesion en servidor (inactividad y cierre de sesion)."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        sid = token.get("sid")
        if not sid:
            raise AuthenticationFailed("Token sin sesion.", code="session_revoked")
        request.session_id = sid
        validate_session(sid, user.pk, touch=True)
        return user, token
