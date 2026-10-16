from rest_framework import serializers

from .models import FollowUp


class FollowUpSerializer(serializers.ModelSerializer):
    invoice_id = serializers.UUIDField(source="invoice.id", read_only=True)
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True)
    client_name = serializers.CharField(source="invoice.client_name", read_only=True)

    class Meta:
        model = FollowUp
        fields = [
            "id",
            "invoice_id",
            "invoice_external_id",
            "client_name",
            "subject",
            "body",
            "tone",
            "reasoning",
            "recommended_action",
            "status",
            "invoice_snapshot",
            "ai_model",
            "generated_at",
            "approved_at",
            "sent_at",
            "updated_at",
        ]
        read_only_fields = fields
