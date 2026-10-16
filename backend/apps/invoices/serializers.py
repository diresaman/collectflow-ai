from rest_framework import serializers

from .models import AutomationRun, Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "external_id",
            "client_name",
            "client_email",
            "amount",
            "currency",
            "due_date",
            "status",
            "is_overdue",
            "days_overdue",
            "last_synced_at",
        ]
        read_only_fields = fields


class AutomationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationRun
        fields = [
            "id",
            "trigger",
            "status",
            "started_at",
            "completed_at",
            "records_processed",
            "created_count",
            "updated_count",
            "unchanged_count",
            "rejected_count",
            "error_summary",
            "errors",
        ]
        read_only_fields = fields
