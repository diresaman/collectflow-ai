from rest_framework import serializers

from apps.invoices.serializers import AutomationRunSerializer

from .models import AutomationRunItem


class AutomationRunItemSerializer(serializers.ModelSerializer):
    invoice_id = serializers.UUIDField(allow_null=True, read_only=True)
    follow_up_id = serializers.UUIDField(allow_null=True, read_only=True)

    class Meta:
        model = AutomationRunItem
        fields = ["id", "invoice_id", "invoice_external_id", "outcome", "reason_code", "detail", "follow_up_id"]
        read_only_fields = fields


class AutomationRunDetailSerializer(AutomationRunSerializer):
    items = AutomationRunItemSerializer(many=True, read_only=True)

    class Meta(AutomationRunSerializer.Meta):
        fields = [*AutomationRunSerializer.Meta.fields, "items"]
        read_only_fields = fields
