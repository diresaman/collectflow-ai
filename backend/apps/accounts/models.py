from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Role(models.TextChoices):
    """Ordered from least to most privileged; each role includes the ones before it."""

    OPERATOR = "OPERATOR", "Operator"
    APPROVER = "APPROVER", "Approver"
    ADMIN = "ADMIN", "Admin"


ROLE_RANK = {Role.OPERATOR: 1, Role.APPROVER: 2, Role.ADMIN: 3}


class Profile(models.Model):
    """CollectFlow role of a Django user. A user without a profile has no access (superusers are ADMIN)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.OPERATOR)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(role__in=["OPERATOR", "APPROVER", "ADMIN"]), name="profile_role_valid"),
        ]

    def __str__(self) -> str:
        return f"{self.user} ({self.role})"


class LoginFailure(models.Model):
    """One failed login, used for rate limiting. Stores the attempted username and nothing else."""

    username = models.CharField(max_length=150, db_index=True)
    occurred_at = models.DateTimeField(default=timezone.now)
