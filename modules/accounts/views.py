from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.core.permissions import IsAdmin

from . import services
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    RefreshSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
)

User = get_user_model()


# --- Autenticacion ----------------------------------------------------------
class LoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user, tokens = services.login(**data.validated_data, request=request)
        return Response({**tokens, "user": UserSerializer(user).data})


class RefreshView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        data = RefreshSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(services.refresh_tokens(data.validated_data["refresh"]))


class LogoutView(APIView):
    allow_password_change_pending = True

    def post(self, request):
        data = LogoutSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.logout(
            request.session_id, data.validated_data.get("refresh"), user=request.user, request=request
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    allow_password_change_pending = True

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    allow_password_change_pending = True

    def post(self, request):
        data = ChangePasswordSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.change_password(
            request.user,
            data.validated_data["current_password"],
            data.validated_data["new_password"],
            session_id=request.session_id,
            request=request,
        )
        return Response({"detail": "Contrasena actualizada."})


# --- Gestion de personal (solo Administrador) --------------------------------
class UserListCreateView(APIView):
    permission_classes = [*APIView.permission_classes, IsAdmin]

    def get(self, request):
        users = User.objects.order_by("username")
        active = request.query_params.get("is_active")
        if active in ("true", "false"):
            users = users.filter(is_active=active == "true")
        return Response(UserSerializer(users, many=True).data)

    def post(self, request):
        data = UserCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user, temporary = services.create_user(
            **data.validated_data, acting_user=request.user, request=request
        )
        return Response(
            {**UserSerializer(user).data, "temporary_password": temporary},
            status=status.HTTP_201_CREATED,
        )


class UserDetailView(APIView):
    permission_classes = [*APIView.permission_classes, IsAdmin]

    def get(self, request, pk):
        return Response(UserSerializer(get_object_or_404(User, pk=pk)).data)

    def patch(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        data = UserUpdateSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        fields = dict(data.validated_data)
        if "whatsapp_number" in fields:
            fields["whatsapp_number"] = fields["whatsapp_number"] or None
        return Response(
            UserSerializer(
                services.update_user(user, acting_user=request.user, request=request, **fields)
            ).data
        )


class UserDeactivateView(APIView):
    permission_classes = [*APIView.permission_classes, IsAdmin]

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        return Response(
            UserSerializer(services.deactivate_user(user, acting_user=request.user, request=request)).data
        )


class UserActivateView(APIView):
    permission_classes = [*APIView.permission_classes, IsAdmin]

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        return Response(
            UserSerializer(services.activate_user(user, acting_user=request.user, request=request)).data
        )


class UserResetPasswordView(APIView):
    permission_classes = [*APIView.permission_classes, IsAdmin]

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        temporary = services.reset_password(user, acting_user=request.user, request=request)
        return Response({"temporary_password": temporary})
