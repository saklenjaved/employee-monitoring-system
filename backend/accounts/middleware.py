from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect

from .models import Membership, MembershipStatus, Role


class MembershipMiddleware:
    """Find the organization membership of the signed-in user, and sign out anyone without one.

    A non-Super-Admin user may only work in the organization of their active membership.
    If that membership is gone (removed or transferred), the next request signs them out.
    The Super Admin has no membership and is not affected.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.membership = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.role != Role.SUPER_ADMIN:
            request.membership = (
                Membership.objects.filter(user=user, status=MembershipStatus.ACTIVE)
                .select_related("organization")
                .first()
            )
            if request.membership is None:
                logout(request)
                return redirect(settings.LOGIN_URL)
        return self.get_response(request)
