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


class UserCreateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    role = serializers.ChoiceField(choices=Role.choices, default=Role.STAFF)
    cargo = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    whatsapp_number = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, default=None, validators=[validate_e164]
    )

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Ya existe un usuario con ese nombre.")
        return value


class UserUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=Role.choices, required=False)
    cargo = serializers.CharField(max_length=100, required=False, allow_blank=True)
    whatsapp_number = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, validators=[validate_e164]
    )
