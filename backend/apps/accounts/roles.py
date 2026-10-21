from typing import Any

from .models import ROLE_RANK, Profile, Role


def role_of(user: Any) -> str | None:
    """The user's CollectFlow role, or None if they have none (no access)."""
    if user is None or not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        return None
    try:
        return user.profile.role
    except Profile.DoesNotExist:
        return Role.ADMIN if user.is_superuser else None


def has_role(user: Any, minimum: str) -> bool:
    role = role_of(user)
    return role is not None and ROLE_RANK[role] >= ROLE_RANK[minimum]


def describe(user: Any) -> dict[str, Any]:
    return {
        "id": user.pk,
        "username": user.username,
        "display_name": user.get_full_name() or user.username,
        "email": user.email,
        "role": role_of(user),
    }
