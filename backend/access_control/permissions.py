from functools import wraps

from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Protect a view. The check runs on the server for every request.

    - Not logged in: send to the login page.
    - Account belongs to a deactivated organization: log out and send to login.
    - Role not in `roles`: 403 Forbidden. With no roles given, any logged-in role passes.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if user.organization_id and not user.organization.is_active:
                logout(request)
                return redirect_to_login(request.get_full_path())
            if roles and user.role not in roles:
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
