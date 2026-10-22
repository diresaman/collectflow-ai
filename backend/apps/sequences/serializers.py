from typing import Any

from django.db.models import Count
from rest_framework import serializers

from . import engine
from .models import Sequence, SequenceEnrollment, SequenceStep


class SequenceStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = SequenceStep
        fields = ["order", "offset_days", "channel", "tone", "requires_approver"]
        read_only_fields = fields


class SequenceSerializer(serializers.ModelSerializer):
    steps = SequenceStepSerializer(many=True, read_only=True)
    enrollment_counts = serializers.SerializerMethodField()

    def get_enrollment_counts(self, sequence: Sequence) -> dict[str, Any]:
        """Live enrollments per current position: pending step, or the step they wait for."""
        live = sequence.enrollments.filter(status__in=SequenceEnrollment.LIVE_STATUSES)
        per_step: dict[str, int] = {}
        for enrollment in live.only("pending_step", "last_completed_step"):
            key = str(enrollment.pending_step or enrollment.next_step)
            per_step[key] = per_step.get(key, 0) + 1
        by_status = dict(sequence.enrollments.values_list("status").annotate(n=Count("id")))
        return {"by_step": per_step, "by_status": by_status}

    class Meta:
        model = Sequence
        fields = ["id", "name", "version", "is_default", "active", "created_at", "steps", "enrollment_counts"]
        read_only_fields = fields


class EnrollmentSerializer(serializers.ModelSerializer):
    invoice_id = serializers.UUIDField(read_only=True)
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True)
    sequence = serializers.SerializerMethodField()
    next_step = serializers.IntegerField(read_only=True)
    steps = serializers.SerializerMethodField()

    def get_sequence(self, enrollment: SequenceEnrollment) -> dict[str, Any]:
        return {"id": str(enrollment.sequence_id), "name": enrollment.sequence.name, "version": enrollment.sequence.version}

    def get_steps(self, enrollment: SequenceEnrollment) -> list[dict[str, Any]]:
        """Each step with its state: done, pending (in progress), upcoming."""
        states = []
        for order, step in sorted(engine.steps_of(enrollment.sequence).items()):
            if order <= enrollment.last_completed_step:
                state = "done"
            elif order == enrollment.pending_step:
                state = "pending"
            else:
                state = "upcoming"
            states.append({"order": order, "channel": step.channel, "tone": step.tone, "offset_days": step.offset_days,
                           "requires_approver": step.requires_approver, "state": state})
        return states

    class Meta:
        model = SequenceEnrollment
        fields = [
            "id", "invoice_id", "invoice_external_id", "sequence", "status", "last_completed_step", "pending_step",
            "next_step", "next_action_at", "escalation_level", "blocked_reason", "pause_reason", "exit_reason",
            "review_required", "review_reason", "history", "steps", "created_at", "updated_at", "completed_at",
        ]
        read_only_fields = fields
