from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.organizations.forms import TENANT_ROLE_PERMISSION_CODES
from apps.organizations.models import Membership, Role

from .models import ApprovalRequest, ApprovalStatus


@transaction.atomic
def request_permission_access(*, organization, user, permission_code, reason):
    if permission_code not in TENANT_ROLE_PERMISSION_CODES:
        raise ValidationError("This access cannot be delegated through a tenant role.")
    permission = Permission.objects.get(
        content_type__app_label=permission_code.split(".", 1)[0],
        codename=permission_code.split(".", 1)[1],
    )
    target_id = f"{user.id}:{permission_code}"
    approval, _ = ApprovalRequest.objects.update_or_create(
        organization=organization,
        request_type="access_request",
        target_type="permission",
        target_id=target_id,
        defaults={
            "reason": reason or f"Request access to {permission.name}.",
            "requested_by": user,
            "status": ApprovalStatus.PENDING,
            "decided_by": None,
            "decided_at": None,
        },
    )
    return approval


@transaction.atomic
def grant_requested_access(*, approval):
    if approval.request_type != "access_request" or approval.target_type != "permission":
        return None
    _, permission_code = approval.target_id.split(":", maxsplit=1)
    if permission_code not in TENANT_ROLE_PERMISSION_CODES:
        raise ValidationError("This permission cannot be delegated through a tenant role.")
    app_label, codename = permission_code.split(".", maxsplit=1)
    permission = Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )
    membership = Membership.objects.select_for_update().get(
        organization=approval.organization,
        user=approval.requested_by,
    )
    role, _ = Role.objects.get_or_create(
        organization=approval.organization,
        code=f"approved-access-{approval.requested_by_id}",
        defaults={
            "name": f"Approved access: {approval.requested_by.get_username()}",
            "description": "Managed role containing individually approved access requests.",
        },
    )
    role.permissions.add(permission)
    membership.roles.add(role)
    return role
