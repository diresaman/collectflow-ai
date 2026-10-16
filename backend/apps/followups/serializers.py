from rest_framework import serializers

from .eligibility import changed_snapshot_fields

from .models import DeliveryAttempt, FollowUp


class FollowUpSerializer(serializers.ModelSerializer):
    invoice_id = serializers.UUIDField(source="invoice.id", read_only=True)
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True)
    client_name = serializers.CharField(source="invoice.client_name", read_only=True)
    # Delivery always uses the email captured when the draft was generated.
    recipient_email = serializers.SerializerMethodField()

    # Material invoice changes since generation (DRAFT/APPROVED only): the reminder needs review.
    invoice_changed_fields = serializers.SerializerMethodField()

    def get_recipient_email(self, follow_up: FollowUp) -> str:
        return str(follow_up.invoice_snapshot.get("client_email", ""))

    def get_invoice_changed_fields(self, follow_up: FollowUp) -> list[str]:
        if follow_up.status not in (FollowUp.Status.DRAFT, FollowUp.Status.APPROVED):
            return []
        return changed_snapshot_fields(follow_up.invoice_snapshot, follow_up.invoice)

    class Meta:
        model = FollowUp
        fields = [
            "id",
            "invoice_id",
            "invoice_external_id",
            "client_name",
            "recipient_email",
            "invoice_changed_fields",
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


class DeliveryAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryAttempt
        fields = [
            "id",
            "follow_up",
            "status",
            "recipient_email",
            "started_at",
            "completed_at",
            "gmail_message_id",
            "gmail_thread_id",
            "error_code",
            "error_summary",
        ]
        read_only_fields = fields
