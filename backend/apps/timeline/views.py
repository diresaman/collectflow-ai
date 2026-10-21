import uuid

from django.db.models import QuerySet
from rest_framework import generics, serializers
from rest_framework.pagination import LimitOffsetPagination

from .models import TimelineEvent


class TimelineEventSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True, default=None)
    type_label = serializers.CharField(source="get_type_display", read_only=True)

    class Meta:
        model = TimelineEvent
        fields = ["id", "invoice", "client", "actor_type", "actor_username", "type", "type_label", "payload", "occurred_at"]
        read_only_fields = fields


class TimelinePagination(LimitOffsetPagination):
    default_limit = 50
    max_limit = 200


class TimelineView(generics.ListAPIView):
    """GET /api/timeline/?invoice=<uuid>&client=<uuid>, newest first."""

    serializer_class = TimelineEventSerializer
    pagination_class = TimelinePagination

    def get_queryset(self) -> QuerySet[TimelineEvent]:
        queryset = TimelineEvent.objects.select_related("actor")
        for name in ("invoice", "client"):
            value = self.request.query_params.get(name)
            if value:
                try:
                    queryset = queryset.filter(**{f"{name}_id": uuid.UUID(value)})
                except ValueError:
                    return queryset.none()
        return queryset
