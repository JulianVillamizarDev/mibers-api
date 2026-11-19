from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source="get_action_display", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "created_at",
            "actor",
            "actor_username",
            "action",
            "action_label",
            "entity_type",
            "entity_id",
            "metadata",
            "ip_address",
        ]
        read_only_fields = fields
