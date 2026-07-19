from apps.organizations.permissions import active_membership_for, user_has_organization_permission


PRODUCT_COST_VISIBILITY_PERMISSIONS = (
    "purchasing.view_purchaseorder",
    "organizations.view_retail_analytics",
    "inventory.view_stockmovement",
)


def can_view_product_costs(user, organization):
    if not organization or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or getattr(user, "is_platform_admin", False):
        return True
    membership = active_membership_for(user, organization)
    if membership and membership.is_owner:
        return True
    return any(
        user_has_organization_permission(user, organization, permission)
        for permission in PRODUCT_COST_VISIBILITY_PERMISSIONS
    )
