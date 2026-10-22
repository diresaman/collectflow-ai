from typing import Any

from rest_framework import serializers

from . import promises
from .models import DisputeCase, DisputeNote, PromiseToPay


def _user(user: Any) -> dict[str, Any] | None:
    return {"id": user.pk, "username": user.username, "display_name": user.get_full_name() or user.username} if user else None


class PromiseSerializer(serializers.ModelSerializer):
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True)
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    owner = serializers.SerializerMethodField()
    resolved_by = serializers.CharField(source="resolved_by.username", read_only=True, default=None)
    grace_until = serializers.SerializerMethodField()

    def get_owner(self, promise: PromiseToPay) -> dict[str, Any] | None:
        return _user(promise.owner)

    def get_grace_until(self, promise: PromiseToPay) -> str:
        return promises.deadline(promise).isoformat()

    class Meta:
        model = PromiseToPay
        fields = ["id", "invoice", "invoice_external_id", "client", "client_name", "reply", "promised_date", "amount",
                  "currency", "status", "note", "owner", "grace_until", "created_at", "resolved_at", "resolved_by",
                  "resolution_note", "paid_seen_at"]
        read_only_fields = fields


class DisputeNoteSerializer(serializers.ModelSerializer):
    author = serializers.CharField(source="author.username", read_only=True)

    class Meta:
        model = DisputeNote
        fields = ["id", "author", "text", "created_at"]
        read_only_fields = fields


class DisputeSerializer(serializers.ModelSerializer):
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True)
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    reason_display = serializers.CharField(source="get_reason_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    owner = serializers.SerializerMethodField()
    opened_by = serializers.CharField(source="opened_by.username", read_only=True)
    resolved_by = serializers.CharField(source="resolved_by.username", read_only=True, default=None)
    notes = DisputeNoteSerializer(many=True, read_only=True)

    def get_owner(self, case: DisputeCase) -> dict[str, Any] | None:
        return _user(case.owner)

    class Meta:
        model = DisputeCase
        fields = ["id", "invoice", "invoice_external_id", "client", "client_name", "reply", "reason", "reason_display",
                  "description", "status", "status_display", "owner", "opened_by", "created_at", "resolved_at",
                  "resolved_by", "resolution", "notes"]
        read_only_fields = fields
