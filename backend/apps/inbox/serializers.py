from typing import Any

from rest_framework import serializers

from apps.invoices.models import ChaseHold
from apps.timeline.models import TimelineEvent

from .models import InboundMessage, ReplyClassification, Task

SNIPPET_CHARS = 160


class TaskSerializer(serializers.ModelSerializer):
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True, default=None)
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    step = serializers.IntegerField(source="step.order", read_only=True, default=None)
    done_by = serializers.CharField(source="done_by.username", read_only=True, default=None)

    class Meta:
        model = Task
        fields = ["id", "kind", "title", "detail", "invoice", "invoice_external_id", "client", "client_name", "step",
                  "reply", "due_at", "done_at", "done_by", "created_at"]
        read_only_fields = fields


class HoldSerializer(serializers.ModelSerializer):
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    protected = serializers.BooleanField(read_only=True)
    released_by = serializers.CharField(source="released_by.username", read_only=True, default=None)
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True)

    class Meta:
        model = ChaseHold
        fields = ["id", "invoice", "invoice_external_id", "reason", "reason_display", "protected", "reply_id", "detail",
                  "created_at", "released_at", "released_by", "release_note"]
        read_only_fields = fields


class ReplyListSerializer(serializers.ModelSerializer):
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True)
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    snippet = serializers.SerializerMethodField()
    on_hold = serializers.SerializerMethodField()

    def get_snippet(self, reply: InboundMessage) -> str:
        text = " ".join(reply.body.split())
        return text[:SNIPPET_CHARS] + ("…" if len(text) > SNIPPET_CHARS else "")

    def get_on_hold(self, reply: InboundMessage) -> bool:
        active = getattr(reply, "has_active_hold", None)
        if active is not None:
            return bool(active)
        return ChaseHold.objects.filter(reply_id=reply.pk, released_at__isnull=True).exists()

    class Meta:
        model = InboundMessage
        fields = ["id", "invoice", "invoice_external_id", "client", "client_name", "sender_email", "sender_name",
                  "sender_check", "subject", "snippet", "received_at", "state", "triage_reason", "label", "confidence",
                  "label_source", "on_hold", "gmail_thread_id"]
        read_only_fields = fields


class ClassificationSerializer(serializers.ModelSerializer):
    actor = serializers.CharField(source="actor.username", read_only=True, default=None)

    class Meta:
        model = ReplyClassification
        fields = ["id", "source", "label", "confidence", "extracted", "rationale", "decision", "error_code",
                  "ai_model", "prompt_version", "actor", "note", "created_at"]
        read_only_fields = fields


class ReplyDetailSerializer(ReplyListSerializer):
    classifications = ClassificationSerializer(many=True, read_only=True)
    holds = serializers.SerializerMethodField()
    tasks = TaskSerializer(many=True, read_only=True)
    history = serializers.SerializerMethodField()
    replied_to = serializers.SerializerMethodField()
    gmail_url = serializers.SerializerMethodField()

    def get_holds(self, reply: InboundMessage) -> list[dict[str, Any]]:
        holds = ChaseHold.objects.filter(reply_id=reply.pk).select_related("invoice", "released_by")
        return HoldSerializer(holds, many=True).data  # type: ignore[return-value]

    def get_history(self, reply: InboundMessage) -> list[dict[str, Any]]:
        events = (TimelineEvent.objects.filter(payload__reply_id=str(reply.pk)).select_related("actor", "invoice")
                  .order_by("occurred_at", "id"))
        return [{"id": str(e.pk), "type": e.type, "label": e.get_type_display(), "occurred_at": e.occurred_at,
                 "actor_type": e.actor_type, "actor": e.actor.username if e.actor else None,
                 "invoice_external_id": e.invoice.external_id if e.invoice else None, "payload": e.payload}
                for e in events]

    def get_replied_to(self, reply: InboundMessage) -> dict[str, Any] | None:
        attempt = reply.replied_to
        if attempt is None:
            return None
        return {"follow_up_id": str(attempt.follow_up_id), "subject": attempt.follow_up.subject,
                "sent_at": attempt.completed_at, "step": attempt.follow_up.step.order if attempt.follow_up.step else None}

    def get_gmail_url(self, reply: InboundMessage) -> str:
        return f"https://mail.google.com/mail/u/0/#all/{reply.gmail_thread_id}"

    class Meta(ReplyListSerializer.Meta):
        fields = [*ReplyListSerializer.Meta.fields, "body", "body_truncated", "body_purged_at", "extracted", "rationale",
                  "classify_attempts", "thread_invoice_ids", "resolved_at", "classifications", "holds", "tasks",
                  "history", "replied_to", "gmail_url"]
        read_only_fields = fields
