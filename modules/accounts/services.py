import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from modules.core.exceptions import DomainError
from modules.core.roles import Role

from .authentication import validate_session
from .models import UserSession

User = get_user_model()

# Sin caracteres ambiguos (0/O, 1/l/I) para que sea facil dictarla.
_TEMP_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"




# --- Sesion -----------------------------------------------------------------
def _issue_tokens(user, session: UserSession) -> dict:
    refresh = RefreshToken.for_user(user)
    refresh["sid"] = str(session.id)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def login(username: str, password: str) -> tuple:
    """Autentica (RF1). 5 fallos bloquean la cuenta 15 min (RNF01)."""
    now = timezone.now()
    # Los errores se lanzan FUERA del atomic: si no, el rollback perderia el contador de intentos.
    error: DomainError | None = None
    user = session = None
    with transaction.atomic():
        user = User.objects.select_for_update().filter(username=username).first()
        if user is None:
            User().set_password(password)  # iguala el tiempo de respuesta
            error = _invalid_credentials()
        elif user.is_locked():
            error = DomainError(
                "Cuenta bloqueada temporalmente por intentos fallidos.", "account_locked", 423
            )
        elif not user.is_active or not user.check_password(password):
            if user.is_active:
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
                    user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                    user.failed_login_attempts = 0
                user.save(update_fields=["failed_login_attempts", "locked_until"])
            error = _invalid_credentials()
        else:
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login = now
            user.save(update_fields=["failed_login_attempts", "locked_until", "last_login"])
            session = UserSession.objects.create(user=user, last_seen_at=now)
    if error is not None:
        raise error
    return user, _issue_tokens(user, session)


def _invalid_credentials() -> DomainError:
    return DomainError("Usuario o contrasena incorrectos.", "invalid_credentials", 401)


def refresh_tokens(refresh_token: str) -> dict:
    """Renueva el access token y rota el refresh, sin reiniciar el reloj de inactividad."""
    try:
        refresh = RefreshToken(refresh_token)
    except TokenError as exc:
        raise DomainError("Refresh token invalido o expirado.", "invalid_token", 401) from exc
    sid = refresh.get("sid")
    user_id = refresh.get(jwt_settings.USER_ID_CLAIM)
    if not sid or not user_id:
        raise DomainError("Refresh token invalido.", "invalid_token", 401)
    try:
        validate_session(sid, user_id, touch=False)
    except AuthenticationFailed as exc:
        raise DomainError(str(exc.detail), exc.detail.code, 401) from exc
    if not User.objects.filter(pk=user_id, is_active=True).exists():
        raise DomainError("Usuario inactivo.", "invalid_token", 401)
    data = {"access": str(refresh.access_token)}
    if jwt_settings.ROTATE_REFRESH_TOKENS:
        if jwt_settings.BLACKLIST_AFTER_ROTATION:
            try:
                refresh.blacklist()
            except AttributeError:
                pass
        refresh.set_jti()
        refresh.set_exp()
        refresh.set_iat()
        data["refresh"] = str(refresh)
    return data


def logout(session_id, refresh_token: str | None = None) -> None:
    UserSession.objects.filter(pk=session_id, revoked_at__isnull=True).update(revoked_at=timezone.now())
    if refresh_token:
        try:
            RefreshToken(refresh_token).blacklist()
        except TokenError:
            pass


def revoke_all_sessions(user, *, except_session_id=None) -> None:
    qs = UserSession.objects.filter(user=user, revoked_at__isnull=True)
    if except_session_id:
        qs = qs.exclude(pk=except_session_id)
    qs.update(revoked_at=timezone.now())


# --- Contrasenas (RF2) ------------------------------------------------------




# --- Gestion de personal (RF2, RF3) -----------------------------------------








