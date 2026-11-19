from django.contrib.auth import get_user_model
from rest_framework import serializers

from modules.core.roles import Role
from modules.core.validators import validate_e164

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True)




class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "role",
            "cargo",
            "whatsapp_number",
            "is_active",
            "must_change_password",
            "last_login",
            "date_joined",
        ]
        read_only_fields = fields




