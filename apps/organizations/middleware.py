from .models import MembershipStatus


class OrganizationContextMiddleware:
    session_key = "active_organization_id"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.organization = None
        request.membership = None

        if request.user.is_authenticated:
            memberships = request.user.memberships.select_related("organization").prefetch_related(
                "roles__permissions__content_type",
                "branches",
            ).filter(status=MembershipStatus.ACTIVE, organization__status="active")
            requested_id = request.session.get(self.session_key)
            membership = memberships.filter(organization_id=requested_id).first()
            membership = membership or memberships.first()

            if membership:
                request.organization = membership.organization
                request.membership = membership
                request.user._organization_permission_cache_enabled = True
                request.user._organization_membership_cache = {
                    membership.organization_id: membership,
                }
                request.session[self.session_key] = str(membership.organization_id)

        return self.get_response(request)
