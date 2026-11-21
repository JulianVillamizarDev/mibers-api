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

from modules.audit import services as audit
from modules.audit.models import Action
from modules.core.exceptions import DomainError
from modules.core.roles import Role

from .authentication import validate_session
from .models import UserSession

User = get_user_model()

# Sin caracteres ambiguos (0/O, 1/l/I) para que sea facil dictarla.
_TEMP_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_temporary_password(length: int = 12) -> str:
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))


# --- Sesion -----------------------------------------------------------------
def _issue_tokens(user, session: UserSession) -> dict:
    refresh = RefreshToken.for_user(user)
    refresh["sid"] = str(session.id)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def login(username: str, password: str, request=None) -> tuple:
    """Autentica (RF1). 5 fallos bloquean la cuenta 15 min (RNF01)."""
    now = timezone.now()
    # Los errores se lanzan FUERA del atomic: si no, el rollback perderia el contador de intentos.
    error: DomainError | None = None
    reason = ""
    locked_now = False
    user = session = None
    with transaction.atomic():
        user = User.objects.select_for_update().filter(username=username).first()
        if user is None:
            User().set_password(password)  # iguala el tiempo de respuesta
            error, reason = _invalid_credentials(), "unknown_user"
        elif user.is_locked():
            error = DomainError(
                "Cuenta bloqueada temporalmente por intentos fallidos.", "account_locked", 423
            )
            reason = "locked"
        elif not user.is_active or not user.check_password(password):
            if user.is_active:
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
                    user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                    user.failed_login_attempts = 0
                    locked_now = True
                user.save(update_fields=["failed_login_attempts", "locked_until"])
            error = _invalid_credentials()
            reason = "inactive" if not user.is_active else "bad_password"
        else:
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login = now
            user.save(update_fields=["failed_login_attempts", "locked_until", "last_login"])
            session = UserSession.objects.create(user=user, last_seen_at=now)
    # La auditoria se escribe fuera del atomic para que sobreviva al error lanzado.
    if error is not None:
        audit.record(
            Action.LOGIN_FAILED,
            actor=user,
            actor_username=username,
            metadata={"reason": reason},
            request=request,
        )
        if locked_now:
            audit.record(Action.ACCOUNT_LOCKED, actor=user, request=request)
        raise error
    audit.record(Action.LOGIN, actor=user, entity=session, request=request)
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


def logout(session_id, refresh_token: str | None = None, *, user=None, request=None) -> None:
    UserSession.objects.filter(pk=session_id, revoked_at__isnull=True).update(revoked_at=timezone.now())
    audit.record(Action.LOGOUT, actor=user, request=request)
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
def change_password(user, current_password: str, new_password: str, session_id=None, request=None) -> None:
    if not user.check_password(current_password):
        raise DomainError("La contrasena actual no es correcta.", "wrong_current_password")
    if current_password == new_password:
        raise DomainError("La nueva contrasena debe ser distinta de la actual.", "same_password")
    validate_password(new_password, user)  # lanza ValidationError -> 400
    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    revoke_all_sessions(user, except_session_id=session_id)
    audit.record(Action.PASSWORD_CHANGED, actor=user, entity=user, request=request)


def reset_password(user, *, acting_user=None, request=None) -> str:
    """Genera una contrasena temporal (devuelta una sola vez) y obliga a cambiarla."""
    temporary = generate_temporary_password()
    user.set_password(temporary)
    user.must_change_password = True
    user.failed_login_attempts = 0
    user.locked_until = None
    user.save(update_fields=["password", "must_change_password", "failed_login_attempts", "locked_until"])
    revoke_all_sessions(user)
    audit.record(Action.PASSWORD_RESET, actor=acting_user, entity=user, request=request)
    return temporary


# --- Gestion de personal (RF2, RF3) -----------------------------------------
def _active_admins():
    return User.objects.filter(role=Role.ADMIN, is_active=True)


def create_user(
    *,
    username,
    role=Role.STAFF,
    first_name="",
    last_name="",
    cargo="",
    whatsapp_number=None,
    acting_user=None,
    request=None,
):
    user = User(
        username=username,
        role=role,
        first_name=first_name,
        last_name=last_name,
        cargo=cargo,
        whatsapp_number=whatsapp_number or None,
    )
    temporary = generate_temporary_password()
    user.set_password(temporary)
    user.must_change_password = True
    user.save()
    audit.record(
        Action.USER_CREATED, actor=acting_user, entity=user, metadata={"role": role}, request=request
    )
    return user, temporary


def update_user(user, *, acting_user=None, request=None, **fields):
    new_role = fields.get("role")
    if new_role and new_role != Role.ADMIN and user.role == Role.ADMIN and user.is_active:
        if not _active_admins().exclude(pk=user.pk).exists():
            raise DomainError("Debe quedar al menos un administrador activo.", "last_admin")
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    # No se guardan valores (p. ej. el WhatsApp): solo los campos que cambiaron.
    audit.record(
        Action.USER_UPDATED,
        actor=acting_user,
        entity=user,
        metadata={"fields": sorted(fields)},
        request=request,
    )
    return user


def deactivate_user(user, *, acting_user, request=None):
    """Desactiva sin borrar, para conservar lo que hizo el colaborador (RF2)."""
    if user.pk == acting_user.pk:
        raise DomainError("No puedes desactivar tu propia cuenta.", "cannot_deactivate_self")
    if user.role == Role.ADMIN and not _active_admins().exclude(pk=user.pk).exists():
        raise DomainError("Debe quedar al menos un administrador activo.", "last_admin")
    user.is_active = False
    user.save(update_fields=["is_active"])
    revoke_all_sessions(user)
    audit.record(Action.USER_DEACTIVATED, actor=acting_user, entity=user, request=request)
    return user


def activate_user(user, *, acting_user=None, request=None):
    user.is_active = True
    user.save(update_fields=["is_active"])
    audit.record(Action.USER_ACTIVATED, actor=acting_user, entity=user, request=request)
    return user
