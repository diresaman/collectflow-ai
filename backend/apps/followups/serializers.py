from typing import Any

from rest_framework import serializers

from apps.accounts import approval

from .eligibility import changed_snapshot_fields
from .models import DeliveryAttempt, FollowUp


class FollowUpSerializer(serializers.ModelSerializer):
    invoice_id = serializers.UUIDField(source="invoice.id", read_only=True)
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True)
    client_name = serializers.CharField(source="invoice.client_name", read_only=True)
    # Delivery always uses the email captured when the draft was generated.
    recipient_email = serializers.SerializerMethodField()

    step = serializers.SerializerMethodField()
    threaded = serializers.SerializerMethodField()
    approval = serializers.SerializerMethodField()
    last_edited_by = serializers.CharField(source="last_edited_by.username", read_only=True, default=None)
    approved_by = serializers.CharField(source="approved_by.username", read_only=True, default=None)

    # Material invoice changes since generation (DRAFT/APPROVED only): the reminder needs review.
    invoice_changed_fields = serializers.SerializerMethodField()

    def get_step(self, follow_up: FollowUp) -> dict[str, Any] | None:
        step = follow_up.step
        if step is None:
            return None
        return {"order": step.order, "channel": step.channel, "tone": step.tone,
                "requires_approver": step.requires_approver}

    def get_threaded(self, follow_up: FollowUp) -> bool:
        return follow_up.thread_reply_to_id is not None

    def get_approval(self, follow_up: FollowUp) -> dict[str, Any]:
        """Who may approve, why, and (for the signed-in user) why they can't, so the UI can explain."""
        need = approval.requirement(follow_up)
        request = self.context.get("request")
        blocked = None
        if request is not None and follow_up.status == FollowUp.Status.DRAFT:
            refusal = approval.refusal(follow_up, request.user)
            if refusal is not None:
                blocked = {"code": refusal[0], "detail": refusal[1]}
        return {"required_role": need.role, "reasons": list(need.reasons), "blocked_for_you": blocked}

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
            "prompt_version",
            "step",
            "threaded",
            "approval",
            "last_edited_by",
            "approved_by",
            "generated_at",
            "approved_at",
            "sent_at",
            "updated_at",
        ]
        read_only_fields = fields


class DeliveryAttemptSerializer(serializers.ModelSerializer):
    requested_by = serializers.CharField(source="requested_by.username", read_only=True, default=None)

    class Meta:
        model = DeliveryAttempt
        fields = [
            "id",
            "follow_up",
            "status",
            "recipient_email",
            "requested_by",
            "started_at",
            "completed_at",
            "gmail_message_id",
            "gmail_thread_id",
            "error_code",
            "error_summary",
        ]
        read_only_fields = fields
