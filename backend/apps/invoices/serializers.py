from rest_framework import serializers

from .models import AutomationRun, Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)
    days_overdue = serializers.IntegerField(read_only=True)
    client_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "external_id",
            "client_id",
            "client_name",
            "client_email",
            "amount",
            "currency",
            "due_date",
            "status",
            "is_overdue",
            "days_overdue",
            "in_sheet",
            "last_synced_at",
        ]
        read_only_fields = fields


class AutomationRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationRun
        fields = [
            "id",
            "kind",
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
            "sync_run",
            "invoices_evaluated",
            "invoices_overdue",
            "drafts_generated",
            "invoices_skipped",
            "generation_failures",
            "finalized_at",
        ]
        read_only_fields = fields
