from typing import Any

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request

from .models import Role
from .roles import has_role


class _MinimumRole(BasePermission):
    minimum: str = Role.OPERATOR
    message = "Your role does not allow this action."

    def has_permission(self, request: Request, view: Any) -> bool:
        return has_role(request.user, self.minimum)


class IsOperator(_MinimumRole):
    """Default for business endpoints: any user with a CollectFlow role."""

    minimum = Role.OPERATOR


class IsApprover(_MinimumRole):
    minimum = Role.APPROVER


class IsAdmin(_MinimumRole):
    minimum = Role.ADMIN


class ReadOperatorWriteAdmin(BasePermission):
    message = "Only admins can change this."

    def has_permission(self, request: Request, view: Any) -> bool:
        return has_role(request.user, Role.OPERATOR if request.method in SAFE_METHODS else Role.ADMIN)
