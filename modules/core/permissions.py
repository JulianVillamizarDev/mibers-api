from rest_framework.permissions import BasePermission

from .roles import Role


class IsAdmin(BasePermission):
    """Solo el Administrador (RF3)."""

    message = "Solo el administrador puede realizar esta accion."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.ADMIN)


class IsStaffOrAdmin(BasePermission):
    """Cualquier usuario interno autenticado (Administrador o Personal)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in (Role.ADMIN, Role.STAFF))


class PasswordChangeNotRequired(BasePermission):
    """RF2: con contrasena temporal solo se puede usar lo marcado con
    `allow_password_change_pending = True` en la vista (cambiar contrasena, /me, logout)."""

    message = "Debes cambiar tu contrasena temporal antes de continuar."
    code = "password_change_required"

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return True  # lo resuelve IsAuthenticated
        if not user.must_change_password:
            return True
        return bool(getattr(view, "allow_password_change_pending", False))
