from rest_framework import serializers

from .models import Task


class TaskSerializer(serializers.ModelSerializer):
    invoice_external_id = serializers.CharField(source="invoice.external_id", read_only=True, default=None)
    client_name = serializers.CharField(source="client.display_name", read_only=True)
    step = serializers.IntegerField(source="step.order", read_only=True, default=None)
    done_by = serializers.CharField(source="done_by.username", read_only=True, default=None)

    class Meta:
        model = Task
        fields = ["id", "kind", "title", "invoice", "invoice_external_id", "client", "client_name", "step",
                  "due_at", "done_at", "done_by", "created_at"]
        read_only_fields = fields
