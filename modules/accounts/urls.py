from django.urls import path

from . import views

urlpatterns = [
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    path("auth/refresh", views.RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/me", views.MeView.as_view(), name="auth-me"),
    path("auth/change-password", views.ChangePasswordView.as_view(), name="auth-change-password"),
    path("usuarios", views.UserListCreateView.as_view(), name="usuarios"),
    path("usuarios/<uuid:pk>", views.UserDetailView.as_view(), name="usuario-detalle"),
    path("usuarios/<uuid:pk>/desactivar", views.UserDeactivateView.as_view(), name="usuario-desactivar"),
    path("usuarios/<uuid:pk>/activar", views.UserActivateView.as_view(), name="usuario-activar"),
    path(
        "usuarios/<uuid:pk>/restablecer-password",
        views.UserResetPasswordView.as_view(),
        name="usuario-restablecer",
    ),
]
