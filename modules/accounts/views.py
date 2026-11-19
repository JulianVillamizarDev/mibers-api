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




# --- Gestion de personal (solo Administrador) --------------------------------








